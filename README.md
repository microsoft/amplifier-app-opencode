# amplifier-app-opencode

Run the [opencode](https://opencode.ai) coding TUI on top of your local
[amplifier-agent](https://github.com/microsoft/amplifier-agent) — one command to
install, one command to start coding:

```bash
# 1. install (also installs everything it needs)
curl -fsSL https://raw.githubusercontent.com/microsoft/amplifier-app-opencode/main/install.sh | bash

# 2. set up the connection and jump into opencode
amplifier-opencode launch
```

amplifier-opencode discovers which models your amplifier-agent serves, writes a
working opencode config from that discovery, and opens opencode — re-synced
every time, with no config to maintain by hand.

---

## What this gives you

opencode is a fast terminal coding assistant. amplifier-agent is Microsoft's
modular agent framework with a multi-provider, OpenAI-compatible HTTP face.
This adapter wires the two together so you don't have to.

- **Self-managing:** installs and updates amplifier-agent and opencode for you,
  and walks you through connecting a model provider on first run — no "go
  install X yourself" detours
- **Always-live models:** opencode's model picker is re-discovered on every run,
  so it matches whatever amplifier-agent is currently serving
- **Zero-config bridge:** amplifier-agent's server is auto-started in the
  background if it isn't already running
- **Drop-in opencode:** `/models`, `/connect`, and all slash commands work normally
- **Built-in `doctor`:** diagnoses any setup issue in one command

> **Two commands to know:** `amplifier-opencode launch` sets up the connection
> **and** drops you into the opencode TUI. Plain `amplifier-opencode` just
> refreshes the connection (starts the server, re-discovers models, rewrites the
> config) without launching — handy after you change providers or add a key.

---

## Install (one command)

You install one thing and run one thing. The rest of the stack heals itself.

```bash
curl -fsSL https://raw.githubusercontent.com/microsoft/amplifier-app-opencode/main/install.sh | bash
```

Prefer to review first (recommended for any `curl | bash`):

```bash
curl -fsSL https://raw.githubusercontent.com/microsoft/amplifier-app-opencode/main/install.sh -o install.sh
less install.sh
bash install.sh
```

That installs [`uv`](https://docs.astral.sh/uv/) (if missing) and the
`amplifier-opencode` CLI. Then just run:

```bash
amplifier-opencode
```

On first launch amplifier-opencode **self-heals the rest of the stack**:

- Installs [`amplifier-agent`](https://github.com/microsoft/amplifier-agent)
  (the backend server) if missing, and force-updates it if it is below the
  required minimum — no "go install X yourself" dead-ends.
- Installs [`opencode`](https://opencode.ai) (the TUI) if missing, using the
  best method for your OS (curl installer, Homebrew, npm, scoop, or choco).
- If no provider credentials are configured, it **walks you through connecting
  one** (Anthropic, OpenAI, Azure, or Ollama) and stores it via amplifier-agent.
- Anything already installed and healthy is left untouched.

Want just the setup without launching? Run `amplifier-opencode setup`.

Keeping everything current is also one command:

```bash
amplifier-opencode update              # update amplifier-opencode + amplifier-agent + opencode
amplifier-opencode update --no-opencode  # update the amplifier pieces, keep opencode pinned
```

**Non-interactive / CI:** pass `--yes` (globally) to auto-install without
prompts, e.g. `amplifier-opencode --yes setup`. Pass `--no-bootstrap` to skip
the self-healing preflight entirely and assume the environment is ready.

**Platform support:** macOS, Linux, and WSL are fully supported. On **native
Windows**, run amplifier-opencode inside **WSL** — opencode's own docs
recommend WSL, and the self-healing installers are bash-based. If a component
can't be auto-installed on your platform, amplifier-opencode tells you exactly
what to do instead of failing silently.

---

## Manual install (advanced)

**Most people can skip this** — the one-command install above handles it all.
This section is for anyone who wants to install each component by hand.

<details>
<summary>Show the full step-by-step manual install</summary>

You need **a few system tools** and **three Amplifier components** installed.
The steps below walk through the official install for each so a brand-new
machine can get set up start-to-finish.

### 0. System tools — git, curl

Most macOS installs already have these via Xcode Command Line Tools. On a
fresh Linux container you'll need:

```bash
# Ubuntu/Debian
sudo apt-get update && sudo apt-get install -y git curl

# Fedora/RHEL
sudo dnf install -y git curl

# Arch
sudo pacman -S --noconfirm git curl
```

Why: `git` is required because amplifier-agent and amplifier-app-opencode are
installed via `git+https://...` URLs (neither is on PyPI yet). `curl` is
required by the uv and opencode one-line installers.

### 1. amplifier-agent — the backend server (>= 0.9.1 required)

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

> **Version requirement: `amplifier-agent >= 0.9.1` is mandatory.** Older
> versions lack the pieces amplifier-opencode depends on (the `serve
> chat-completions` HTTP face, multi-provider routing, and the `auth`
> subcommand). If you use the one-command install, amplifier-opencode detects a
> too-old amplifier-agent and force-updates it for you; if you're installing by
> hand and see an older version, upgrade with `amplifier-agent update` and
> re-run `amplifier-opencode doctor` to confirm.

For full install options (source builds, manual `uv tool install --from git+…`,
installer flags) see the
[amplifier-agent README](https://github.com/microsoft/amplifier-agent#install).

### 2. opencode — the TUI

The official one-line installer downloads the platform-native opencode binary:

```bash
curl -fsSL https://opencode.ai/install | bash
```

It places `opencode` in `~/.opencode/bin/` and appends that directory to
your shell PATH by writing an `export` line into `~/.bashrc` or `~/.zshrc`.

Open a new terminal (or `source ~/.bashrc` / `source ~/.zshrc`) and verify:

```bash
opencode --version
```

> **Heads-up if you're running this in a container, headless server, or any
> non-interactive shell:** the opencode installer only updates your shell's
> rc file. Non-interactive shells (systemd services, container exec scripts,
> sub-shells launched by other tools) do NOT source `~/.bashrc` or
> `~/.zshrc`, so they won't see `opencode` on PATH. In those environments,
> add `~/.opencode/bin` to PATH explicitly — for example:
>
> ```bash
> export PATH="$HOME/.opencode/bin:$PATH"
> ```
>
> Or place that export in `/etc/profile.d/opencode.sh` for system-wide
> coverage. `amplifier-opencode doctor` will flag this with a clear error
> if opencode isn't on PATH when it runs.

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

</details>

---

## Provider Credentials

**You usually don't need to do anything here manually.** The first time you run
`amplifier-opencode` (or `amplifier-opencode setup`) with no provider connected,
it walks you through picking a provider and pasting a key, then stores it for
you. This section is reference for when you'd rather set credentials yourself.

amplifier-opencode itself does no provider detection. `amplifier-agent serve`
auto-enables every provider whose credentials resolve (env var, or
`~/.amplifier-agent/credentials.json` via `amplifier-agent auth set`) whenever
you don't pass it an explicit `--config`. Set one or more of the following
environment variables (or run `amplifier-agent auth set`) before running
`amplifier-opencode launch`:

| Provider | Env var |
|---|---|
| Anthropic (Claude) | `ANTHROPIC_API_KEY` |
| OpenAI (GPT) | `OPENAI_API_KEY` |
| Azure OpenAI | `AZURE_OPENAI_API_KEY` or `AZURE_OPENAI_KEY` |
| Ollama (local models) | `OLLAMA_HOST` |

Run `amplifier-opencode doctor` to see which providers amplifier-agent will
actually serve (it shells out to `amplifier-agent providers list --json`).

### Advanced: custom host_config.json

If you want fine-grained control (custom MCP servers, approval policies,
per-provider config overrides), write your own `host_config.json` and pass
it with `--host-config`. amplifier-opencode passes it straight through to
`amplifier-agent serve --config`:

```bash
amplifier-opencode launch --host-config /path/to/host_config.json
```

See [amplifier-agent's host_config documentation](https://github.com/microsoft/amplifier-agent) for the full schema.

---

## First run

```bash
amplifier-opencode
```

The very first time, amplifier-opencode makes sure the whole stack is ready
before it does anything else: it installs or updates amplifier-agent and
opencode if needed, and — if no provider is connected yet — walks you through
adding one. (To do just that setup without going further, run
`amplifier-opencode setup`.) Once everything's healthy you'll see something
like:

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
  [ OK ]  opencode config     opencode config has provider.amplifier with 3 models
  [ OK ]  live models         Discovered 3 model(s): claude-haiku-4-5-20251001, ...

  Providers (via `amplifier-agent providers list`):
    ✓ anthropic resolvable (source=env) → will be served
    ✗ openai not resolvable → set OPENAI_API_KEY or run `amplifier-agent auth set openai <key>` to enable it

    → 1 provider will be auto-enabled on launch (anthropic)

All required checks passed.
```

Exit code is 0 when everything passes, 1 if any FAIL. Suitable for CI scripts.

Common FAILs and their fix:

| FAIL message | Fix |
|---|---|
| `amplifier-agent not on PATH` | Re-run the install one-liner above, then open a new terminal |
| `opencode not on PATH` | `curl -fsSL https://opencode.ai/install \| bash` (then open a new terminal) |
| `No provider credentials resolvable` | Export `ANTHROPIC_API_KEY`, OR run `amplifier-agent auth set anthropic <key>` |
| `Could not run \`amplifier-agent providers list --json\`` | Install/upgrade amplifier-agent so the doctor command can query it |
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
| `--yes` | — | false | Assume "yes" to all prompts (install/heal without asking). For CI and non-interactive shells. |
| `--no-bootstrap` | — | false | Skip the self-healing preflight; assume amplifier-agent and opencode are already installed. |

### `launch` (default)

Discover models, write opencode.json, exec opencode. Run when no subcommand is given.

| Flag | Env var | Default | Purpose |
|---|---|---|---|
| `--workspace` | `AMPLIFIER_AGENT_WORKSPACE` | `opencode` | Server-side workspace name (only used when starting the server) |
| `--host-config` | `AMPLIFIER_AGENT_HOST_CONFIG` | — | Path to a host_config.json passed to `amplifier-agent serve --config`; omitted entirely when unset, relying on amplifier-agent's auto-enable (only used when starting the server) |
| `--project-dir` | — | (use global) | Write opencode.json into this directory instead of global config |
| `--no-start` | — | false | Don't auto-start amplifier-agent; require server already up |
| `--no-launch` | — | false | Don't exec opencode; just write the config |
| `--amplifier-agent-bin` | `AMPLIFIER_AGENT_BIN` | autodetect | Override the amplifier-agent binary path |
| `--provider-id` | — | `amplifier` | Provider ID under `provider.<id>` |
| `OPENCODE_ARGS...` (after `--`) | — | — | Pass-through to opencode |

### `doctor`

Run all prerequisite checks. No flags; honours the global `--base-url` and `--api-key`.

### `setup`

Make the whole stack ready without launching: install/heal amplifier-agent and
opencode, then walk through connecting a provider if none is configured. Handy
for a one-time "get me set up" pass. Respects the global `--yes` flag.

### `update`

Update amplifier-opencode, then amplifier-agent, then opencode to their latest
versions.

| Flag | Default | Purpose |
|---|---|---|
| `--no-opencode` | false | Update the Amplifier pieces but leave opencode at its current version |
| `--ref` | `main` | Git ref to install amplifier-opencode from |
| `--force` | false | Reinstall even if already up to date |

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

> [!NOTE]
> This project is not currently accepting external contributions, but we're actively working toward opening this up. We value community input and look forward to collaborating in the future. For now, feel free to fork and experiment!

Most contributions require you to agree to a
Contributor License Agreement (CLA) declaring that you have the right to, and actually do, grant us
the rights to use your contribution. For details, visit [Contributor License Agreements](https://cla.opensource.microsoft.com).

When you submit a pull request, a CLA bot will automatically determine whether you need to provide
a CLA and decorate the PR appropriately (e.g., status check, comment). Simply follow the instructions
provided by the bot. You will only need to do this once across all repos using our CLA.

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/).
For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or
contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or comments.

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft
trademarks or logos is subject to and must follow
[Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/legal/intellectualproperty/trademarks/usage/general).
Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship.
Any use of third-party trademarks or logos are subject to those third-party's policies.