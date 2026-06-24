# amplifier-app-opencode

Launch the [opencode](https://opencode.ai) TUI on top of [amplifier-agent](https://github.com/microsoft/amplifier-agent) with one command:

```bash
amplifier-opencode
```

This binary discovers what models your local amplifier-agent serves, writes a working opencode config from that discovery, and launches opencode — every time, no manual config to maintain.

---

## What this gives you

opencode is a fast terminal coding assistant. amplifier-agent is Microsoft's
modular agent framework with a multi-provider OpenAI-compatible HTTP face.

Without this adapter, integrating them requires you to manually mirror
amplifier-agent's `/v1/models` output into your opencode config and re-edit
it whenever your provider mix changes.

With this adapter:

- One command (`amplifier-opencode`) handles the whole flow
- The model list in opencode's picker is **always live** — re-discovered on every launch
- amplifier-agent's server is auto-started in the background if it isn't running
- Drop-in opencode TUI: `/models`, `/connect`, slash commands all work normally
- Includes a `doctor` subcommand that diagnoses any setup issue before you ask

---

## Prerequisites

You need **three** things installed. The first two are owned by other projects;
this README walks through the official install for each so a brand-new machine
can get set up start-to-finish.

### 1. amplifier-agent — the backend server (>= 0.8.0 required)

`amplifier-agent` is the OpenAI-compatible HTTP server this adapter talks to.
Use the official one-line installer — it pulls the latest released binary and
primes the bundle cache so the first run is instant:

```bash
curl -fsSL https://raw.githubusercontent.com/microsoft/amplifier-agent/main/install.sh | bash

# to pin a specific version instead of latest:
#   curl -fsSL https://raw.githubusercontent.com/microsoft/amplifier-agent/main/install.sh | bash -s -- --tag v0.9.0

# ensure ~/.local/bin is on PATH, then verify:
amplifier-agent version --json   # → {"version":"0.9.0","protocolVersion":"0.3.0"}
```

The installer needs [`uv`](https://docs.astral.sh/uv/) and `curl` on PATH and
will tell you exactly what to install if either is missing — it will not
bootstrap them silently.

> **Version requirement: `amplifier-agent >= 0.8.0` is mandatory.** Versions
> below 0.8.0 do not ship the `serve chat-completions` HTTP face, multi-provider
> routing, or the `auth` subcommand — `amplifier-opencode` will fail to spawn
> the server, populate `/v1/models`, or read persisted credentials.
>
> If the version reported is older than 0.8.0, upgrade in place with
> `amplifier-agent update` and re-run `amplifier-opencode doctor` to confirm.

For full install options (source builds, manual `uv tool install --from git+…`,
installer flags) see the
[amplifier-agent README](https://github.com/microsoft/amplifier-agent#install).

### 2. opencode — the TUI

The official one-line installer downloads the platform-native opencode binary:

```bash
curl -fsSL https://opencode.ai/install | bash
```

It places `opencode` in `~/.opencode/bin/` and adds that to your shell PATH.
Open a new terminal (or `source ~/.zshrc`) and verify:

```bash
opencode --version
```

For other install methods (Homebrew, manual download, package managers) see
[opencode.ai/docs/intro](https://opencode.ai/docs/intro).

### 3. amplifier-app-opencode — this adapter

Same uv-tool pattern as amplifier-agent:

```bash
uv tool install --from git+https://github.com/microsoft/amplifier-app-opencode amplifier-app-opencode

# Verify
amplifier-opencode --help
```

(Once published to PyPI, this becomes `uv tool install amplifier-app-opencode`.)

### 4. At least one provider credential

amplifier-agent talks to upstream model APIs (Anthropic, OpenAI, etc.) and
needs credentials for at least one of them. Easiest: set an environment
variable that amplifier-agent already knows about:

```bash
# Pick at least ONE that you have access to:
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENAI_API_KEY="sk-..."
export AZURE_OPENAI_API_KEY="..."
export OLLAMA_HOST="http://localhost:11434"   # if running ollama locally

# To persist across all terminals, add the line to ~/.zshrc (or your shell's rc)
```

Alternative: use `amplifier-agent`'s persistent credential file. Run once and
your key is stored under `~/.amplifier-agent/credentials.json` (mode 0600),
available to every future invocation from any directory:

```bash
amplifier-agent auth set anthropic sk-ant-...
amplifier-agent auth list                       # confirm it's stored
```

The amplifier-agent server uses **env-first** resolution: shell env vars win
over the credentials file, so existing shell-rc workflows continue working
unchanged.

---

## Provider Credentials

amplifier-opencode auto-detects which AI provider you have credentials for
and configures amplifier-agent to serve those providers. Set one or more of
the following environment variables before running `amplifier-opencode launch`:

| Provider | Env var |
|---|---|
| Anthropic (Claude) | `ANTHROPIC_API_KEY` |
| OpenAI (GPT) | `OPENAI_API_KEY` |
| Azure OpenAI | `AZURE_OPENAI_API_KEY` or `AZURE_OPENAI_KEY` |
| Ollama (local models) | `OLLAMA_HOST` |

Run `amplifier-opencode doctor` to see which providers will be available.

### Advanced: custom host_config.json

If you want fine-grained control (custom MCP servers, approval policies,
per-provider config overrides), write your own `host_config.json` and pass
it with `--host-config`:

```bash
amplifier-opencode launch --host-config /path/to/host_config.json
```

See [amplifier-agent's host_config documentation](https://github.com/microsoft/amplifier-agent) for the full schema.

---

## First run

```bash
amplifier-opencode
```

You'll see something like:

```
[1/4] Starting amplifier-agent (port 9099, workspace='opencode')
      amplifier-agent ready at http://127.0.0.1:9099/v1
[2/4] Discovering models via GET http://127.0.0.1:9099/v1/models
      - anthropic   claude-haiku-4-5-20251001            Claude Haiku 4.5
      - anthropic   claude-opus-4-8                      Claude Opus 4.8
      - anthropic   claude-sonnet-4-6                    Claude Sonnet 4.6
[3/4] Wrote /Users/you/.config/opencode/opencode.jsonc  (global config)
[4/4] Configuration complete.

✓ opencode is configured to use Amplifier. Pick how you want to drive it:

    TUI       run `opencode` in any directory
    Desktop   open the opencode desktop app — it picks up the global config
    Headless  opencode run "your prompt here"

To jump straight into the TUI next time: amplifier-opencode launch
```

The bridge is now set up. Run `opencode` in any terminal and pick a model under
the **Amplifier** section — or open the opencode desktop app. The global config
applies everywhere.

If you'd rather have one command that sets up the bridge AND drops you into the
TUI, use `amplifier-opencode launch` instead.

---

## Daily usage

### Refresh the bridge

```bash
amplifier-opencode
```

Default action: check the server, start it if needed, re-discover models, and
update `opencode.jsonc`. Doesn't launch anything — just keeps the bridge in
sync with whatever amplifier-agent is currently serving. Run this any time you
change providers, add credentials, or restart amplifier-agent.

### Set up the bridge AND launch the TUI

```bash
amplifier-opencode launch
```

Same setup flow plus an exec into the opencode TUI. From any directory.

### Pass arguments through to opencode

Anything after `--` is forwarded to opencode:

```bash
amplifier-opencode launch -- run "summarise this codebase"
```

### Write to a project's opencode.json instead of the global config

```bash
cd my-project
amplifier-opencode --project-dir .          # bridge only, into ./opencode.json
amplifier-opencode launch --project-dir .   # bridge + TUI from this directory
```

The generated config lives in `./opencode.json`. opencode walks up from cwd to
find it.

### Point at a different amplifier-agent

```bash
# Server running on another machine or port
amplifier-opencode --base-url http://my-server:9099/v1 --api-key my-token

# Server already running — don't auto-start
amplifier-opencode --no-start
```

---

## Troubleshooting

Run the doctor before you ask anyone for help. It reports on every
prerequisite in one shot:

```bash
amplifier-opencode doctor
```

Output:

```
amplifier-opencode doctor

  [ OK ]  amplifier-agent     amplifier-agent found at /Users/you/.local/bin/amplifier-agent
  [ OK ]  opencode            opencode found at /Users/you/.opencode/bin/opencode
  [ OK ]  server              amplifier-agent server running at http://127.0.0.1:9099/v1
  [ OK ]  credentials         Credentials available (env: anthropic=ANTHROPIC_API_KEY)
  [ OK ]  opencode config     opencode config has provider.amplifier with 3 models
  [ OK ]  live models         Discovered 3 model(s): claude-haiku-4-5-20251001, ...

All required checks passed.
```

Exit code is 0 when everything passes, 1 if any FAIL. Suitable for CI scripts.

Common FAILs and their fix:

| FAIL message | Fix |
|---|---|
| `amplifier-agent not on PATH` | Re-run the install one-liner above, then open a new terminal |
| `opencode not on PATH` | `curl -fsSL https://opencode.ai/install \| bash` (then open a new terminal) |
| `No provider credentials found` | Export `ANTHROPIC_API_KEY`, OR run `amplifier-agent auth set anthropic <key>` |
| `opencode config ... is malformed JSON` | Open `~/.config/opencode/opencode.jsonc`, fix or delete it, retry |

---

## CLI reference

```
amplifier-opencode [GLOBAL OPTIONS] [SUBCOMMAND] [SUBCOMMAND OPTIONS]
```

### Global options

| Flag | Env var | Default | Purpose |
|---|---|---|---|
| `--base-url` | `AMPLIFIER_AGENT_BASE_URL` | `http://127.0.0.1:9099/v1` | amplifier-agent endpoint |
| `--api-key` | `AMPLIFIER_AGENT_API_KEY` | `local-dev-secret` | wire-level bearer token |

### `launch` (default)

Discover models, write opencode.json, exec opencode. Run when no subcommand is given.

| Flag | Env var | Default | Purpose |
|---|---|---|---|
| `--workspace` | `AMPLIFIER_AGENT_WORKSPACE` | `opencode` | Server-side workspace name (only used when starting the server) |
| `--host-config` | `AMPLIFIER_AGENT_HOST_CONFIG` | — | Path to a host_config.json to use verbatim; overrides auto-generated config (only used when starting the server) |
| `--project-dir` | — | (use global) | Write opencode.json into this directory instead of global config |
| `--no-start` | — | false | Don't auto-start amplifier-agent; require server already up |
| `--no-launch` | — | false | Don't exec opencode; just write the config |
| `--amplifier-agent-bin` | `AMPLIFIER_AGENT_BIN` | autodetect | Override the amplifier-agent binary path |
| `--provider-id` | — | `amplifier` | Provider ID under `provider.<id>` |
| `OPENCODE_ARGS...` (after `--`) | — | — | Pass-through to opencode |

### `doctor`

Run all prerequisite checks. No flags; honours the global `--base-url` and `--api-key`.

---

## Where things live

| Path | What |
|---|---|
| `<project-dir>/opencode.json` OR `~/.config/opencode/opencode.jsonc` | Generated config (overwritten on every launch) |
| `/tmp/amplifier-agent.log` | stdout/stderr from the spawned amplifier-agent process |
| `/tmp/amplifier-opencode-agent.pid` | PID of the spawned amplifier-agent |
| `~/.amplifier-agent/state/workspaces/` | amplifier-agent's session storage |
| `~/.amplifier-agent/credentials.json` | Persistent credentials (mode 0600) — set via `amplifier-agent auth` |
| `~/.local/share/opencode/log/opencode.log` | opencode's own log file |

---

## Design notes

opencode's documented contract for custom OpenAI-compatible providers (the
["Atomic Chat" pattern](https://opencode.ai/docs/providers/#atomic-chat))
requires a **static** `models` block listing every model ID the upstream
server serves. opencode does not auto-fetch `/v1/models` at runtime for
custom-config providers.

Rather than maintain the model list by hand or attempt fragile runtime
monkey-patches against opencode's internals, this binary **materialises the
static config from the live `/v1/models` response before opencode launches**.
Every invocation re-syncs. The model list is therefore always live; the
"static" nature of opencode's config is just an implementation detail.

opencode itself is unmodified. The adapter lives entirely outside both
upstream projects — no plugins, no patches, no npm packages, no JavaScript.

---

## Contributing

This project welcomes contributions and suggestions. Most contributions
require you to agree to a Contributor License Agreement (CLA) declaring that
you have the right to, and actually do, grant us the rights to use your
contribution. For details, visit [https://cla.opensource.microsoft.com](https://cla.opensource.microsoft.com).

When you submit a pull request, a CLA bot will automatically determine
whether you need to provide a CLA and decorate the PR appropriately (e.g.,
status check, comment). Simply follow the instructions provided by the bot.
You will only need to do this once across all repos using our CLA.

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/).
For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/)
or contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or comments.

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft trademarks or logos is subject to and must follow [Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/en-us/legal/intellectualproperty/trademarks/usage/general). Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship. Any use of third-party trademarks or logos are subject to those third-party's policies.

## License

[MIT](LICENSE)
