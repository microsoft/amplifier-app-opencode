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

const unknown = {
  entries: [
    { provider: "openai", model: "gpt", tokens_in: null, tokens_out: null, cache_read_tokens: null, cost: null },
  ],
}
const zero = { tokensIn: 0, tokensOut: 0, cacheRead: 0, cacheWrite: 0, costUSD: 0 }

test("a usage snapshot taken before a request keeps the turn's earlier values", () => {
  const book = new UsageBook()
  for (const envelope of turn) book.observe("ses_a", envelope)
  book.observe("ses_a", { type: "turn_started", payload: {} })
  book.observe("ses_a", { type: "usage", payload: { snapshot: usage } })
  const usageNow = book.observe("ses_a", { type: "usage", payload: { snapshot: unknown } })

  expect(usageNow?.turn[0]).toMatchObject({ tokensIn: 10, tokensOut: 2, costUSD: 0.5 })
  expect(usageNow?.session[0]).toMatchObject({ tokensIn: 20, tokensOut: 4, costUSD: 1 })
})

test("a turn's first usage snapshot counts what is not yet known as 0", () => {
  const book = new UsageBook()
  book.observe("ses_a", { type: "turn_started", payload: {} })
  const usageNow = book.observe("ses_a", { type: "usage", payload: { snapshot: unknown } })

  expect(usageNow?.turn[0]).toMatchObject(zero)
  expect(usageNow?.session[0]).toMatchObject(zero)
})

test("a cost the provider never reports is unknown when the turn ends", () => {
  const book = new UsageBook()
  const never = { entries: [{ provider: "local", model: "llm", tokens_in: 7, tokens_out: 3, cost: null }] }
  book.observe("ses_a", { type: "turn_started", payload: {} })
  book.observe("ses_a", { type: "usage", payload: { snapshot: never } })
  const usageNow = book.observe("ses_a", { type: "terminal", payload: { usage: never } })

  expect(usageNow?.turn[0]).toMatchObject({ tokensIn: 7, tokensOut: 3, costUSD: null })
  expect(usageNow?.session[0]).toMatchObject({ tokensIn: 7, tokensOut: 3, costUSD: null })
})
