// amplifier-opencode-plugin 0.25.0
import { Plugin } from "@opencode/plugin/tui"
import { createSignal, For, Show } from "solid-js"

interface Row {
  provider: string
  model: string
  tokensIn: number | null
  tokensOut: number | null
  cacheRead: number | null
  cacheWrite: number | null
  costUSD: number | null
}

interface Usage {
  sessionID: string
  turn: Row[]
  session: Row[]
  compactions: number
}

// The server part's RPC, by its id; the schemas are the server's to enforce.
const USAGE_RPC = {
  id: "amplifier",
  methods: { usage: { input: { type: "object" }, output: { type: "object" } } },
  events: { usage: { schema: { type: "object" } } },
} as const

const tokens = (value: number | null) => (value === null ? "?" : value.toLocaleString("en-US"))
const money = (value: number | null) => (value === null ? "$?" : `$${value.toFixed(4)}`)

function Rows(props: { label: string; rows: Row[]; muted: unknown }) {
  return (
    <For each={props.rows}>
      {(row) => (
        <box>
          <text fg={props.muted as never}>
            {props.label} {money(row.costUSD)}
          </text>
          <text fg={props.muted as never}>
            {"  "}
            {tokens(row.tokensIn)} in · {tokens(row.tokensOut)} out
          </text>
          <text fg={props.muted as never}>
            {"  "}
            {tokens(row.cacheRead)} cache read · {tokens(row.cacheWrite)} write
          </text>
        </box>
      )}
    </For>
  )
}

export default Plugin.define({
  id: "amplifier.usage",
  setup(context) {
    const rpc = context.client.rpc(USAGE_RPC as never) as unknown as {
      usage(input: { sessionID: string }, options?: { location?: unknown }): Promise<Usage>
      events: { on(name: "usage", handler: (event: { data: Usage }) => void): () => void }
    }
    const [usage, setUsage] = createSignal<Record<string, Usage>>({})
    const store = (value: Usage) => setUsage((all) => ({ ...all, [value.sessionID]: value }))
    const unsubscribe = rpc.events.on("usage", (event) => store(event.data))
    const asked = new Set<string>()
    const ask = (sessionID: string) => {
      if (asked.has(sessionID)) return
      asked.add(sessionID)
      const location = context.data.session.get(sessionID)?.location
      rpc
        .usage({ sessionID }, location ? { location } : undefined)
        .then((value) => {
          if (!usage()[sessionID]) store(value)
        })
        .catch(() => {})
    }

    const unslot = context.ui.slot({
      append: "sidebar.content",
      render: (props) => {
        ask(props.sessionID)
        const theme = context.theme
        const current = () => usage()[props.sessionID]
        // One block per provider and model the session used.
        const models = () => {
          const value = current()
          if (!value) return []
          const keys = [...new Set([...value.session, ...value.turn].map((row) => `${row.provider}/${row.model}`))]
          return keys.map((key) => ({
            key,
            turn: value.turn.filter((row) => `${row.provider}/${row.model}` === key),
            session: value.session.filter((row) => `${row.provider}/${row.model}` === key),
          }))
        }
        return (
          <Show when={models().length > 0}>
            <box>
              <text fg={theme.text.base}>
                <b>Amplifier usage</b>
              </text>
              <For each={models()}>
                {(model) => (
                  <box>
                    <text fg={theme.text.base}>{model.key}</text>
                    <Rows label="Turn" rows={model.turn} muted={theme.text.muted} />
                    <Rows label="Session" rows={model.session} muted={theme.text.muted} />
                  </box>
                )}
              </For>
              <text fg={theme.text.muted}>Compactions: {current()?.compactions ?? 0}</text>
            </box>
          </Show>
        )
      },
    })
    return () => {
      unslot()
      unsubscribe()
    }
  },
})
