# amplifier-app-opencode

Launch [opencode](https://opencode.ai) with auto-discovered [amplifier-agent](https://github.com/microsoft/amplifier-agent) models.

A single binary that wraps the entire integration flow into one command:

```
amplifier-opencode
```

That's it.

## What it does on every invocation

```
┌────────────────────────────────────────────────────────────────────────┐
│ 1. CHECK     -- is amplifier-agent's chat-completions server up?       │
│                 GET {base-url}/models                                  │
│                                                                        │
│ 2. START     -- if not, spawn ``amplifier-agent serve chat-completions``│
│                 in the background. Wait until healthy.                 │
│                                                                        │
│ 3. DISCOVER  -- GET /v1/models. amplifier-agent's response includes    │
│                 metadata (display_name, limit, cost, capabilities,    │
│                 reasoning) which we translate into opencode's         │
│                 provider block shape.                                  │
│                                                                        │
│ 4. WRITE     -- materialise ``opencode.json`` in the project           │
│                 directory with the discovered models under            │
│                 ``provider.amplifier``. Preserves other keys.         │
│                                                                        │
│ 5. EXEC      -- replace this process with ``opencode``. You land in   │
│                 the TUI with a live model list ready to pick.         │
└────────────────────────────────────────────────────────────────────────┘
```

No opencode plugin. No monkey-patch. No JavaScript. The "dynamic discovery"
the user wants happens **before opencode starts** — so opencode itself only
ever sees a static config (which is the only shape it can accept for custom
OpenAI-compatible providers).

## Why this design

[opencode's documented contract for custom providers](https://opencode.ai/docs/providers/#atomic-chat) ("Atomic Chat" pattern) requires a static `models` block in `opencode.json` that mirrors what `GET /v1/models` returns on the upstream server. The plugin system has hooks for dynamic discovery, but they're gated on the provider existing in [models.dev](https://models.dev) — which excludes any custom server like amplifier-agent.

Rather than fight that constraint with monkey-patches that break on every opencode version bump, we make peace with it: we re-generate the static config **once per launch** from the live server. Drift is impossible because every invocation re-reads the source of truth.

## Install

```bash
uv tool install --from /path/to/amplifier-app-opencode amplifier-app-opencode
```

or, for local development:

```bash
cd amplifier-app-opencode
uv pip install -e .
```

Prerequisites:

- Python 3.11+ (uv handles this for you)
- [opencode](https://opencode.ai) installed and on PATH
- [amplifier-agent](https://github.com/microsoft/amplifier-agent) installed and on PATH (only required if you want the wrapper to auto-start the server)

## Usage

### Most common

```bash
cd your-project/
amplifier-opencode
```

Output:

```
[1/4] Starting amplifier-agent (port 9099, workspace='opencode')
      amplifier-agent ready at http://127.0.0.1:9099/v1
[2/4] Discovering models via GET http://127.0.0.1:9099/v1/models
      - anthropic   claude-haiku-4-5-20251001            Claude Haiku 4.5
      - anthropic   claude-opus-4-8                      Claude Opus 4.8
      - anthropic   claude-sonnet-4-6                    Claude Sonnet 4.6
[3/4] Wrote /your/cwd/opencode.json
[4/4] Launching opencode in /your/cwd
[opencode TUI opens]
```

The model picker (`/models` in the TUI) will show the **Amplifier** section with the three Claude models.

### With a custom host_config

```bash
amplifier-opencode --config /path/to/host_config.json
```

The `host_config.json` is forwarded to amplifier-agent. Anything it changes about the served model list (different provider, additional MCP servers, custom skills) is automatically reflected in opencode's picker next launch.

### Server already running elsewhere

```bash
amplifier-opencode --base-url http://my-server:9099/v1 --no-start
```

Skips the start step; fails loudly if `/v1/models` isn't reachable. Useful if amplifier-agent runs in Docker, on a different machine, or under a process manager.

### Generate config without launching

```bash
amplifier-opencode --no-launch
```

Useful for CI / pre-flight checks. Discovers models, writes `opencode.json`, exits.

### Pass-through to opencode

```bash
amplifier-opencode -- run "what's the weather?"
```

Anything after `--` is handed to `opencode` unchanged.

## CLI reference

| Flag | Env var | Default | Purpose |
|---|---|---|---|
| `--base-url` | `AMPLIFIER_AGENT_BASE_URL` | `http://127.0.0.1:9099/v1` | amplifier-agent endpoint |
| `--api-key` | `AMPLIFIER_AGENT_API_KEY` | `local-dev-secret` | Bearer token |
| `--workspace` | `AMPLIFIER_AGENT_WORKSPACE` | `opencode` | Server-side workspace name (when starting) |
| `--config` | `AMPLIFIER_AGENT_HOST_CONFIG` | none | host_config.json path (when starting) |
| `--project-dir` | — | cwd | Where to write opencode.json |
| `--no-start` | — | false | Don't auto-start; require server already up |
| `--no-launch` | — | false | Don't exec opencode; just write the config |
| `--amplifier-agent-bin` | `AMPLIFIER_AGENT_BIN` | `which amplifier-agent` | Override binary path |
| `--provider-id` | — | `amplifier` | Provider ID under `provider.<id>` |

All flags can also be set via environment variables. CLI flags take precedence.

## Generated opencode.json

A representative result:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "amplifier": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Amplifier",
      "options": {
        "baseURL": "http://127.0.0.1:9099/v1",
        "apiKey": "local-dev-secret"
      },
      "models": {
        "claude-haiku-4-5-20251001": {
          "name": "Claude Haiku 4.5",
          "reasoning": true,
          "limit": { "context": 200000, "output": 64000 }
        },
        "claude-opus-4-8": {
          "name": "Claude Opus 4.8",
          "reasoning": true,
          "limit": { "context": 1000000, "output": 128000 }
        },
        "claude-sonnet-4-6": {
          "name": "Claude Sonnet 4.6",
          "reasoning": true,
          "limit": { "context": 1000000, "output": 128000 }
        }
      }
    }
  }
}
```

Any other keys in pre-existing `opencode.json` are preserved.

## Where things live

| Path | What |
|---|---|
| `<project-dir>/opencode.json` | Generated config (overwritten on every launch) |
| `/tmp/amplifier-agent.log` | stdout/stderr from the spawned amplifier-agent process |
| `/tmp/amplifier-opencode-agent.pid` | PID of the spawned amplifier-agent (so re-runs detect it) |
| `~/.amplifier-agent/state/workspaces/` | amplifier-agent's session storage |
| `~/.local/share/opencode/log/opencode.log` | opencode's own log file |

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `amplifier-agent binary not found in PATH` | amplifier-agent not installed | `uv tool install amplifier-agent` |
| `opencode binary not found in PATH` | opencode not installed | `curl -fsSL https://opencode.ai/install \| bash` |
| `amplifier-agent did not become ready within 30s` | host_config error or port collision | Check `/tmp/amplifier-agent.log` |
| `/v1/models returned 0 models` | No provider credentials in env, or provider modules not installed | Check amplifier-agent boot log: `Skipping provider 'openai' -- module not installed` etc. |
| Amplifier section missing in TUI | Generated `opencode.json` not loaded by opencode | Confirm cwd contains the generated file. opencode walks up from cwd looking for the project root — git-init the dir if it's not already a project. |

## License

MIT.
