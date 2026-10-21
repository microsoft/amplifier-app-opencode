import type {
  LanguageModelV3FinishReason,
  LanguageModelV3Prompt,
  LanguageModelV3StreamPart,
  LanguageModelV3Usage,
} from "@ai-sdk/provider"
import { titleFrom } from "./mapping.ts"
import { fileChanges, PROXIED, type ProxyResult, proxyResult } from "./proxy.ts"
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

/**
 * The opencode tool and input a proxied Amplifier call runs through, when opencode offers that tool and the input is
 * one its schema accepts; otherwise the call stays hosted.
 */
export function proxiedCall(
  call: Pick<ToolCall, "name" | "arguments" | "source">,
  offered: ReadonlySet<string>,
): { name: string; input: Json } | undefined {
  if (call.source === "mcp") return undefined
  const name = PROXIED[call.name]
  if (!name || !offered.has(name)) return undefined
  const args = record(call.arguments)
  const string = (value: unknown) => (typeof value === "string" ? value : undefined)
  switch (name) {
    case "edit": {
      const path = text(args.file_path)
      const oldString = string(args.old_string)
      const newString = string(args.new_string)
      if (!path || oldString === undefined || newString === undefined) return undefined
      const replaceAll = typeof args.replace_all === "boolean" ? args.replace_all : undefined
      return { name, input: defined({ path, oldString, newString, replaceAll }) }
    }
    case "patch": {
      const patchText = text(args.patch) ?? text(args.diff)
      return patchText ? { name, input: { patchText } } : undefined
    }
    case "glob": {
      const pattern = text(args.pattern)
      return pattern ? { name, input: defined({ pattern, path: text(args.path) }) } : undefined
    }
    case "grep": {
      const pattern = text(args.pattern)
      if (!pattern) return undefined
      return { name, input: defined({ pattern, path: text(args.path), include: text(args.glob) }) }
    }
    default:
      return undefined
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

function entriesOf(snapshot: unknown): Json[] {
  const entries = record(snapshot).entries
  return (Array.isArray(entries) ? entries : []).map(record)
}

function usd(entry: Json): number | undefined {
  const value = record(entry.cost).USD
  return typeof value === "string" && value.trim() !== "" ? count(Number(value)) : undefined
}

/** The snapshot's cost in USD, or undefined when it is unknown. */
export function snapshotCost(snapshot: unknown): number | undefined {
  const entries = entriesOf(snapshot)
  return entries.length > 0 ? total(entries, usd) : undefined
}

/** Whether every counter and cost of the snapshot is known. */
export function snapshotKnown(snapshot: unknown): boolean {
  const entries = entriesOf(snapshot)
  return (
    entries.length > 0 &&
    entries.every(
      (entry) =>
        count(entry.tokens_in) !== undefined &&
        count(entry.tokens_out) !== undefined &&
        count(entry.cache_read_tokens) !== undefined &&
        usd(entry) !== undefined,
    )
  )
}

/**
 * The finish usage for a step: the face's chat-completions usage for its usage snapshot and context size, rewritten
 * by `recordedUsage`, as opencode's chat-completions route reads it. So the meter shows the context size and cache
 * writes carry the exact cost, as for chat completions. `costBefore` is the cost earlier steps of the turn reported,
 * so each step carries only what the turn spent since.
 */
export function turnUsage(snapshot: unknown, context: unknown, costBefore = 0): LanguageModelV3Usage {
  const items = entriesOf(snapshot)
  const tokensIn = total(items, (entry) => count(entry.tokens_in))
  const tokensOut = total(items, (entry) => count(entry.tokens_out))
  const cacheRead = total(items, (entry) => count(entry.cache_read_tokens))
  const cacheWrite = total(items, (entry) => count(entry.cache_write_tokens) ?? 0)
  const cost = snapshotCost(snapshot)
  const usage: Json = {}
  if (items.length > 0 && tokensIn !== undefined && tokensOut !== undefined) {
    const prompt = tokensIn + (cacheWrite ?? 0)
    usage.prompt_tokens = prompt
    usage.completion_tokens = tokensOut
    usage.total_tokens = prompt + tokensOut
    usage.prompt_tokens_details = defined({ cached_tokens: cacheRead, cache_write_tokens: cacheWrite })
  }
  if (cost !== undefined) usage.cost_usd = String(Math.max(0, cost - costBefore))
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
const TOOL_CALLS: LanguageModelV3FinishReason = { unified: "tool-calls", raw: "tool-calls" }

function errorText(error: unknown): string {
  const value = record(error)
  const message = text(value.message) ?? "The Amplifier turn failed."
  const remedy = text(value.remedy)
  return remedy ? `${message} ${remedy}` : message
}

/** Where proxied calls are registered and settled; the language model passes the `proxy` module. */
export interface ProxySink {
  register(callID: string, sessionID: string | undefined): void
  resolve(callID: string, result: ProxyResult): void
  reject(callID: string, error: Error): void
}

export interface MapperOptions {
  /** The opencode tools the request offers; Amplifier calls of those tools are proxied. */
  readonly offered?: ReadonlySet<string>
  readonly sink?: ProxySink
  readonly sessionID?: string
}

/**
 * Turn events to language model stream parts, one envelope at a time, for one or more steps. Amplifier tool calls are
 * hosted calls (`providerExecuted`) that get exactly one result, except proxied calls: those are local tool calls that
 * opencode runs through its wrapped tool, which returns Amplifier's result from the sink. A step with proxied calls
 * finishes with `tool-calls` before the next text or reasoning, once no hosted call is open, and the turn continues in
 * the next step. The last step finishes with `stop`, or with an error part when the turn failed or was cancelled.
 */
export class TurnMapper {
  private next = 0
  private textID: string | undefined
  private reasoningID: string | undefined
  private readonly open = new Map<string, string>()
  private readonly proxied = new Map<string, string>()
  private readonly offered: ReadonlySet<string>
  private readonly sink: ProxySink | undefined
  private readonly sessionID: string | undefined
  private snapshot: unknown
  private known: unknown
  private context: unknown
  private reported = 0
  private stepProxied = 0
  private ended = false
  private metadata: LanguageModelV3StreamPart | undefined

  constructor(
    private readonly modelID: string,
    options: MapperOptions = {},
  ) {
    this.offered = options.sink ? (options.offered ?? new Set()) : new Set()
    this.sink = options.sink
    this.sessionID = options.sessionID
  }

  /** Whether `terminal`, or a failure, already ended the stream. */
  get done(): boolean {
    return this.ended
  }

  /** The opencode session the turn runs in, when proxied calls are possible. */
  get session(): string | undefined {
    return this.sessionID
  }

  /** The parts that begin a step after the first: the stream start and the turn's response metadata. */
  stepStart(): LanguageModelV3StreamPart[] {
    return [{ type: "stream-start", warnings: [] }, ...(this.metadata ? [this.metadata] : [])]
  }

  /** The finish of a step with nothing left to show, after the turn already ended. */
  emptyFinish(): LanguageModelV3StreamPart {
    return this.finish(STOP)
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

  /** An error result for every hosted call still without one, and a failure for every open proxied call. */
  private settleOpen(message: string): LanguageModelV3StreamPart[] {
    const parts: LanguageModelV3StreamPart[] = [...this.open].map(([toolCallId, toolName]) => ({
      type: "tool-result",
      toolCallId,
      toolName,
      result: message,
      isError: true,
    }))
    this.open.clear()
    for (const callID of this.proxied.keys()) this.sink?.reject(callID, new Error(message))
    this.proxied.clear()
    return parts
  }

  /** The finish part of a step, carrying the cost the turn added since the previous step. */
  private finish(reason: LanguageModelV3FinishReason, raw = reason.raw): LanguageModelV3StreamPart {
    const snapshot = this.ended ? this.snapshot : this.known
    const usage = turnUsage(snapshot, this.context, this.reported)
    this.reported = Math.max(this.reported, snapshotCost(snapshot) ?? this.reported)
    return { type: "finish", finishReason: { ...reason, raw }, usage }
  }

  /** The end of a step with proxied calls, before content that belongs to the next step. */
  private boundary(): LanguageModelV3StreamPart[] {
    if (this.stepProxied === 0 || this.open.size > 0) return []
    this.stepProxied = 0
    return [...this.closeText(), ...this.closeReasoning(), this.finish(TOOL_CALLS)]
  }

  private toolCall(call: ToolCall): LanguageModelV3StreamPart[] {
    const proxied = this.sink ? proxiedCall(call, this.offered) : undefined
    const { name, input } = proxied ?? toolDisplay(call)
    const json = JSON.stringify(input)
    const id = call.call_id
    const parts: LanguageModelV3StreamPart[] = [...this.closeText(), ...this.closeReasoning()]
    if (proxied) {
      this.sink?.register(id, this.sessionID)
      this.proxied.set(id, name)
      this.stepProxied++
      return [
        ...parts,
        { type: "tool-input-start", id, toolName: name },
        { type: "tool-input-delta", id, delta: json },
        { type: "tool-input-end", id },
        { type: "tool-call", toolCallId: id, toolName: name, input: json },
      ]
    }
    this.open.set(id, name)
    return [
      ...parts,
      { type: "tool-input-start", id, toolName: name, providerExecuted: true },
      { type: "tool-input-delta", id, delta: json },
      { type: "tool-input-end", id },
      { type: "tool-call", toolCallId: id, toolName: name, input: json, providerExecuted: true },
    ]
  }

  private toolResult(payload: Json): LanguageModelV3StreamPart[] {
    const resolution = record(payload.resolution)
    const id = text(resolution.call_id)
    if (!id) return []
    const content = typeof resolution.content === "string" ? resolution.content : ""
    const outcome = text(resolution.outcome) ?? "unknown"
    const reason = outcome === "completed" ? undefined : resolution.error ? errorText(resolution.error) : undefined
    const failure = reason ?? `The tool call ended ${outcome}.`
    const proxied = this.proxied.get(id)
    if (proxied) {
      this.proxied.delete(id)
      if (outcome === "completed") this.sink?.resolve(id, proxyResult(proxied, content, fileChanges(payload)))
      else this.sink?.reject(id, new Error(content ? `${failure}\n${content}` : failure))
      return []
    }
    const name = this.open.get(id)
    if (!name) return []
    this.open.delete(id)
    if (outcome === "completed") return [{ type: "tool-result", toolCallId: id, toolName: name, result: content }]
    return [
      {
        type: "tool-result",
        toolCallId: id,
        toolName: name,
        result: content ? `${failure}\n${content}` : failure,
        isError: true,
      },
    ]
  }

  map(envelope: Envelope): LanguageModelV3StreamPart[] {
    if (this.ended) return []
    const payload = envelope.payload
    switch (envelope.type) {
      case "turn_started": {
        const actual = record(payload.primary_actual)
        const model = text(actual.provider) && text(actual.model) ? `${actual.provider}/${actual.model}` : this.modelID
        this.metadata = { type: "response-metadata", id: envelope.turn_id, modelId: model }
        return [{ type: "stream-start", warnings: [] }, this.metadata]
      }
      case "output_delta": {
        const content = Array.isArray(payload.content) ? payload.content : []
        const delta = content.map((part: unknown) => (record(part).type === "text" ? text(record(part).text) : ""))
        const joined = delta.join("")
        if (!joined) return []
        const parts = [...this.boundary(), ...this.closeReasoning()]
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
        const parts = [...this.boundary(), ...this.closeText()]
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
          ...this.boundary(),
          ...this.closeText(),
          { type: "reasoning-start", id },
          { type: "reasoning-delta", id, delta: block },
          { type: "reasoning-end", id },
        ]
      }
      case "tool_call": {
        const call = record(payload.call) as unknown as ToolCall
        if (!text(call.call_id) || !text(call.name)) return []
        if (this.open.has(call.call_id) || this.proxied.has(call.call_id)) return []
        return this.toolCall(call)
      }
      case "tool_result":
        return this.toolResult(payload)
      case "usage":
        this.snapshot = payload.snapshot
        if (snapshotKnown(payload.snapshot)) this.known = payload.snapshot
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
        this.stepProxied = 0
        if (state === "success" || state === "rejected") return [...parts, this.finish(STOP, state)]
        return [...parts, this.finish(STOP, state), { type: "error", error: new Error(errorText(payload.error)) }]
      }
      default:
        return []
    }
  }

  /** The parts that end a stream that broke before `terminal`: error results for open calls, then the error. */
  fail(error: unknown): LanguageModelV3StreamPart[] {
    if (this.ended) return []
    this.ended = true
    this.stepProxied = 0
    const message = error instanceof Error ? error.message : String(error)
    return [
      ...this.closeText(),
      ...this.closeReasoning(),
      ...this.settleOpen(message),
      { type: "error", error: error instanceof Error ? error : new Error(message) },
    ]
  }
}
