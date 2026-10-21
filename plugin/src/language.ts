import type {
  LanguageModelV3,
  LanguageModelV3CallOptions,
  LanguageModelV3Content,
  LanguageModelV3StreamPart,
} from "@ai-sdk/provider"
import type { Face } from "./face.ts"
import { log } from "./face.ts"
import { sessionHeader } from "./mapping.ts"
import { envelopes, permissionAsk, promptMessages, type ToolCall, TurnMapper } from "./turns.ts"

export const PROVIDER = "amplifier"

/** What the language model needs from the plugin: the running face, and opencode's permission check. */
export interface LanguageContext {
  face(): Face | undefined
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
}

/** The running turn of each opencode session, so an interrupted session stops its turn. */
const running = new Map<string, AbortController>()

/** Stop the turn running for an opencode session; closing its request makes the face cancel the turn. */
export function interrupt(sessionID: string): void {
  running.get(sessionID)?.abort(new Error("The opencode session was interrupted."))
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
 * text, reasoning, and hosted tool calls with their results.
 */
export class AmplifierLanguageModel implements LanguageModelV3 {
  readonly specificationVersion = "v3"
  readonly provider = PROVIDER
  readonly supportedUrls = {}

  constructor(
    readonly modelId: string,
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
    const face = this.context.face()
    if (!face) throw new Error("The Amplifier face is not running. Run: amplifier-opencode doctor")
    const sessionID = header(options.headers, "x-opencode-session-id")
    const effort = options.providerOptions?.[PROVIDER]?.reasoningEffort
    const body = {
      model: this.modelId,
      messages: promptMessages(options.prompt),
      ...(typeof effort === "string" ? { reasoning_effort: effort } : {}),
      ...(this.context.asks ? { approvals: "ask" } : {}),
    }
    const abort = new AbortController()
    const forward = () => abort.abort(options.abortSignal?.reason)
    options.abortSignal?.addEventListener("abort", forward, { once: true })
    if (sessionID) {
      running.get(sessionID)?.abort(new Error("A newer turn started in this opencode session."))
      running.set(sessionID, abort)
    }
    const release = () => {
      options.abortSignal?.removeEventListener("abort", forward)
      if (sessionID && running.get(sessionID) === abort) running.delete(sessionID)
    }

    let response: Response
    try {
      response = await fetch(`${face.baseURL}/amplifier/turns`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${face.token}`,
          "Content-Type": "application/json",
          ...(sessionID ? { "Amplifier-Session-Id": sessionHeader(sessionID) } : {}),
        },
        body: JSON.stringify(body),
        signal: abort.signal,
      })
    } catch (error) {
      release()
      throw error
    }
    if (!response.ok || !response.body) {
      release()
      throw await failure(response)
    }

    const mapper = new TurnMapper(this.modelId)
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
    const read = async (): Promise<LanguageModelV3StreamPart[]> => {
      if (mapper.done) return []
      let result: Awaited<ReturnType<typeof reader.read>>
      try {
        result = await reader.read()
      } catch (error) {
        return mapper.fail(abort.signal.aborted ? new Error("The Amplifier turn was cancelled.") : error)
      }
      if (result.done) return mapper.fail(new Error("The Amplifier face ended the turn without a result."))
      const envelope = result.value
      if (envelope.type === "tool_call") {
        const call = envelope.payload.call as ToolCall | undefined
        if (call?.call_id) calls.set(call.call_id, call)
      } else if (envelope.type === "approval_request" && this.context.asks) {
        // Without `approvals: "ask"` the face's policy already answered.
        void approve((envelope.payload.request ?? {}) as Record<string, unknown>)
      }
      return mapper.map(envelope)
    }

    const stream = new ReadableStream<LanguageModelV3StreamPart>({
      async pull(controller) {
        for (;;) {
          const parts = await read()
          for (const part of parts) controller.enqueue(part)
          if (mapper.done) {
            release()
            controller.close()
            return
          }
          if (parts.length > 0) return
        }
      },
      cancel(reason) {
        release()
        abort.abort(reason)
      },
    })
    return { stream, request: { body } }
  }
}
