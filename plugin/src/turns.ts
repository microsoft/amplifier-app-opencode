import type {
  LanguageModelV3FinishReason,
  LanguageModelV3Prompt,
  LanguageModelV3StreamPart,
  LanguageModelV3Usage,
} from "@ai-sdk/provider"
import { titleFrom } from "./mapping.ts"
import { CONTEXT_FIELD, recordedUsage } from "./usage.ts"

/** One `turn-events/1` envelope from the face's turns endpoint. */
export interface Envelope {
  readonly type: string
  readonly turn_id?: string
  readonly sequence?: number
  readonly payload: Record<string, any>
}

/** A tool call as the face reports it. */
export interface ToolCall {
  readonly call_id: string
  readonly name: string
  readonly source?: string
  readonly arguments: Record<string, unknown>
}

type Json = Record<string, unknown>

function record(value: unknown): Json {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Json) : {}
}

function text(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined
}

function defined(value: Json): Json {
  return Object.fromEntries(Object.entries(value).filter(([, item]) => item !== undefined))
}

// ---------------------------------------------------------------------------------------------------------------------
// Request

const SYSTEM_UPDATE = /^<system-update>\n([\s\S]*)\n<\/system-update>$/

function dataURL(data: unknown, mediaType: string): string | undefined {
  if (data instanceof URL) return data.protocol === "data:" ? data.href : undefined
  if (data instanceof Uint8Array) return `data:${mediaType};base64,${Buffer.from(data).toString("base64")}`
  if (typeof data !== "string") return undefined
  if (data.startsWith("data:")) return data
  if (/^[a-z]+:\/\//i.test(data)) return undefined
  return `data:${mediaType};base64,${data}`
}

/**
 * The chat-completions `messages` for the face from opencode's prompt. opencode's own system prompt describes
 * opencode's tools, so it is dropped; Amplifier runs with its own instructions. A later system update stays a system
 * message, so the request's user messages are the conversation's user turns. User messages keep their text and
 * images. Assistant messages keep their text and reasoning; tool calls, tool results, and tool messages are dropped,
 * because the face holds them in its session and refuses them in requests.
 */
export function promptMessages(prompt: LanguageModelV3Prompt): Json[] {
  const messages: Json[] = []
  let leading = true
  for (const message of prompt) {
    if (message.role === "system") {
      if (!leading) messages.push({ role: "system", content: message.content })
      continue
    }
    leading = false
    if (message.role === "tool") continue
    if (message.role === "user") {
      const only = message.content.length === 1 ? message.content[0] : undefined
      const update = only?.type === "text" ? SYSTEM_UPDATE.exec(only.text) : null
      if (update) {
        messages.push({ role: "system", content: update[1] })
        continue
      }
      const parts: Json[] = []
      for (const part of message.content) {
        if (part.type === "text") parts.push({ type: "text", text: part.text })
        else if (part.type === "file" && part.mediaType.startsWith("image/")) {
          const url = dataURL(part.data, part.mediaType === "image/*" ? "image/png" : part.mediaType)
          if (url) parts.push({ type: "image_url", image_url: { url } })
        }
      }
      messages.push({ role: "user", content: parts })
      continue
    }
    let content = ""
    let reasoning = ""
    for (const part of message.content) {
      if (part.type === "text") content += part.text
      else if (part.type === "reasoning") reasoning += part.text
    }
    messages.push(
      reasoning ? { role: "assistant", content, reasoning_content: reasoning } : { role: "assistant", content },
    )
  }
  return messages
}

// ---------------------------------------------------------------------------------------------------------------------
// Server-sent events

/** The envelope in one SSE event's `data:` lines, or undefined when it holds none. */
function envelopeOf(event: string): Envelope | undefined {
  const data = event
    .split(/\r\n|\r|\n/)
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(line.startsWith("data: ") ? 6 : 5))
  if (data.length === 0) return undefined
  try {
    const parsed = JSON.parse(data.join("\n"))
    return typeof parsed?.type === "string" ? { ...parsed, payload: record(parsed.payload) } : undefined
  } catch {
    return undefined
  }
}

const BLANK_LINE = /(?:\r\n|\r(?!\n)|\n)(?:\r\n|\r(?!\n)|\n)/

/** The envelopes of a server-sent event stream, each as soon as the blank line that ends its event arrives. */
export function envelopes(): TransformStream<Uint8Array, Envelope> {
  const decoder = new TextDecoder()
  let pending = ""
  const drain = (controller: TransformStreamDefaultController<Envelope>) => {
    // A blank line ends an event. A CR ending the buffer is taken as a whole terminator; were it the first half of a
    // CRLF, the LF that follows only adds an empty line to the next event.
    for (let match = BLANK_LINE.exec(pending); match; match = BLANK_LINE.exec(pending)) {
      const envelope = envelopeOf(pending.slice(0, match.index))
      pending = pending.slice(match.index + match[0].length)
      if (envelope) controller.enqueue(envelope)
    }
  }
  return new TransformStream({
    transform(chunk, controller) {
      pending += decoder.decode(chunk, { stream: true })
      drain(controller)
    },
    flush(controller) {
      pending += decoder.decode()
      drain(controller)
      const envelope = envelopeOf(pending)
      if (envelope) controller.enqueue(envelope)
      pending = ""
    },
  })
}

// ---------------------------------------------------------------------------------------------------------------------
// Tool display and permissions

/** The tool name and input opencode renders for an Amplifier tool call. */
export function toolDisplay(call: Pick<ToolCall, "name" | "arguments">): { name: string; input: Json } {
  const args = record(call.arguments)
  switch (call.name) {
    case "bash":
      return { name: "bash", input: defined({ command: args.command, description: args.description }) }
    case "read_file":
      return { name: "read", input: defined({ path: args.file_path, offset: args.offset, limit: args.limit }) }
    case "write_file":
      return { name: "write", input: defined({ path: args.file_path, content: args.content }) }
    case "edit_file":
      return {
        name: "edit",
        input: defined({
          path: args.file_path,
          oldString: args.old_string,
          newString: args.new_string,
          replaceAll: args.replace_all,
        }),
      }
    case "apply_patch":
      return { name: "apply_patch", input: defined({ patchText: args.patch ?? args.diff, path: args.path }) }
    case "glob":
      return { name: "glob", input: defined({ pattern: args.pattern, path: args.path }) }
    case "grep":
      return { name: "grep", input: defined({ pattern: args.pattern, path: args.path, include: args.glob }) }
    case "web_fetch":
      return { name: "webfetch", input: defined({ url: args.url }) }
    case "web_search":
      return { name: "websearch", input: defined({ query: args.query }) }
    case "delegate": {
      const prompt = text(args.instruction) ?? ""
      const agent = text(args.agent)
      return {
        name: "task",
        input: defined({ description: titleFrom(prompt) ?? agent ?? "delegate", prompt, subagent_type: agent }),
      }
    }
    default:
      return { name: call.name, input: args }
  }
}

/** The files an `apply_patch` call touches, from its patch text or its single path. */
function patchPaths(args: Json): string[] {
  const patch = text(args.patch)
  if (!patch) return text(args.path) ? [args.path as string] : []
  const paths = [...patch.matchAll(/^\*\*\* (?:Add|Update|Delete) File: (.+)$|^\*\*\* Move to: (.+)$/gm)]
  return [...new Set(paths.map((match) => (match[1] ?? match[2]).trim()))]
}

/** The opencode permission an Amplifier approval asks for. */
export interface PermissionAsk {
  readonly action: string
  readonly resources: string[]
  readonly save: string[]
}

/**
 * The opencode permission for an Amplifier tool call, so opencode's permission rules and "always allow" apply.
 * Shell commands use opencode's `shell` action, the one its `bash` permission key configures.
 */
export function permissionAsk(call: Pick<ToolCall, "name" | "arguments" | "source">): PermissionAsk {
  const args = record(call.arguments)
  const one = (value: unknown, save = ["*"]) => {
    const resource = text(value) ?? "*"
    return { resources: [resource], save }
  }
  if (call.source === "mcp") return { action: "mcp", ...one(call.name) }
  switch (call.name) {
    case "bash": {
      const command = text(args.command)
      const head = command?.trim().split(/\s+/)[0]
      return { action: "shell", ...one(command, head ? [`${head} *`] : []) }
    }
    case "write_file":
    case "edit_file":
      return { action: "edit", ...one(args.file_path) }
    case "apply_patch": {
      const paths = patchPaths(args)
      return { action: "edit", resources: paths.length ? paths : ["*"], save: ["*"] }
    }
    case "read_file":
      return { action: "read", ...one(args.file_path) }
    case "glob":
    case "grep":
      return { action: "read", ...one(args.path ?? args.pattern) }
    case "web_fetch":
      return { action: "webfetch", ...one(args.url) }
    case "web_search":
      return { action: "webfetch", ...one(args.query) }
    default:
      return { action: call.name, resources: ["*"], save: ["*"] }
  }
}

// ---------------------------------------------------------------------------------------------------------------------
// Usage

function count(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : undefined
}

/** A known sum of one field over every entry, or undefined when any entry's value is unknown. */
function total(entries: Json[], read: (entry: Json) => number | undefined): number | undefined {
  let sum = 0
  for (const entry of entries) {
    const value = read(entry)
    if (value === undefined) return undefined
    sum += value
  }
  return sum
}

/**
 * The finish usage for a turn: the face's chat-completions usage for its usage snapshot and context size, rewritten by
 * `recordedUsage`, as opencode's chat-completions route reads it. So the meter shows the context size and cache writes
 * carry the exact cost, as for chat completions.
 */
export function turnUsage(snapshot: unknown, context: unknown): LanguageModelV3Usage {
  const entries = (Array.isArray(record(snapshot).entries) ? record(snapshot).entries : []) as unknown[]
  const items = entries.map(record)
  const tokensIn = total(items, (entry) => count(entry.tokens_in))
  const tokensOut = total(items, (entry) => count(entry.tokens_out))
  const cacheRead = total(items, (entry) => count(entry.cache_read_tokens))
  const cacheWrite = total(items, (entry) => count(entry.cache_write_tokens) ?? 0)
  const cost = total(items, (entry) => {
    const usd = record(entry.cost).USD
    return typeof usd === "string" && usd.trim() !== "" ? count(Number(usd)) : undefined
  })
  const usage: Json = {}
  if (items.length > 0 && tokensIn !== undefined && tokensOut !== undefined) {
    const prompt = tokensIn + (cacheWrite ?? 0)
    usage.prompt_tokens = prompt
    usage.completion_tokens = tokensOut
    usage.total_tokens = prompt + tokensOut
    usage.prompt_tokens_details = defined({ cached_tokens: cacheRead, cache_write_tokens: cacheWrite })
  }
  if (items.length > 0 && cost !== undefined) usage.cost_usd = String(cost)
  const size = record(context)
  if (count(size.tokens) !== undefined) usage[CONTEXT_FIELD] = defined({ tokens: size.tokens, budget: size.budget })
  const recorded = recordedUsage(usage)
  const prompt = count(recorded.prompt_tokens)
  const completion = count(recorded.completion_tokens)
  const details = record(recorded.prompt_tokens_details)
  const read = count(details.cached_tokens) ?? 0
  const write = count(details.cache_write_tokens) ?? 0
  return {
    inputTokens: {
      total: prompt,
      noCache: prompt === undefined ? undefined : Math.max(0, prompt - read - write),
      cacheRead: prompt === undefined ? undefined : read,
      cacheWrite: prompt === undefined && write === 0 ? undefined : write,
    },
    outputTokens: { total: completion, text: completion, reasoning: undefined },
  }
}

// ---------------------------------------------------------------------------------------------------------------------
// Events to stream parts

const STOP: LanguageModelV3FinishReason = { unified: "stop", raw: "success" }

function errorText(error: unknown): string {
  const value = record(error)
  const message = text(value.message) ?? "The Amplifier turn failed."
  const remedy = text(value.remedy)
  return remedy ? `${message} ${remedy}` : message
}

/**
 * Turn events to language model stream parts, one envelope at a time. Every Amplifier tool call is a hosted call
 * (`providerExecuted`) that gets exactly one result, and a turn is one step that finishes with `stop`, or with an
 * error part when the turn failed or was cancelled.
 */
export class TurnMapper {
  private next = 0
  private textID: string | undefined
  private reasoningID: string | undefined
  private readonly open = new Map<string, string>()
  private snapshot: unknown
  private context: unknown
  private ended = false

  constructor(private readonly modelID: string) {}

  /** Whether `terminal`, or a failure, already ended the stream. */
  get done(): boolean {
    return this.ended
  }

  private id(kind: string): string {
    return `${kind}-${this.next++}`
  }

  private closeText(): LanguageModelV3StreamPart[] {
    if (!this.textID) return []
    const id = this.textID
    this.textID = undefined
    return [{ type: "text-end", id }]
  }

  private closeReasoning(): LanguageModelV3StreamPart[] {
    if (!this.reasoningID) return []
    const id = this.reasoningID
    this.reasoningID = undefined
    return [{ type: "reasoning-end", id }]
  }

  /** An error result for every call still without one. */
  private settleOpen(message: string): LanguageModelV3StreamPart[] {
    const parts: LanguageModelV3StreamPart[] = [...this.open].map(([toolCallId, toolName]) => ({
      type: "tool-result",
      toolCallId,
      toolName,
      result: message,
      isError: true,
    }))
    this.open.clear()
    return parts
  }

  private finish(raw: string): LanguageModelV3StreamPart {
    return { type: "finish", finishReason: { ...STOP, raw }, usage: turnUsage(this.snapshot, this.context) }
  }

  map(envelope: Envelope): LanguageModelV3StreamPart[] {
    if (this.ended) return []
    const payload = envelope.payload
    switch (envelope.type) {
      case "turn_started": {
        const actual = record(payload.primary_actual)
        const model = text(actual.provider) && text(actual.model) ? `${actual.provider}/${actual.model}` : this.modelID
        return [
          { type: "stream-start", warnings: [] },
          { type: "response-metadata", id: envelope.turn_id, modelId: model },
        ]
      }
      case "output_delta": {
        const content = Array.isArray(payload.content) ? payload.content : []
        const delta = content.map((part: unknown) => (record(part).type === "text" ? text(record(part).text) : ""))
        const joined = delta.join("")
        if (!joined) return []
        const parts = this.closeReasoning()
        if (!this.textID) {
          this.textID = this.id("text")
          parts.push({ type: "text-start", id: this.textID })
        }
        parts.push({ type: "text-delta", id: this.textID, delta: joined })
        return parts
      }
      case "reasoning_delta": {
        const delta = text(payload.text)
        if (!delta) return []
        const parts = this.closeText()
        if (!this.reasoningID) {
          this.reasoningID = this.id("reasoning")
          parts.push({ type: "reasoning-start", id: this.reasoningID })
        }
        parts.push({ type: "reasoning-delta", id: this.reasoningID, delta })
        return parts
      }
      case "reasoning_final": {
        if (this.reasoningID) return this.closeReasoning()
        const block = text(payload.text)
        if (!block) return []
        const id = this.id("reasoning")
        return [
          ...this.closeText(),
          { type: "reasoning-start", id },
          { type: "reasoning-delta", id, delta: block },
          { type: "reasoning-end", id },
        ]
      }
      case "tool_call": {
        const call = record(payload.call) as unknown as ToolCall
        if (!text(call.call_id) || !text(call.name) || this.open.has(call.call_id)) return []
        const { name, input } = toolDisplay(call)
        const json = JSON.stringify(input)
        this.open.set(call.call_id, name)
        const id = call.call_id
        return [
          ...this.closeText(),
          ...this.closeReasoning(),
          { type: "tool-input-start", id, toolName: name, providerExecuted: true },
          { type: "tool-input-delta", id, delta: json },
          { type: "tool-input-end", id },
          { type: "tool-call", toolCallId: id, toolName: name, input: json, providerExecuted: true },
        ]
      }
      case "tool_result": {
        const resolution = record(payload.resolution)
        const id = text(resolution.call_id)
        const name = id ? this.open.get(id) : undefined
        if (!id || !name) return []
        this.open.delete(id)
        const content = typeof resolution.content === "string" ? resolution.content : ""
        const outcome = text(resolution.outcome) ?? "unknown"
        if (outcome === "completed") return [{ type: "tool-result", toolCallId: id, toolName: name, result: content }]
        const reason = resolution.error ? errorText(resolution.error) : `The tool call ended ${outcome}.`
        return [
          {
            type: "tool-result",
            toolCallId: id,
            toolName: name,
            result: content ? `${reason}\n${content}` : reason,
            isError: true,
          },
        ]
      }
      case "usage":
        this.snapshot = payload.snapshot
        return []
      case "progress": {
        const size = record(record(payload.data).context_size)
        if (Object.keys(size).length > 0) this.context = size
        return []
      }
      case "terminal": {
        if (payload.usage) this.snapshot = payload.usage
        const state = text(payload.state) ?? "failure"
        const parts = [
          ...this.closeText(),
          ...this.closeReasoning(),
          ...this.settleOpen(`The Amplifier turn ended ${state}.`),
        ]
        this.ended = true
        if (state === "success" || state === "rejected") return [...parts, this.finish(state)]
        return [...parts, this.finish(state), { type: "error", error: new Error(errorText(payload.error)) }]
      }
      default:
        return []
    }
  }

  /** The parts that end a stream that broke before `terminal`: error results for open calls, then the error. */
  fail(error: unknown): LanguageModelV3StreamPart[] {
    if (this.ended) return []
    this.ended = true
    const message = error instanceof Error ? error.message : String(error)
    return [
      ...this.closeText(),
      ...this.closeReasoning(),
      ...this.settleOpen(message),
      { type: "error", error: error instanceof Error ? error : new Error(message) },
    ]
  }
}
