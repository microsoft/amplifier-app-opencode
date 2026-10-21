import { createHash } from "node:crypto"
import type { Plugin } from "@opencode/plugin"
import { type Connection, faceEnvironment, INTEGRATIONS } from "./credentials.ts"
import { commandVersion, type Face, log, startFace } from "./face.ts"
import { DEFAULT_MODEL_KEY, defaultModelKey, type ModelEntry, modelEntries, titleFrom } from "./mapping.ts"
import { AmplifierLanguageModel, interrupt, PROVIDER } from "./language.ts"
import { faceApprovals, type Options, readOptions } from "./options.ts"
import * as proxy from "./proxy.ts"
import { PROXY_TOOLS } from "./proxy.ts"
import { CARRIER_COST } from "./usage.ts"
import { UsageBook, USAGE_RPC } from "./usage-book.ts"

declare const AMPLIFIER_OPENCODE_VERSION: string

const AGENT = "amplifier"
/**
 * The `amplifier` provider's AI SDK package: this file, which opencode imports for its `create` export. A package
 * name would make opencode install it from npm.
 */
const PACKAGE = `aisdk:${import.meta.url}`

/** The AI SDK provider opencode creates from PACKAGE. The `language` hook supplies each model. */
export function createAmplifier(): Record<string, never> {
  return {}
}
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
        approvals: faceApprovals(options.approvals),
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
      await register(ctx, live, options, stale)
    } catch (error) {
      live.face?.stop()
      throw error
    }
    const watching = new AbortController()
    watchEvents(ctx, live, options, directory, watching.signal)
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

/**
 * Restart the face when opencode's connections change the environment it runs with, and show its models. Stop the
 * Amplifier turn of a session opencode interrupts.
 */
function watchEvents(
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
          approvals: faceApprovals(options.approvals),
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
        else if (event.type === "session.execution.interrupted") interrupt(event.data.sessionID)
      }
    } catch (error) {
      if (!signal.aborted) log(`stopped following opencode connections for ${directory}: ${(error as Error).message}`)
    }
  })()
  // A connection made while the face started has no event left to announce it.
  refresh()
}

/**
 * The amplifier agent's permissions. opencode's defaults decide Amplifier's effects, which ask as opencode's own tools
 * do (`shell`, `edit`, `read`, `webfetch`, `mcp`, or the tool's name), and the user's permission rules follow these.
 * opencode's own tools are never offered to the model; the ones whose actions Amplifier's effects do not share are
 * denied as well. `glob` and `grep` stay allowed: opencode shows Amplifier's searches through them, and a tool whose
 * action is denied outright is not available at all. Amplifier asks for the effects those tools show before they run.
 */
const PERMISSIONS = [
  { action: "*", resource: "*", effect: "allow" },
  { action: "read", resource: "*.env", effect: "ask" },
  { action: "read", resource: "*.env.*", effect: "ask" },
  { action: "read", resource: "*.env.example", effect: "allow" },
  ...["websearch", "question", "todowrite", "subagent", "skill", "execute", "opencode_*"].map(
    (action) => ({ action, resource: "*", effect: "deny" }),
  ),
]

async function register(ctx: Plugin.Context, live: Live, options: Options, stale: boolean): Promise<void> {
  const { defaultModel, setDefaultAgent } = options
  await ctx.provider.transform((providers) => {
    const face = live.face
    if (!face) return
    providers.update(PROVIDER, (provider) => {
      provider.name = stale ? "Amplifier (run: amplifier-opencode install)" : "Amplifier"
      provider.activation = "enabled"
      provider.package = PACKAGE
    })
    for (const entry of live.entries) {
      providers.models.update(PROVIDER, entry.key, (model) => {
        model.modelID = entry.modelID as never
        model.name = entry.name
        // Amplifier sets its own output limit. A turn's usage makes the last message's tokens the context size and
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
      // A turn that shows proxied tool calls spans one step per batch of them; a step limit would cut the turn.
      agent.steps = undefined as never
      // opencode adds the user's permission rules after these.
      const rules = agent.permissions as readonly { action: string; resource: string; effect: string }[]
      const defaults = rules.findIndex((rule, index) => JSON.stringify(rule) !== JSON.stringify(PERMISSIONS[index]))
      agent.permissions = [...PERMISSIONS, ...rules.slice(defaults < 0 ? rules.length : defaults)] as never
    })
    if (setDefaultAgent) agents.default(AGENT)
  })

  await ctx.aisdk.hook(
    "sdk",
    (event) => {
      event.sdk ??= {}
    },
    SCOPE,
  )

  const asks = options.approvals === "opencode"
  const book = new UsageBook()
  let usageEvents: { emit(name: "usage", data: never): Promise<void> } | undefined
  try {
    const registration = await ctx.rpc.register(USAGE_RPC as never, {
      usage: async (input: { sessionID: string }) => book.get(input.sessionID),
    } as never)
    usageEvents = (registration as unknown as { events: typeof usageEvents }).events
  } catch (error) {
    log(`could not offer Amplifier usage to the TUI: ${(error as Error).message}`)
  }

  await ctx.tool.transform((tools) => {
    for (const id of PROXY_TOOLS) {
      tools.update(id, (tool) => {
        const original = tool.execute
        tool.execute = async (input, context) => {
          // A call the language model registered is Amplifier's: it already ran, so only its result is shown.
          if (!proxy.registered(context.id)) return original(input, context)
          return (await proxy.wait(context.id, context.signal)) as never
        }
      })
    }
  })
  await ctx.aisdk.hook(
    "language",
    (event) => {
      event.language = new AmplifierLanguageModel(String(event.model.modelID ?? event.model.id), {
        face: () => live.face,
        asks,
        assert: (input) => ctx.permission.assert({ ...input, sessionID: input.sessionID as never }),
        observe: (sessionID, envelope) => {
          const usage = book.observe(sessionID, envelope)
          if (usage) usageEvents?.emit("usage", usage as never).catch(() => {})
        },
      })
    },
    SCOPE,
  )

  await ctx.session.hook(
    "context",
    (event) => {
      // Amplifier runs its own tools. Only the tools that show Amplifier's calls stay, so their calls can run. opencode
      // offers `edit` or `patch` by the model's name; Amplifier may call either, so both stay. A definition opencode
      // removed comes back by its name, and Amplifier never reads it.
      const tools = event.tools as Record<string, { description: string; input: unknown }>
      event.tools = Object.fromEntries(
        PROXY_TOOLS.map((name) => [name, tools[name] ?? { description: name, input: { type: "object" } }]),
      ) as never
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
