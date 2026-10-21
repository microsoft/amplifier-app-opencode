import type { Plugin } from "@opencode/plugin"
import { commandVersion, type Face, log, startFace } from "./face.ts"
import { DEFAULT_MODEL_KEY, defaultModelKey, modelEntries, sessionHeader, titleFrom, withoutReasoning } from "./mapping.ts"
import { readOptions } from "./options.ts"

declare const AMPLIFIER_OPENCODE_VERSION: string

const PROVIDER = "amplifier"
const AGENT = "amplifier"
const SCOPE = { providerID: PROVIDER }

/**
 * Amplifier Agent as an opencode provider and agent. opencode creates one plugin instance per location
 * (working directory), and a session only runs in its own location, so each instance runs one face in its directory.
 */
const plugin: Plugin.Plugin = {
  id: "amplifier",
  setup: async (ctx) => {
    const directory = ctx.location.directory
    const { options, problems } = readOptions()
    for (const problem of problems) log(problem)
    const environment = { ...process.env, ...options.environment }

    const [installed, face] = await Promise.all([
      commandVersion(options.command, environment),
      startFace({ command: options.command, directory, environment, approvals: options.approvals }).catch(
        (error: Error) => error,
      ),
    ])
    if (face instanceof Error) {
      log(face.message)
      throw face
    }
    const stale = installed !== AMPLIFIER_OPENCODE_VERSION
    if (stale) {
      log(
        `plugin ${AMPLIFIER_OPENCODE_VERSION} runs with amplifier-opencode ${installed ?? "(unknown)"}. ` +
          "Run: amplifier-opencode install",
      )
    }

    try {
      await register(ctx, face, options.defaultModel, options.setDefaultAgent, stale)
    } catch (error) {
      face.stop()
      throw error
    }
    return () => face.stop()
  },
}

async function register(
  ctx: Plugin.Context,
  face: Face,
  defaultModel: string | null,
  setDefaultAgent: boolean,
  stale: boolean,
): Promise<void> {
  const entries = modelEntries(face.models)

  await ctx.provider.transform((providers) => {
    providers.update(PROVIDER, (provider) => {
      provider.name = stale ? "Amplifier (run: amplifier-opencode install)" : "Amplifier"
      provider.activation = "enabled"
      provider.package = "@opencode/ai/providers/openai-compatible"
      provider.settings = { baseURL: face.baseURL, apiKey: face.token }
    })
    for (const entry of entries) {
      providers.models.update(PROVIDER, entry.key, (model) => {
        model.modelID = entry.modelID as never
        model.name = entry.name
        // An unknown window keeps opencode from compacting on its own: the face reports usage summed over every
        // model call of a turn, so it overstates the context, and Amplifier compacts the session itself.
        model.limit = { context: 0, output: 0 }
        model.variants = entry.efforts.map((effort) => ({
          id: effort as never,
          settings: { reasoningEffort: effort },
        }))
      })
    }
  })

  if (setDefaultAgent) {
    const key = defaultModelKey(entries, defaultModel)
    if (defaultModel && key === DEFAULT_MODEL_KEY && defaultModel !== DEFAULT_MODEL_KEY) {
      log(`defaultModel ${defaultModel} is not a model the face lists; using the Amplifier default`)
    }
    await ctx.model.transform((models) => models.default.set(PROVIDER, key))
  }

  await ctx.agent.transform((agents) => {
    agents.update(AGENT, (agent) => {
      agent.name = AGENT as never
      agent.description = "Amplifier Agent. Runs its own tools in this directory."
      agent.mode = "primary"
      agent.hidden = false
      agent.model = { providerID: PROVIDER, id: defaultModelKey(entries, defaultModel) } as never
      // Amplifier runs its tools itself; opencode sends none and runs none.
      agent.permissions = [{ action: "*", resource: "*", effect: "deny" }] as never
    })
    if (setDefaultAgent) agents.default(AGENT)
  })

  await ctx.session.hook(
    "model.request",
    (event) => {
      if (event.kind === "primary") event.headers["Amplifier-Session-Id"] = sessionHeader(event.sessionID)
    },
    SCOPE,
  )

  await ctx.session.hook(
    "context",
    (event) => {
      // The face refuses output limits and tools; it runs Amplifier's own tools.
      delete event.options.maxTokens
      event.tools = {}
    },
    SCOPE,
  )

  await ctx.session.hook(
    "http.request",
    async (event) => {
      const text = await event.request.clone().text()
      if (!text.includes('"reasoning_content"')) return
      const headers = new Headers(event.request.headers)
      headers.delete("content-length")
      event.request = new Request(event.request.url, {
        method: event.request.method,
        headers,
        body: JSON.stringify(withoutReasoning(JSON.parse(text))),
        signal: event.request.signal,
      })
    },
    SCOPE,
  )

  await ctx.session.hook(
    "title",
    (event) => {
      const user = event.messages.find((message) => message.role === "user")
      const text = (user?.content ?? []).map((part) => (part.type === "text" ? part.text : "")).join("\n")
      event.result = titleFrom(text) ?? "Amplifier session"
    },
    SCOPE,
  )

  await ctx.session.hook(
    "compaction",
    (event) => {
      // Amplifier compacts the session it holds. Replaying the transcript unchanged keeps the request's user messages
      // in step with the session's turns, so the face continues the session instead of starting over.
      event.result = {
        summary: "Amplifier manages this conversation's context itself.",
        native: { messages: event.messages },
      }
    },
    SCOPE,
  )

  await ctx.session.hook(
    "retry",
    (event) => {
      // A failed turn may already have run tools; resending it could run them again.
      event.decision = { retry: false }
    },
    SCOPE,
  )
}

export default plugin
