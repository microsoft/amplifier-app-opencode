import { createHash } from "node:crypto"

/** One entry of the face's `GET /v1/models`. */
export interface FaceModel {
  readonly id: string
  readonly display_name?: string
  readonly context_window?: number
  readonly max_output_tokens?: number
  readonly reasoning_efforts?: readonly string[]
}

export interface ModelEntry {
  /** Key in opencode's model list for the `amplifier` provider. */
  readonly key: string
  /** Name the face accepts in `model`. */
  readonly modelID: string
  readonly name: string
  readonly contextWindow?: number
  readonly efforts: readonly string[]
}

export const DEFAULT_MODEL_KEY = "default"

/** The `Amplifier-Session-Id` value for an opencode session. */
export function sessionHeader(sessionID: string): string {
  return `oc-${createHash("sha256").update(sessionID).digest("hex").slice(0, 40)}`
}

/** opencode's model key for a face model name: `<provider>/<model>` becomes `<provider>-<model>`. */
export function modelKey(id: string): string {
  return id.replaceAll("/", "-")
}

/** opencode model entries for the face's models. The configured agent's name, without a `/`, is keyed `default`. */
export function modelEntries(models: readonly FaceModel[]): ModelEntry[] {
  return models.map((model) =>
    !model.id.includes("/")
      ? { key: DEFAULT_MODEL_KEY, modelID: model.id, name: "Amplifier default", efforts: [] }
      : {
          key: modelKey(model.id),
          modelID: model.id,
          name: model.display_name ?? model.id,
          contextWindow: model.context_window,
          efforts: model.reasoning_efforts ?? [],
        },
  )
}

/** The model key for the `defaultModel` option, or `default` when it names no listed model. */
export function defaultModelKey(entries: readonly ModelEntry[], defaultModel: string | null | undefined): string {
  if (!defaultModel) return DEFAULT_MODEL_KEY
  const match = entries.find((entry) => entry.modelID === defaultModel || entry.key === defaultModel)
  return match?.key ?? DEFAULT_MODEL_KEY
}

/** A session title from the first line of the first user message, at most 60 characters. */
export function titleFrom(text: string): string | undefined {
  const line = text
    .split("\n")
    .map((part) => part.trim())
    .find((part) => part.length > 0)
  if (!line) return undefined
  return line.length <= 60 ? line : `${line.slice(0, 57).trimEnd()}...`
}

/** The chat-completions body without the assistant `reasoning_content` the face does not accept. */
export function withoutReasoning(body: unknown): unknown {
  if (typeof body !== "object" || body === null || !("messages" in body) || !Array.isArray(body.messages)) return body
  return {
    ...body,
    messages: body.messages.map((message: unknown) => {
      if (typeof message !== "object" || message === null || !("reasoning_content" in message)) return message
      const { reasoning_content: _, ...rest } = message
      return rest
    }),
  }
}
