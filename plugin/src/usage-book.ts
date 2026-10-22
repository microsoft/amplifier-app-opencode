import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs"
import path from "node:path"
import { log } from "./face.ts"
import type { Envelope } from "./turns.ts"

/** What one provider and model used. A value Amplifier did not report is null. */
export interface UsageRow {
  readonly provider: string
  readonly model: string
  readonly tokensIn: number | null
  readonly tokensOut: number | null
  readonly cacheRead: number | null
  readonly cacheWrite: number | null
  readonly costUSD: number | null
}

/** The usage the TUI shows for an opencode session. */
export interface SessionUsage {
  readonly sessionID: string
  readonly turn: UsageRow[]
  readonly session: UsageRow[]
  readonly compactions: number
}

type Json = Record<string, unknown>

function record(value: unknown): Json {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Json) : {}
}

function count(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null
}

function usd(value: unknown): number | null {
  const amount = record(value).USD
  return typeof amount === "string" && amount.trim() !== "" ? count(Number(amount)) : null
}

/** The rows of a `usage` snapshot. */
export function usageRows(snapshot: unknown): UsageRow[] {
  const entries = record(snapshot).entries
  return (Array.isArray(entries) ? entries : []).map((item) => {
    const entry = record(item)
    return {
      provider: typeof entry.provider === "string" ? entry.provider : "unknown",
      model: typeof entry.model === "string" ? entry.model : "unknown",
      tokensIn: count(entry.tokens_in),
      tokensOut: count(entry.tokens_out),
      cacheRead: count(entry.cache_read_tokens),
      cacheWrite: count(entry.cache_write_tokens),
      costUSD: usd(entry.cost),
    }
  })
}

const plus = (a: number | null, b: number | null) => (a === null || b === null ? null : a + b)

/** Rows summed by provider and model; a value unknown in either stays unknown. */
export function addRows(a: readonly UsageRow[], b: readonly UsageRow[]): UsageRow[] {
  const rows = new Map<string, UsageRow>()
  for (const row of [...a, ...b]) {
    const key = `${row.provider}/${row.model}`
    const seen = rows.get(key)
    rows.set(
      key,
      seen
        ? {
            ...seen,
            tokensIn: plus(seen.tokensIn, row.tokensIn),
            tokensOut: plus(seen.tokensOut, row.tokensOut),
            cacheRead: plus(seen.cacheRead, row.cacheRead),
            cacheWrite: plus(seen.cacheWrite, row.cacheWrite),
            costUSD: plus(seen.costUSD, row.costUSD),
          }
        : row,
    )
  }
  return [...rows.values()]
}

/**
 * A turn's rows from a `usage` snapshot taken while the turn runs. Amplifier reports a value it does not yet know for
 * every request as null, so each null keeps the value the turn's previous row for that provider and model had, and is
 * 0 when there was none. The `terminal` event's usage replaces these rows, so a value never reported ends up null.
 */
function runningRows(previous: readonly UsageRow[], snapshot: readonly UsageRow[]): UsageRow[] {
  const known = new Map(previous.map((row) => [`${row.provider}/${row.model}`, row]))
  return snapshot.map((row) => {
    const last = known.get(`${row.provider}/${row.model}`)
    return {
      ...row,
      tokensIn: row.tokensIn ?? last?.tokensIn ?? 0,
      tokensOut: row.tokensOut ?? last?.tokensOut ?? 0,
      cacheRead: row.cacheRead ?? last?.cacheRead ?? 0,
      cacheWrite: row.cacheWrite ?? last?.cacheWrite ?? 0,
      costUSD: row.costUSD ?? last?.costUSD ?? 0,
    }
  })
}

interface Book {
  done: UsageRow[]
  turn: UsageRow[]
  running: boolean
  compactions: number
}

/** Rows as UsageBook saves them. */
function savedRows(value: unknown): UsageRow[] {
  return (Array.isArray(value) ? value : []).map((item) => {
    const row = record(item)
    return {
      provider: typeof row.provider === "string" ? row.provider : "unknown",
      model: typeof row.model === "string" ? row.model : "unknown",
      tokensIn: count(row.tokensIn),
      tokensOut: count(row.tokensOut),
      cacheRead: count(row.cacheRead),
      cacheWrite: count(row.cacheWrite),
      costUSD: count(row.costUSD),
    }
  })
}

/**
 * The usage of each opencode session's turns, from their events. With a directory, each session's usage is saved to
 * `<directory>/<session>.json` when a turn ends and read back the first time the session is asked for.
 */
export class UsageBook {
  private readonly books = new Map<string, Book>()

  constructor(private readonly directory?: string) {}

  private file(sessionID: string): string | undefined {
    return this.directory && /^[\w-]+$/.test(sessionID) ? path.join(this.directory, `${sessionID}.json`) : undefined
  }

  private load(sessionID: string): Book | undefined {
    const known = this.books.get(sessionID)
    if (known) return known
    const file = this.file(sessionID)
    if (!file) return undefined
    let saved: Record<string, unknown>
    try {
      saved = record(JSON.parse(readFileSync(file, "utf8")))
    } catch {
      return undefined
    }
    const compactions = count(saved.compactions) ?? 0
    const book = { done: savedRows(saved.done), turn: savedRows(saved.turn), running: false, compactions }
    this.books.set(sessionID, book)
    return book
  }

  private save(sessionID: string, book: Book): void {
    const file = this.file(sessionID)
    if (!file) return
    try {
      mkdirSync(path.dirname(file), { recursive: true })
      const { done, turn, compactions } = book
      writeFileSync(`${file}.tmp`, JSON.stringify({ done, turn, compactions }))
      renameSync(`${file}.tmp`, file)
    } catch (error) {
      log(`could not save the Amplifier usage of ${sessionID}: ${(error as Error).message}`)
    }
  }

  get(sessionID: string): SessionUsage {
    const book = this.load(sessionID)
    if (!book) return { sessionID, turn: [], session: [], compactions: 0 }
    return {
      sessionID,
      turn: book.turn,
      session: book.running ? addRows(book.done, book.turn) : book.done,
      compactions: book.compactions,
    }
  }

  /** Record one turn event; returns the session's usage when the event changed it. */
  observe(sessionID: string, envelope: Envelope): SessionUsage | undefined {
    let book = this.load(sessionID)
    if (!book) {
      book = { done: [], turn: [], running: false, compactions: 0 }
      this.books.set(sessionID, book)
    }
    const payload = envelope.payload
    switch (envelope.type) {
      case "turn_started":
        book.turn = []
        book.running = true
        return this.get(sessionID)
      case "usage":
        book.turn = runningRows(book.turn, usageRows(payload.snapshot))
        return this.get(sessionID)
      case "progress":
        if (record(record(payload.data).context).compacted !== true) return undefined
        book.compactions++
        return this.get(sessionID)
      case "terminal":
        if (payload.usage) book.turn = usageRows(payload.usage)
        if (book.running) book.done = addRows(book.done, book.turn)
        book.running = false
        this.save(sessionID, book)
        return this.get(sessionID)
      default:
        return undefined
    }
  }
}

const NUMBER = { type: ["number", "null"] }
const ROW = {
  type: "object",
  properties: {
    provider: { type: "string" },
    model: { type: "string" },
    tokensIn: NUMBER,
    tokensOut: NUMBER,
    cacheRead: NUMBER,
    cacheWrite: NUMBER,
    costUSD: NUMBER,
  },
  required: ["provider", "model", "tokensIn", "tokensOut", "cacheRead", "cacheWrite", "costUSD"],
}
const USAGE = {
  type: "object",
  properties: {
    sessionID: { type: "string" },
    turn: { type: "array", items: ROW },
    session: { type: "array", items: ROW },
    compactions: { type: "number" },
  },
  required: ["sessionID", "turn", "session", "compactions"],
} as const

/** The plugin's RPC: the usage of a session, and an event each time it changes. The TUI part repeats this id. */
export const USAGE_RPC = {
  id: "amplifier",
  methods: {
    usage: {
      input: { type: "object", properties: { sessionID: { type: "string" } }, required: ["sessionID"] },
      output: USAGE,
    },
  },
  events: { usage: { schema: USAGE } },
} as const
