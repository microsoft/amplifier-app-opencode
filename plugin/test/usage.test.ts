import { describe, expect, test } from "bun:test"
import { CONTEXT_FIELD, recordedUsage } from "../src/usage.ts"

const FINAL =
  'data: {"id":"c","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":30000,' +
  '"completion_tokens":900,"total_tokens":30900,"prompt_tokens_details":{"cached_tokens":20000,' +
  `"cache_write_tokens":100},"cost_usd":"0.0421","${CONTEXT_FIELD}":{"tokens":12000,"budget":180000}}}\n\n`

function usageOf(text: string): Record<string, unknown> {
  const line = text.split("\n").find((part) => part.includes('"usage"'))
  return JSON.parse(line?.slice("data: ".length) ?? "null").usage
}

describe("recordedUsage", () => {
  test("reports the context size as input and carries the cost in cache writes", () => {
    const usage = recordedUsage(usageOf(FINAL))
    expect(usage).toEqual({
      prompt_tokens: 12000,
      completion_tokens: 0,
      total_tokens: 12000,
      prompt_tokens_details: { cached_tokens: 0, cache_write_tokens: 0.0421 },
      cost_usd: "0.0421",
      [CONTEXT_FIELD]: { tokens: 12000, budget: 180000 },
    })
  })

  test("tokens opencode derives add up to exactly the context size", () => {
    // opencode's uncached input is prompt_tokens - (cached_tokens + cache_write_tokens), and its meter adds the
    // input, output, reasoning, cache reads, and cache writes back up.
    for (let tokens = 0; tokens <= 2_000_000; tokens += 997) {
      for (const cost of ["0", "0.0000001", "0.0004134", "0.008151", "0.0421", "1.2345678", "37.5"]) {
        const face = { prompt_tokens: 1, completion_tokens: 1, cost_usd: cost, [CONTEXT_FIELD]: { tokens } }
        const usage = recordedUsage(face)
        const { cached_tokens: read, cache_write_tokens: write } = usage.prompt_tokens_details as {
          cached_tokens: number
          cache_write_tokens: number
        }
        const input = Math.max(0, (usage.prompt_tokens as number) - (0 + read + write))
        const meter = input + (usage.completion_tokens as number) + 0 + read + write
        if (tokens >= Number(cost)) expect(meter).toBe(tokens)
        expect(write).toBe(Number(cost))
      }
    }
  })

  test("drops reasoning counts with the context size", () => {
    const usage = recordedUsage({
      prompt_tokens: 10,
      completion_tokens: 5,
      completion_tokens_details: { reasoning_tokens: 3 },
      [CONTEXT_FIELD]: { tokens: 10 },
    })
    expect(usage.completion_tokens_details).toBeUndefined()
    expect(usage.prompt_tokens).toBe(10)
    expect(usage.prompt_tokens_details).toEqual({ cached_tokens: 0, cache_write_tokens: 0 })
  })

  test("without the context size keeps the face's counts and still carries the cost", () => {
    const usage = recordedUsage({
      prompt_tokens: 30000,
      completion_tokens: 900,
      total_tokens: 30900,
      prompt_tokens_details: { cached_tokens: 20000, cache_write_tokens: 100 },
      cost_usd: "0.5",
      [CONTEXT_FIELD]: { budget: 180000 },
    })
    expect(usage).toMatchObject({
      prompt_tokens: 29900.5,
      completion_tokens: 900,
      total_tokens: 30800.5,
      prompt_tokens_details: { cached_tokens: 20000, cache_write_tokens: 0.5 },
    })
  })

  test("without a cost moves cache writes into input so they cost nothing", () => {
    const usage = recordedUsage({
      prompt_tokens: 300,
      completion_tokens: 9,
      total_tokens: 309,
      prompt_tokens_details: { cached_tokens: 200, cache_write_tokens: 50 },
    })
    expect(usage).toEqual({
      prompt_tokens: 250,
      completion_tokens: 9,
      total_tokens: 259,
      prompt_tokens_details: { cached_tokens: 200, cache_write_tokens: 0 },
    })
  })

  test("leaves a usage without counts unchanged", () => {
    expect(recordedUsage({ cost_usd: "1" })).toEqual({ cost_usd: "1" })
  })
})
