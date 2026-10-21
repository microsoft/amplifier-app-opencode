import { createHash } from "node:crypto"
import type { Plugin } from "@opencode/plugin"
import { type Connection, faceEnvironment, INTEGRATIONS } from "./credentials.ts"
import { commandVersion, type Face, log, startFace } from "./face.ts"
import {
  DEFAULT_MODEL_KEY,
  defaultModelKey,
  type ModelEntry,
  modelEntries,
  sessionHeader,
  titleFrom,
} from "./mapping.ts"
import { type Options, readOptions } from "./options.ts"
import { CARRIER_COST, usageRewriter } from "./usage.ts"

declare const AMPLIFIER_OPENCODE_VERSION: string

const PROVIDER = "amplifier"
const AGENT = "amplifier"
const SCOPE = { providerID: PROVIDER }

/** The face the registrations show, if one runs, and the environment it was started with. */
interface Live {
  face: Face | undefined
  entries: ModelEntry[]
  fingerprint: string
}

/**
 * Amplifier Agent as an opencode provider and agent. opencode creates one plugin instance per location
 * (working directory), and a session only runs in its own location, so each instance runs one face in its directory.
 * The face runs with the provider credentials of opencode's connections, and restarts when they change.
 */
const plugin: Plugin.Plugin = {
  id: "amplifier",
  setup: async (ctx) => {
    const directory = ctx.location.directory
    const { options, problems } = readOptions()
    for (const problem of problems) log(problem)
    const resolved = await environment(ctx, options)
    logSources(directory, resolved.sources)

    const [installed, face] = await Promise.all([
      commandVersion(options.command, resolved.environment),
      startFace({
        command: options.command,
        directory,
        environment: resolved.environment,
        approvals: options.approvals,
      }).catch((error: Error) => error),
    ])
    // Without a face the provider and agent stay hidden; connecting a provider in opencode starts it.
    if (face instanceof Error) log(face.message)
    const stale = installed !== AMPLIFIER_OPENCODE_VERSION
    if (stale) {
      log(
        `plugin ${AMPLIFIER_OPENCODE_VERSION} runs with amplifier-opencode ${installed ?? "(unknown)"}. ` +
          "Run: amplifier-opencode install",
      )
    }

    const live: Live = { face: undefined, entries: [], fingerprint: resolved.fingerprint }
    if (!(face instanceof Error)) show(live, face, options)
    try {
      await register(ctx, live, options.defaultModel, options.setDefaultAgent, stale)
    } catch (error) {
      live.face?.stop()
      throw error
    }
    const watching = new AbortController()
    watchConnections(ctx, live, options, directory, watching.signal)
    return () => {
      watching.abort()
      live.face?.stop()
    }
  },
}

/** The face's environment, its sources, and a fingerprint that changes when the environment does. */
async function environment(ctx: Plugin.Context, options: Options) {
  const resolved = faceEnvironment(process.env, await connections(ctx), options)
  const fingerprint = createHash("sha256")
    .update(JSON.stringify(Object.entries(resolved.environment).sort(([a], [b]) => a.localeCompare(b))))
    .digest("hex")
  return { ...resolved, fingerprint }
}

/** The active opencode connection of each integration Amplifier can use, resolved to its credential. */
async function connections(ctx: Plugin.Context): Promise<Connection[]> {
  const found: Connection[] = []
  for (const integrationID of INTEGRATIONS) {
    try {
      const connection = await ctx.integration.connection.active(integrationID)
      if (!connection) continue
      // Resolving can refresh a sign-in, so only the kind of credential Amplifier uses is resolved.
      const usable = integrationID === "github-copilot" ? "oauth" : "key"
      if (connection.type === "credential" && connection.method !== usable) continue
      const credential = await ctx.integration.connection.resolve(connection)
      if (credential) found.push({ integrationID, credential })
    } catch (error) {
      log(`could not read the opencode ${integrationID} connection: ${(error as Error).message}`)
    }
  }
  return found
}

function logSources(directory: string, sources: Readonly<Record<string, string>>): void {
  const names = Object.entries(sources).map(([name, source]) => `${name} from ${source}`)
  log(`face environment for ${directory}: ${names.join(", ") || "no provider credentials"}`)
}

function show(live: Live, face: Face, options: Options): void {
  live.face = face
  live.entries = modelEntries(face.models)
  const { defaultModel } = options
  if (!defaultModel || defaultModel === DEFAULT_MODEL_KEY) return
  if (defaultModelKey(live.entries, defaultModel) === DEFAULT_MODEL_KEY) {
    log(`defaultModel ${defaultModel} is not a model the face lists; using the Amplifier default`)
  }
}

/** Restart the face when opencode's connections change the environment it runs with, and show its models. */
function watchConnections(
  ctx: Plugin.Context,
  live: Live,
  options: Options,
  directory: string,
  signal: AbortSignal,
): void {
  let queue = Promise.resolve()
  const refresh = () => {
    queue = queue
      .then(async () => {
        if (signal.aborted) return
        const resolved = await environment(ctx, options)
        if (signal.aborted || resolved.fingerprint === live.fingerprint) return
        log(`opencode connections changed; restarting the face for ${directory}`)
        logSources(directory, resolved.sources)
        const face = await startFace({
          command: options.command,
          directory,
          environment: resolved.environment,
          approvals: options.approvals,
        })
        if (signal.aborted) {
          face.stop()
          return
        }
        const previous = live.face
        show(live, face, options)
        live.fingerprint = resolved.fingerprint
        await ctx.provider.reload()
        await ctx.model.reload()
        await ctx.agent.reload()
        previous?.stop()
      })
      .catch((error: Error) =>
        log(`${live.face ? "kept the running face" : "no face runs"} for ${directory}: ${error.message}`),
      )
  }

  void (async () => {
    try {
      for await (const event of ctx.event.subscribe({ signal })) {
        if (event.type === "credential.updated" || event.type === "credential.switched") refresh()
      }
    } catch (error) {
      if (!signal.aborted) log(`stopped following opencode connections for ${directory}: ${(error as Error).message}`)
    }
  })()
  // A connection made while the face started has no event left to announce it.
  refresh()
}

async function register(
  ctx: Plugin.Context,
  live: Live,
  defaultModel: string | null,
  setDefaultAgent: boolean,
  stale: boolean,
): Promise<void> {
  await ctx.provider.transform((providers) => {
    const face = live.face
    if (!face) return
    providers.update(PROVIDER, (provider) => {
      provider.name = stale ? "Amplifier (run: amplifier-opencode install)" : "Amplifier"
      provider.activation = "enabled"
      provider.package = "@opencode/ai/providers/openai-compatible"
      provider.settings = { baseURL: face.baseURL, apiKey: face.token }
    })
    for (const entry of live.entries) {
      providers.models.update(PROVIDER, entry.key, (model) => {
        model.modelID = entry.modelID as never
        model.name = entry.name
        // The face takes no output limit. The usage rewrite makes the last message's tokens the context size and
        // carries the turn's cost in cache writes, which only this cost prices.
        model.limit = { context: entry.contextWindow ?? 0, output: 0 }
        model.cost = CARRIER_COST as never
        model.variants = entry.efforts.map((effort) => ({
          id: effort as never,
          settings: { reasoningEffort: effort },
        }))
      })
    }
  })

  if (setDefaultAgent) {
    await ctx.model.transform((models) => {
      if (live.face) models.default.set(PROVIDER, defaultModelKey(live.entries, defaultModel))
    })
  }

  await ctx.agent.transform((agents) => {
    if (!live.face) return
    agents.update(AGENT, (agent) => {
      agent.name = AGENT as never
      agent.description = "Amplifier Agent. Runs its own tools in this directory."
      agent.mode = "primary"
      agent.hidden = false
      agent.model = { providerID: PROVIDER, id: defaultModelKey(live.entries, defaultModel) } as never
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
    "http.response",
    (event) => {
      const { response } = event
      if (!response.body || !response.headers.get("content-type")?.startsWith("text/event-stream")) return
      const headers = new Headers(response.headers)
      headers.delete("content-length")
      event.response = new Response(response.body.pipeThrough(usageRewriter()), {
        status: response.status,
        statusText: response.statusText,
        headers,
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
