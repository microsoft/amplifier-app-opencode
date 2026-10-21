import { describe, expect, test } from "bun:test"
import { CONTEXT_FIELD, recordedUsage, usageRewriter } from "../src/usage.ts"

const encoder = new TextEncoder()

async function rewrite(chunks: readonly (string | Uint8Array)[]): Promise<{ text: string; parts: number }> {
  const source = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(typeof chunk === "string" ? encoder.encode(chunk) : chunk)
      controller.close()
    },
  })
  const parts: Uint8Array[] = []
  const reader = source.pipeThrough(usageRewriter()).getReader()
  for (let read = await reader.read(); !read.done; read = await reader.read()) parts.push(read.value)
  return { text: new TextDecoder().decode(Buffer.concat(parts)), parts: parts.length }
}

/** Every split of `text` into two chunks, and into one chunk per byte. */
function splits(text: string): string[][] {
  const all = [...Array(text.length + 1).keys()].map((at) => [text.slice(0, at), text.slice(at)])
  return [...all, [...text]]
}

const CONTENT = 'data: {"id":"c","choices":[{"index":0,"delta":{"content":"Hi"},"finish_reason":null}]}\n\n'
const FINAL =
  'data: {"id":"c","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":30000,' +
  '"completion_tokens":900,"total_tokens":30900,"prompt_tokens_details":{"cached_tokens":20000,' +
  `"cache_write_tokens":100},"cost_usd":"0.0421","${CONTEXT_FIELD}":{"tokens":12000,"budget":180000}}}\n\n`
const DONE = "data: [DONE]\n\n"
const ERROR = 'data: {"error":{"message":"The provider failed.","type":"provider","code":"x","param":null}}\n\n'

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

describe("usageRewriter", () => {
  const stream = CONTENT + FINAL + DONE
  const rewritten = `"usage":${JSON.stringify(recordedUsage(usageOf(FINAL)))}}`
  const expected = CONTENT + FINAL.replace(/"usage":.*\}\}\}/, () => rewritten) + DONE

  test("rewrites only the usage, wherever the chunks split the stream", async () => {
    expect(expected).not.toBe(stream)
    for (const chunks of splits(stream)) expect((await rewrite(chunks)).text).toBe(expected)
  })

  test("passes an event on as soon as its blank line arrives", async () => {
    const parts: string[] = []
    const rewriter = usageRewriter()
    const writer = rewriter.writable.getWriter()
    const reader = rewriter.readable.getReader()
    void writer.write(encoder.encode(CONTENT + CONTENT.slice(0, 10)))
    parts.push(new TextDecoder().decode((await reader.read()).value))
    expect(parts).toEqual([CONTENT])
    void writer.close()
  })

  test("passes error frames, [DONE], comments, and other fields through unchanged", async () => {
    const other = ': keep-alive\n\nevent: message\ndata: {"usage":{"prompt_tokens":1,"completion_tokens":1}}\n\n'
    for (const text of [ERROR, DONE, other, 'data: {"usage":"not an object"}\n\n', "data: {broken\n\n"]) {
      expect((await rewrite([text])).text).toBe(text)
    }
  })

  test("keeps CRLF framing and data split over lines", async () => {
    const usage = '{"prompt_tokens":7,"completion_tokens":1,"total_tokens":8,"cost_usd":"0.25"}'
    const text = `data: {"choices":[],\r\ndata: "usage":${usage}}\r\n\r\n${DONE}`
    const result = await rewrite(splits(text).at(-1) ?? [])
    expect(result.text).toBe(
      `data: {"choices":[],"usage":${JSON.stringify(recordedUsage(JSON.parse(usage)))}}\r\n\r\n${DONE}`,
    )
  })

  test("keeps multi-byte characters split across chunks", async () => {
    const text = 'data: {"choices":[{"delta":{"content":"caf\u00e9 \u{1F600}"}}]}\n\n'
    const bytes = encoder.encode(text)
    for (let at = 0; at <= bytes.length; at++) {
      expect((await rewrite([bytes.subarray(0, at), bytes.subarray(at)])).text).toBe(text)
    }
  })

  test("passes an unterminated tail through at the end", async () => {
    expect((await rewrite([CONTENT, "data: [DONE]"])).text).toBe(`${CONTENT}data: [DONE]`)
  })
})
