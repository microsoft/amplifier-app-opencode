import { expect, test } from "bun:test"
import { mkdtempSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import path from "node:path"
import { UsageBook } from "../src/usage-book.ts"
import type { Envelope } from "../src/turns.ts"

const usage = { entries: [{ provider: "openai", model: "gpt", tokens_in: 10, tokens_out: 2, cost: { USD: "0.5" } }] }
const turn: Envelope[] = [
  { type: "turn_started", payload: {} },
  { type: "terminal", payload: { usage } },
]

test("a session's usage is saved when a turn ends and read back by a new book", () => {
  const directory = mkdtempSync(path.join(tmpdir(), "usage-book-"))
  try {
    const first = new UsageBook(directory)
    for (const envelope of turn) first.observe("ses_a", envelope)
    for (const envelope of turn) first.observe("ses_a", envelope)

    const second = new UsageBook(directory)
    expect(second.get("ses_a")).toEqual(first.get("ses_a"))
    expect(second.get("ses_a").session[0]).toMatchObject({ tokensIn: 20, tokensOut: 4, costUSD: 1, cacheRead: null })
    for (const envelope of turn) second.observe("ses_a", envelope)
    expect(new UsageBook(directory).get("ses_a").session[0]?.tokensIn).toBe(30)
    expect(new UsageBook(directory).get("ses_b").session).toEqual([])
  } finally {
    rmSync(directory, { recursive: true, force: true })
  }
})
