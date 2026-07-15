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

## What you get

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

---

## Install

Install one thing; the rest of the stack heals itself.

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
`amplifier-opencode` CLI. Then run:

```bash
amplifier-opencode launch
```

On that first run, amplifier-opencode **self-heals the rest of the stack**:

- Installs [`amplifier-agent`](https://github.com/microsoft/amplifier-agent)
  (the backend server) if missing, and updates it if it's below the required
  minimum.
- Installs [`opencode`](https://opencode.ai) (the TUI) if missing, using the
  best method for your OS (curl installer, Homebrew, npm, scoop, or choco).
- If no provider credentials are configured, it **walks you through connecting
  one** (Anthropic, OpenAI, Azure, or Ollama) and stores it via amplifier-agent.
- Anything already installed and healthy is left untouched.

Want just the setup without launching? Run `amplifier-opencode setup`.

### Platform support

macOS, Linux, and WSL are fully supported. On **native Windows**, run
amplifier-opencode inside **WSL** — opencode's own docs recommend WSL, and the
self-healing installers are bash-based. If a component can't be auto-installed
on your platform, amplifier-opencode tells you exactly what to do instead of
failing silently.

---

## Start coding

```bash
amplifier-opencode launch
```

This sets up the connection and drops you straight into the opencode TUI. The
first time, it makes sure the whole stack is ready — installing amplifier-agent
and opencode if needed, and walking you through connecting a provider if none is
configured. Once everything's healthy you'll see something like:

```
[1/4] Starting amplifier-agent (port 9099, workspace='opencode')
      amplifier-agent ready at http://127.0.0.1:9099/v1
[2/4] Discovering models via GET http://127.0.0.1:9099/v1/models
      - anthropic   claude-haiku-4-5-20251001            Claude Haiku 4.5
      - anthropic   claude-opus-4-8                      Claude Opus 4.8
      - anthropic   claude-sonnet-4-6                    Claude Sonnet 4.6
[3/4] Wrote /Users/you/.config/opencode/opencode.jsonc  (global config)
[4/4] Configuration complete.
```

opencode opens with the model picker under the **Amplifier** section. Pick a
model and start coding. The config is global, so it applies from every
directory — and it's re-synced on every run.

---

## Connecting a provider

**You usually don't need to do anything here.** On first run (or via
`amplifier-opencode setup`), if no provider is connected, amplifier-opencode
walks you through picking one and pasting a key, then stores it for you.

To set a key yourself instead, export one of these before running and
amplifier-agent will pick it up automatically:

| Provider | Env var |
|---|---|
| Anthropic (Claude) | `ANTHROPIC_API_KEY` |
| OpenAI (GPT) | `OPENAI_API_KEY` |
| Azure OpenAI | `AZURE_OPENAI_API_KEY` or `AZURE_OPENAI_KEY` |
| Ollama (local models) | `OLLAMA_HOST` |

Run `amplifier-opencode doctor` to see which providers will actually be served.

---

## Everyday use

| Command | What it does |
|---|---|
| `amplifier-opencode launch` | Set up the bridge **and** open the opencode TUI |
| `amplifier-opencode` | Refresh the bridge only (re-discover models, rewrite config) — no launch |
| `amplifier-opencode setup` | Install/heal the stack and connect a provider, without launching |
| `amplifier-opencode update` | Update amplifier-opencode, amplifier-agent, and opencode |
| `amplifier-opencode doctor` | Diagnose every prerequisite in one shot |

Run the plain `amplifier-opencode` refresh any time you change providers, add a
credential, or restart amplifier-agent — it re-syncs opencode with whatever
amplifier-agent is now serving.

Pass arguments straight through to opencode after `--`:

```bash
amplifier-opencode launch -- run "summarise this codebase"
```

Keep everything current with one command:

```bash
amplifier-opencode update                # amplifier-opencode + amplifier-agent + opencode
amplifier-opencode update --no-opencode  # update the Amplifier pieces, keep opencode pinned
```

### Command reference

- **`amplifier-opencode launch`** — set up the bridge and exec the opencode TUI.
  Pass-through args after `--` go to opencode.
- **`amplifier-opencode`** (no subcommand) — set up / refresh the bridge without
  launching.
- **`amplifier-opencode setup`** — install and heal the stack and connect a
  provider; no launch. Add `--yes` for non-interactive installs.
- **`amplifier-opencode update`** — update all three components (`--no-opencode`
  to leave opencode alone).
- **`amplifier-opencode doctor`** — run all prerequisite checks; exit code 0 when
  everything passes, 1 on any failure.

The full flag tables for every command are in **Advanced usage** below.

---

## Troubleshooting

Run the doctor before asking anyone for help — it reports on every prerequisite
in one shot:

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

Common failures and their fix:

| FAIL message | Fix |
|---|---|
| `amplifier-agent not on PATH` | Re-run the install one-liner, then open a new terminal |
| `opencode not on PATH` | `curl -fsSL https://opencode.ai/install \| bash` (then open a new terminal) |
| `No provider credentials resolvable` | Export `ANTHROPIC_API_KEY`, OR run `amplifier-agent auth set anthropic <key>` |
| `Could not run \`amplifier-agent providers list --json\`` | Install/upgrade amplifier-agent so the doctor command can query it |
| `opencode config ... is malformed JSON` | Open `~/.config/opencode/opencode.jsonc`, fix or delete it, retry |

---

## Advanced usage

<details>
<summary>Manual install, full flag reference, and power-user options — click to expand</summary>

Most people never need anything in this section — the one-command install and
the everyday commands above cover normal use. This is here for hand-installs,
scripting, and fine-grained control.

### Manual install

Prefer to install each component yourself? You need **a few system tools** and
**three Amplifier components**. The steps below walk through the official
install for each so a brand-new machine can get set up start-to-finish.

#### 0. System tools — git, curl

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

#### 1. amplifier-agent — the backend server (>= 0.9.1 required)

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

#### 2. opencode — the TUI

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

#### 3. amplifier-app-opencode — this adapter

Same uv-tool pattern as amplifier-agent:

```bash
uv tool install --from git+https://github.com/microsoft/amplifier-app-opencode amplifier-app-opencode

# Verify
amplifier-opencode --help
```

(Once published to PyPI, this becomes `uv tool install amplifier-app-opencode`.)

#### 4. At least one provider credential

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

### Full CLI reference

```
amplifier-opencode [GLOBAL OPTIONS] [SUBCOMMAND] [SUBCOMMAND OPTIONS]
```

#### Global options

| Flag | Env var | Default | Purpose |
|---|---|---|---|
| `--base-url` | `AMPLIFIER_AGENT_BASE_URL` | `http://127.0.0.1:9099/v1` | amplifier-agent endpoint |
| `--api-key` | `AMPLIFIER_AGENT_API_KEY` | `local-dev-secret` | wire-level bearer token |
| `--yes` | — | false | Assume "yes" to all prompts (install/heal without asking). For CI and non-interactive shells. |
| `--no-bootstrap` | — | false | Skip the self-healing preflight; assume amplifier-agent and opencode are already installed. |

#### `launch` (default)

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

#### `doctor`

Run all prerequisite checks. No flags; honours the global `--base-url` and `--api-key`.

#### `setup`

Make the whole stack ready without launching: install/heal amplifier-agent and
opencode, then walk through connecting a provider if none is configured. Handy
for a one-time "get me set up" pass. Respects the global `--yes` flag.

#### `update`

Update amplifier-opencode, then amplifier-agent, then opencode to their latest
versions.

| Flag | Default | Purpose |
|---|---|---|
| `--no-opencode` | false | Update the Amplifier pieces but leave opencode at its current version |
| `--ref` | `main` | Git ref to install amplifier-opencode from |
| `--force` | false | Reinstall even if already up to date |

### Custom host_config.json

If you want fine-grained control (custom MCP servers, approval policies,
per-provider config overrides), write your own `host_config.json` and pass
it with `--host-config`. amplifier-opencode passes it straight through to
`amplifier-agent serve --config`:

```bash
amplifier-opencode launch --host-config /path/to/host_config.json
```

See [amplifier-agent's host_config documentation](https://github.com/microsoft/amplifier-agent) for the full schema.

### Point at a different amplifier-agent

```bash
# Server running on another machine or port
amplifier-opencode --base-url http://my-server:9099/v1 --api-key my-token

# Server already running — don't auto-start
amplifier-opencode --no-start
```

### Write to a project's opencode.json instead of the global config

```bash
cd my-project
amplifier-opencode --project-dir .          # bridge only, into ./opencode.json
amplifier-opencode launch --project-dir .   # bridge + TUI from this directory
```

The generated config lives in `./opencode.json`. opencode walks up from cwd to
find it.

</details>

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
