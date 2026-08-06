<h1 align="center">Amplifier for opencode</h1>

<p align="center">
  <a href="docs/INSTALL.md">Install</a> &nbsp;&bull;&nbsp;
  <a href="docs/CONFIGURATION.md">Configuration</a> &nbsp;&bull;&nbsp;
  <a href="docs/SPEC.md">Specifications</a>
</p>

---

Run the [opencode](https://opencode.ai) coding TUI on top of your local
[amplifier-agent](https://github.com/microsoft/amplifier-agent), one command to
install, one command to start coding:

```bash
# 1. install (also installs everything it needs)
curl -fsSL https://raw.githubusercontent.com/microsoft/amplifier-app-opencode/main/install.sh | bash

# 2. set up the connection and jump into opencode
amplifier-opencode launch
```

amplifier-opencode discovers which models your amplifier-agent serves, writes a
working opencode config from that discovery, and opens opencode, re-synced
every time, with no config to maintain by hand.

## Features

- **Self-managing.** Installs and updates amplifier-agent and opencode, and walks you through connecting a provider on first run.
- **Always-live models.** The model picker is rediscovered from `GET /v1/models` on every launch, so it never drifts from what amplifier-agent is actually serving.
- **Zero-config bridge.** amplifier-agent's server is auto-started in the background if it isn't already running.
- **Skills as commands.** amplifier-agent skills show up as opencode slash commands, no extra setup.
- **Modes as agents.** amplifier-agent modes show up as opencode agents, shown as `<mode> (Amplifier)`.
- **Built-in `doctor`.** Diagnoses any setup issue in one command.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/microsoft/amplifier-app-opencode/main/install.sh | bash
```

This installs [`uv`](https://docs.astral.sh/uv/) if missing, and the `amplifier-opencode` CLI itself. It does not install amplifier-agent or opencode; those come from the first `launch`.

Prefer to review the script before running it:

```bash
curl -fsSL https://raw.githubusercontent.com/microsoft/amplifier-app-opencode/main/install.sh -o install.sh
less install.sh
bash install.sh
```

Then run `amplifier-opencode launch`. On first run it self-heals the rest of the stack: it installs amplifier-agent and opencode if either is missing, and walks you through connecting a provider if none is configured. Anything already installed and healthy is left untouched. Once the stack is healthy, opencode opens with the model picker under the **Amplifier** section, re-synced on every future run. Want just the setup without launching? Run `amplifier-opencode setup`.

For pinning a version, manual install steps, and uninstalling, see [docs/INSTALL.md](docs/INSTALL.md).

### Platform support

macOS, Linux, and WSL are fully supported. Native Windows is not supported, run `amplifier-opencode` inside **WSL** instead. If a component can't be auto-installed on your platform, `amplifier-opencode` tells you what to do instead of failing silently.

## Connecting a provider

**You usually don't need to do anything here.** On first run, if no provider is configured, `amplifier-opencode` walks you through picking one and stores the credential for you.

To set one yourself, export a key before running `launch`:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

amplifier-agent also supports OpenAI, Azure OpenAI, Ollama, and GitHub Copilot. Run `amplifier-opencode doctor` to see which providers will actually be served.

### GitHub Copilot

The `gh` CLI bridge is the easiest way to connect Copilot:

```bash
export GITHUB_TOKEN=$(gh auth token)
```

Copilot models appear namespaced as `github-copilot/<model>`, shown as `<model> (GitHub)` in the picker.

For the full env var table, the persistent credential file, and `host_config.json`, see [docs/CONFIGURATION.md](docs/CONFIGURATION.md).

## Skills and modes

Every launch also bridges amplifier-agent's skills and modes into opencode:

- **Skills become slash commands.** Each user-invocable skill becomes a `/<name>` command that runs the skill server-side in amplifier-agent.
- **Modes become agents.** Each mode becomes a selectable opencode agent, shown as `<mode> (Amplifier)` in the agent picker.

Files land next to your opencode config, at `~/.config/opencode/command/` and `~/.config/opencode/agent/` globally, or under `.opencode/` with `--project-dir`. `amplifier-opencode` only manages the files it generated; a command or agent file you wrote yourself is never overwritten.

opencode's own commands are untouched. `/models`, `/connect`, and everything else it ships with keep working as they always did.

See [docs/spec/skills-and-modes-bridge.md](docs/spec/skills-and-modes-bridge.md) for the exact file shapes and conflict handling.

## Everyday use

```bash
amplifier-opencode launch     # set up the bridge and open the opencode TUI
amplifier-opencode            # refresh the bridge only, no launch (same as `prepare`)
amplifier-opencode setup      # install/heal the stack and connect a provider, no launch
amplifier-opencode update     # update amplifier-opencode, amplifier-agent, and opencode
amplifier-opencode doctor     # diagnose every prerequisite in one shot
```

Run the plain refresh any time you change providers, add a credential, or restart amplifier-agent. Pass arguments straight through to opencode after `--`:

```bash
amplifier-opencode launch -- run "summarise this codebase"
```

The full command and flag reference is in [docs/spec/cli.md](docs/spec/cli.md).

## Troubleshooting

Run the doctor before asking anyone for help:

```bash
amplifier-opencode doctor
```

It reports on every prerequisite in one shot, from binaries on `PATH` to which providers will actually resolve.

A couple of common fixes:

- `opencode not on PATH`: `curl -fsSL https://opencode.ai/install | bash`, then open a new terminal.
- `No provider credentials resolvable`: export `ANTHROPIC_API_KEY`, or run `amplifier-agent auth set anthropic <key>`.

For the meaning of every other failure, see [docs/ISSUES.md](docs/ISSUES.md). Logs live at `<tempdir>/amplifier-agent.log` (the backend server) and `~/.local/share/opencode/log/opencode.log` (opencode itself).

## Architecture at a glance

```
opencode TUI
    |
amplifier-opencode adapter      (this repo: discover, translate, bridge)
    |
amplifier-agent HTTP face       (multi-provider chat-completions server)
    |
providers                       (Anthropic, OpenAI, Azure OpenAI, Ollama, GitHub Copilot)
```

`amplifier-opencode` is the adapter layer only. No model logic, no prompt content, no tool-calling logic lives here; every intelligent behavior happens server-side in amplifier-agent. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full design notes.

## Documentation

| Document | Covers |
|---|---|
| [Install](docs/INSTALL.md) | Reviewing the script, manual install, pinning a version, updating, uninstalling |
| [Configuration](docs/CONFIGURATION.md) | Provider env vars, the credential file, `host_config.json`, raw payload capture |
| [Specifications](docs/SPEC.md) | Normative contracts: CLI surface, install/update, providers, generated config, skills/modes bridge, agent integration, file locations |
| [Architecture](docs/ARCHITECTURE.md) | Why it works the way it does: the launch sequence, live config generation, the two bridges |
| [Known issues](docs/ISSUES.md) | Tracked gaps between the stated contract and current behavior |
| [E2E testing](docs/E2E_TESTING.md) | The DTU end-to-end test framework and how to add a suite |
| [Development](DEVELOPMENT.md) | Local setup and the `make` command surface |

## Development

End-to-end suites drive the real installed `amplifier-opencode` binary, and in TUI suites the real opencode TUI, inside an isolated DTU container. See [DEVELOPMENT.md](DEVELOPMENT.md) for local setup and the `make` command surface.

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
