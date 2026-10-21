import type {
  LanguageModelV3,
  LanguageModelV3CallOptions,
  LanguageModelV3Content,
  LanguageModelV3StreamPart,
} from "@ai-sdk/provider"
import type { Face } from "./face.ts"
import { log } from "./face.ts"
import { sessionHeader } from "./mapping.ts"
import * as proxy from "./proxy.ts"
import { PROXY_TOOLS } from "./proxy.ts"
import { type Envelope, envelopes, permissionAsk, promptMessages, type ToolCall, TurnMapper } from "./turns.ts"

export const PROVIDER = "amplifier"

/** What the language model needs from the plugin: the running face, and opencode's permission check. */
export interface LanguageContext {
  /** The face to start a turn on, once a restart the plugin has scheduled is done. */
  face(): Promise<Face | undefined>
  /** Whether turns ask opencode before each effect the face's policy allows. */
  readonly asks: boolean
  /** Resolves when opencode allows the permission; rejects when it is declined or cannot be asked. */
  assert(input: {
    sessionID: string
    action: string
    resources: string[]
    save: string[]
    metadata: Record<string, unknown>
  }): Promise<void>
  /** Sees every event of every turn, for the usage the plugin reports. */
  observe?(sessionID: string, envelope: Envelope): void
}

/** What a plugin instance serves its location's turns with. */
interface Served {
  readonly context: LanguageContext
  model(modelId: string): LanguageModelV3
}

/**
 * The plugin instance serving each location directory. It lives on `globalThis` so a language model opencode cached
 * from an earlier load of the plugin, after a reinstall or reload, still finds the instance that runs now.
 */
function served(): Map<string, Served> {
  const key = Symbol.for("amplifier-opencode.served")
  const scope = globalThis as unknown as Record<symbol, Map<string, Served> | undefined>
  scope[key] ??= new Map()
  return scope[key]
}

/** Serve the turns of a location with `context`, until the returned function is called. */
export function serve(directory: string, context: LanguageContext): () => void {
  const entry: Served = { context, model: (modelId) => new AmplifierLanguageModel(modelId, directory, context) }
  served().set(directory, entry)
  return () => {
    if (served().get(directory) === entry) served().delete(directory)
  }
}

/** The Amplifier turn of each opencode session, kept across the steps it spans. */
const turns = new Map<string, Turn>()
/** The finish of each session's last step, for a step that continues a turn that already ended. */
const lastFinish = new Map<string, Extract<LanguageModelV3StreamPart, { type: "finish" }>>()

/** Stop the turn running for an opencode session; closing its request makes the face cancel the turn. */
export function interrupt(sessionID: string): void {
  const error = new Error("The opencode session was interrupted.")
  turns.get(sessionID)?.abort(error)
  proxy.rejectAll(error, sessionID)
}

function header(headers: Record<string, string | undefined> | undefined, name: string): string | undefined {
  const wanted = name.toLowerCase()
  for (const [key, value] of Object.entries(headers ?? {})) if (key.toLowerCase() === wanted && value) return value
  return undefined
}

/** How a declined opencode permission differs from one that could not be asked. */
function declined(error: unknown): boolean {
  const value = error as { _tag?: unknown; name?: unknown; message?: unknown } | undefined
  const tag = `${String(value?._tag ?? "")} ${String(value?.name ?? "")}`
  return /Declined|Corrected|Blocked|Tool\.Error|ToolFailure|Rejected/i.test(tag)
}

function reasonOf(error: unknown): string | undefined {
  const message = (error as { message?: unknown } | undefined)?.message
  return typeof message === "string" && message.trim() !== "" ? message : undefined
}

async function failure(response: Response): Promise<Error> {
  let detail = `HTTP ${response.status}`
  try {
    const body = (await response.json()) as { error?: { message?: string } }
    if (body.error?.message) detail = body.error.message
  } catch {
    // The status is the detail.
  }
  return new Error(`Amplifier refused the turn: ${detail}`)
}

/**
 * Amplifier Agent as an AI SDK language model. One `doStream` is one Amplifier turn through the face's turns endpoint:
 * the newest user message continues the face session derived from the opencode session, and the turn's events become
 * text, reasoning, and hosted tool calls with their results. opencode caches a model across plugin reloads, so each
 * `doStream` runs on the plugin instance serving the model's location when it is not the one that created the model.
 */
export class AmplifierLanguageModel implements LanguageModelV3 {
  readonly specificationVersion = "v3"
  readonly provider = PROVIDER
  readonly supportedUrls = {}

  constructor(
    readonly modelId: string,
    private readonly directory: string,
    private readonly context: LanguageContext,
  ) {}

  async doGenerate(options: LanguageModelV3CallOptions) {
    const { stream } = await this.doStream(options)
    const content: LanguageModelV3Content[] = []
    let finish: Extract<LanguageModelV3StreamPart, { type: "finish" }> | undefined
    const reader = stream.getReader()
    for (let read = await reader.read(); !read.done; read = await reader.read()) {
      const part = read.value
      if (part.type === "text-delta") content.push({ type: "text", text: part.delta })
      else if (part.type === "reasoning-delta") content.push({ type: "reasoning", text: part.delta })
      else if (part.type === "error") throw part.error
      else if (part.type === "finish") finish = part
    }
    if (!finish) throw new Error("The Amplifier turn ended without a result.")
    return { content, finishReason: finish.finishReason, usage: finish.usage, warnings: [] }
  }

  async doStream(options: LanguageModelV3CallOptions) {
    const current = served().get(this.directory)
    if (current && current.context !== this.context) return current.model(this.modelId).doStream(options)
    const sessionID = header(options.headers, "x-opencode-session-id")
    const continues = options.prompt.at(-1)?.role === "tool"
    let turn = sessionID ? turns.get(sessionID) : undefined
    if (turn && !continues) {
      turn.abort(new Error("A newer turn started in this opencode session."))
      turn = undefined
    }
    if (!turn && continues) {
      // The turn that ran these tool calls already ended, or stopped; this step only closes it. Starting a turn here
      // would send the last user message again.
      const finish = (sessionID ? lastFinish.get(sessionID) : undefined) ?? EMPTY_FINISH
      return { stream: finished(finish), request: { body: {} } }
    }
    if (!turn) {
      turn = await this.start(options, sessionID)
      if (sessionID) turns.set(sessionID, turn)
    }
    return { stream: turn.step(options.abortSignal), request: { body: turn.body } }
  }

  private async start(options: LanguageModelV3CallOptions, sessionID: string | undefined): Promise<Turn> {
    const face = await this.context.face()
    if (!face) throw new Error("The Amplifier face is not running. Run: amplifier-opencode doctor")
    // A face retired by a restart keeps running until its turns end.
    const release = face.hold()
    try {
      return await this.run(face, release, options, sessionID)
    } catch (error) {
      release()
      throw error
    }
  }

  private async run(
    face: Face,
    release: () => void,
    options: LanguageModelV3CallOptions,
    sessionID: string | undefined,
  ): Promise<Turn> {
    const effort = options.providerOptions?.[PROVIDER]?.reasoningEffort
    const body = {
      model: this.modelId,
      messages: promptMessages(options.prompt),
      ...(typeof effort === "string" ? { reasoning_effort: effort } : {}),
      ...(this.context.asks ? { approvals: "ask" } : {}),
    }
    if (sessionID) proxy.forget(sessionID)
    const abort = new AbortController()
    const response = await fetch(`${face.baseURL}/amplifier/turns`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${face.token}`,
          "Content-Type": "application/json",
          ...(sessionID ? { "Amplifier-Session-Id": sessionHeader(sessionID) } : {}),
        },
        body: JSON.stringify(body),
      signal: abort.signal,
    })
    if (!response.ok || !response.body) throw await failure(response)

    // Proxied calls need a later step in the same session, so a request without one keeps every call hosted.
    const offered = new Set((options.tools ?? []).map((tool) => tool.name).filter((name) => PROXY_TOOLS.includes(name)))
    const mapper = new TurnMapper(this.modelId, sessionID ? { offered, sink: proxy, sessionID } : {})
    const turn = new Turn(body, mapper, abort, () => {
      if (sessionID && turns.get(sessionID) === turn) turns.delete(sessionID)
    })
    const calls = new Map<string, ToolCall>()
    const answer = (requestID: string, decision: "allow" | "deny" | "cancel", reason?: string) => {
      if (abort.signal.aborted) return
      fetch(`${face.baseURL}/amplifier/approvals/${encodeURIComponent(requestID)}`, {
        method: "POST",
        headers: { Authorization: `Bearer ${face.token}`, "Content-Type": "application/json" },
        body: JSON.stringify(reason ? { decision, reason } : { decision }),
        signal: abort.signal,
      }).catch((error: Error) => log(`could not answer approval ${requestID}: ${error.message}`))
    }
    const approve = async (request: Record<string, unknown>) => {
      const requestID = String(request.request_id ?? "")
      if (!requestID) return
      if (!sessionID) return answer(requestID, "cancel", "The request named no opencode session to ask.")
      const call = calls.get(String(request.call_id ?? ""))
      const ask = permissionAsk(call ?? { name: String(request.name ?? "unknown"), arguments: {} })
      try {
        await this.context.assert({
          sessionID,
          ...ask,
          metadata: { amplifier: { tool: call?.name ?? request.name, summary: request.summary } },
        })
        answer(requestID, "allow")
      } catch (error) {
        if (declined(error)) answer(requestID, "deny", reasonOf(error) ?? "Declined in opencode.")
        else {
          log(`could not ask opencode about ${ask.action}: ${reasonOf(error) ?? String(error)}`)
          answer(requestID, "cancel", reasonOf(error))
        }
      }
    }

    const reader = response.body.pipeThrough(envelopes()).getReader()
    void (async () => {
      while (!mapper.done) {
        let result: Awaited<ReturnType<typeof reader.read>>
        try {
          result = await reader.read()
        } catch (error) {
          turn.push(mapper.fail(abort.signal.aborted ? new Error("The Amplifier turn was cancelled.") : error))
          break
        }
        if (result.done) {
          turn.push(mapper.fail(new Error("The Amplifier face ended the turn without a result.")))
          break
        }
        const envelope = result.value
        if (envelope.type === "tool_call") {
          const call = envelope.payload.call as ToolCall | undefined
          if (call?.call_id) calls.set(call.call_id, call)
        } else if (envelope.type === "approval_request" && this.context.asks) {
          // Without `approvals: "ask"` the face's policy already answered.
          void approve((envelope.payload.request ?? {}) as Record<string, unknown>)
        }
        if (sessionID) this.context.observe?.(sessionID, envelope)
        turn.push(mapper.map(envelope))
      }
      turn.end()
      reader.releaseLock()
      release()
    })()
    return turn
  }
}

const EMPTY_FINISH: LanguageModelV3StreamPart = {
  type: "finish",
  finishReason: { unified: "stop", raw: "success" },
  usage: {
    inputTokens: { total: undefined, noCache: undefined, cacheRead: undefined, cacheWrite: undefined },
    outputTokens: { total: undefined, text: undefined, reasoning: undefined },
  },
}

/** A stream of one step that only finishes. */
function finished(finish: LanguageModelV3StreamPart): ReadableStream<LanguageModelV3StreamPart> {
  return new ReadableStream({
    start(controller) {
      controller.enqueue({ type: "stream-start", warnings: [] })
      controller.enqueue(finish)
      controller.close()
    },
  })
}

/**
 * One Amplifier turn: its events, read as they arrive and mapped to stream parts, wait in order for the steps that
 * show them. A step ends at a `tool-calls` finish; the next step continues with the parts after it.
 */
class Turn {
  private readonly parts: LanguageModelV3StreamPart[] = []
  private wake: (() => void) | undefined
  private ended = false
  private steps = 0

  constructor(
    readonly body: Record<string, unknown>,
    private readonly mapper: TurnMapper,
    private readonly controller: AbortController,
    private readonly release: () => void,
  ) {}

  push(parts: LanguageModelV3StreamPart[]): void {
    if (parts.length === 0) return
    this.parts.push(...parts)
    this.wake?.()
  }

  end(): void {
    this.ended = true
    this.wake?.()
  }

  abort(reason: unknown): void {
    this.controller.abort(reason)
    this.release()
  }

  private async next(): Promise<LanguageModelV3StreamPart | undefined> {
    while (this.parts.length === 0) {
      if (this.ended) return undefined
      await new Promise<void>((wake) => {
        this.wake = wake
      })
      this.wake = undefined
    }
    return this.parts.shift()
  }

  /** The stream of the turn's next step. Aborting the step, or cancelling its stream, cancels the turn. */
  step(signal: AbortSignal | undefined): ReadableStream<LanguageModelV3StreamPart> {
    const first = this.steps++ === 0
    const forward = () => this.abort(signal?.reason)
    signal?.addEventListener("abort", forward, { once: true })
    const done = () => signal?.removeEventListener("abort", forward)
    let finished = false
    let started = first
    return new ReadableStream<LanguageModelV3StreamPart>({
      pull: async (controller) => {
        if (!started) {
          started = true
          for (const part of this.mapper.stepStart()) controller.enqueue(part)
          return
        }
        const part = await this.next()
        if (!part) {
          if (!finished) controller.enqueue(this.mapper.emptyFinish())
          done()
          this.release()
          controller.close()
          return
        }
        controller.enqueue(part)
        if (part.type !== "finish") return
        finished = true
        this.remember(part)
        if (part.finishReason.unified === "tool-calls") {
          done()
          controller.close()
        }
      },
      cancel: (reason) => {
        done()
        this.abort(reason)
      },
    })
  }

  private remember(part: Extract<LanguageModelV3StreamPart, { type: "finish" }>): void {
    const session = this.mapper.session
    if (!session) return
    // A step that only closes the turn shows its context size again and costs nothing.
    const inputTokens = { ...part.usage.inputTokens, cacheWrite: 0 }
    lastFinish.set(session, {
      ...part,
      finishReason: { unified: "stop", raw: "success" },
      usage: { ...part.usage, inputTokens },
    })
  }
}
