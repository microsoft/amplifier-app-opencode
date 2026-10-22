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
