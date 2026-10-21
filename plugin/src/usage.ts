/** The usage extension field holding `{tokens, budget}` of the turn's last primary model request. */
export const CONTEXT_FIELD = "com.microsoft.amplifier.context"

/**
 * The cost every Amplifier model declares to opencode: cache writes at one million USD per million tokens, so a turn's
 * `cache_write_tokens` is its cost in USD, and every other token is free.
 */
export const CARRIER_COST = [{ input: 0, output: 0, cache: { read: 0, write: 1_000_000 } }]

type Usage = Record<string, unknown>

function count(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : undefined
}

function record(value: unknown): Record<string, unknown> | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined
}

function costUSD(value: unknown): number | undefined {
  if (typeof value === "string" && value.trim() !== "") return count(Number(value))
  return count(value)
}

/**
 * The face's chat-completions usage rewritten for opencode, which shows the last message's tokens as the context
 * size and prices tokens with CARRIER_COST. With the context field the tokens add up to its size, cache writes
 * included; without it the face's counts stay. Either way the cost travels in `cache_write_tokens`, and the face's own
 * cache writes move into uncached input. A usage without counts the face reports stays unchanged.
 */
export function recordedUsage(usage: Usage): Usage {
  const write = costUSD(usage.cost_usd) ?? 0
  const details = record(usage.prompt_tokens_details) ?? {}
  const tokens = count(record(usage[CONTEXT_FIELD])?.tokens)
  if (tokens !== undefined) {
    const { completion_tokens_details: _, ...rest } = usage
    return {
      ...rest,
      prompt_tokens: tokens,
      completion_tokens: 0,
      total_tokens: tokens,
      prompt_tokens_details: { ...details, cached_tokens: 0, cache_write_tokens: write },
    }
  }
  const prompt = count(usage.prompt_tokens)
  const completion = count(usage.completion_tokens)
  if (prompt === undefined || completion === undefined) return usage
  const input = prompt - (count(details.cache_write_tokens) ?? 0) + write
  return {
    ...usage,
    prompt_tokens: input,
    total_tokens: input + completion,
    prompt_tokens_details: { ...details, cache_write_tokens: write },
  }
}

const LF = 0x0a
const CR = 0x0d
const decoder = new TextDecoder("utf-8", { fatal: true })
const encoder = new TextEncoder()

/** The end of the line terminator at `index`, or -1 when no terminator starts there or a lone CR ends the buffer. */
function terminatorEnd(bytes: Uint8Array, index: number, final: boolean): number {
  if (bytes[index] === LF) return index + 1
  if (bytes[index] !== CR) return -1
  if (index + 1 < bytes.length) return bytes[index + 1] === LF ? index + 2 : index + 1
  return final ? index + 1 : -1
}

/** The length of the first complete SSE event in `bytes`, through the blank line that ends it, or 0 when none is. */
function eventLength(bytes: Uint8Array, final: boolean): number {
  let lineStart = 0
  for (let index = 0; index < bytes.length; index++) {
    if (bytes[index] !== LF && bytes[index] !== CR) continue
    const end = terminatorEnd(bytes, index, final)
    if (end < 0) return 0
    if (index === lineStart) return end
    lineStart = end
    index = end - 1
  }
  return 0
}

/** An event whose only lines are `data:` fields carrying a JSON object with `usage`, with that usage rewritten. */
function rewriteEvent(event: Uint8Array): Uint8Array {
  let text: string
  try {
    text = decoder.decode(event)
  } catch {
    return event
  }
  const body = text.replace(/(\r\n|\r|\n)+$/, "")
  const ending = text.slice(body.length)
  const lines = body.split(/\r\n|\r|\n/)
  if (!lines.every((line) => line.startsWith("data:"))) return event
  const data = lines.map((line) => line.slice(line.startsWith("data: ") ? 6 : 5)).join("\n")
  let parsed: unknown
  try {
    parsed = JSON.parse(data)
  } catch {
    return event
  }
  const chunk = record(parsed)
  const usage = record(chunk?.usage)
  if (!chunk || !usage) return event
  return encoder.encode(`data: ${JSON.stringify({ ...chunk, usage: recordedUsage(usage) })}${ending}`)
}

function concat(left: Uint8Array, right: Uint8Array): Uint8Array {
  if (left.length === 0) return right
  const joined = new Uint8Array(left.length + right.length)
  joined.set(left)
  joined.set(right, left.length)
  return joined
}

/**
 * A server-sent events stream with `recordedUsage` applied to each `data:` event that carries a usage. Every other
 * byte passes through unchanged, each event as soon as its blank line arrives.
 */
export function usageRewriter(): TransformStream<Uint8Array, Uint8Array> {
  let pending: Uint8Array = new Uint8Array(0)
  const drain = (controller: TransformStreamDefaultController<Uint8Array>, final: boolean) => {
    for (let length = eventLength(pending, final); length > 0; length = eventLength(pending, final)) {
      controller.enqueue(rewriteEvent(pending.subarray(0, length)))
      pending = pending.subarray(length)
    }
  }
  return new TransformStream({
    transform(chunk, controller) {
      pending = concat(pending, chunk)
      drain(controller, false)
    },
    flush(controller) {
      drain(controller, true)
      if (pending.length > 0) controller.enqueue(pending)
    },
  })
}
