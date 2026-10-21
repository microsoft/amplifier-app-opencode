# Amplifier for opencode

Use [Amplifier Agent](https://github.com/microsoft/amplifier-agent) as the agent inside [opencode](https://opencode.ai).
Amplifier runs the whole turn: its own prompt, tools, delegation, and context management. opencode is the interface.

## Quick start

Requirements: opencode 2.1 or newer, Linux or macOS (Windows through WSL), and a key for a provider Amplifier supports.

```bash
curl -fsSL https://raw.githubusercontent.com/microsoft/amplifier-app-opencode/main/install.sh | bash
opencode auth login anthropic          # once; or /connect inside opencode
opencode
```

The installer installs [uv](https://docs.astral.sh/uv/) if it is missing, installs `amplifier-opencode` with
`uv tool install`, and adds the Amplifier plugin to opencode. Without the script:

```bash
uv tool install git+https://github.com/microsoft/amplifier-app-opencode
amplifier-opencode install
```

In opencode:

- `amplifier` is the default agent. Every message goes to Amplifier.
- `/models` lists `Amplifier default` and every model Amplifier can reach with your keys, as
  `amplifier/<provider>-<model>`, for example `amplifier/anthropic-claude-sonnet-5-5`.
- Each model has one variant per reasoning effort it supports, for example `amplifier/anthropic-claude-opus-5-5#high`.
- A session keeps its Amplifier conversation, including tool work, across turns and restarts.

Providers you connect in opencode reach Amplifier with no other step: Anthropic, OpenAI, and Google API keys, a GitHub
Copilot sign-in, and an Azure key with its resource name. Connecting a provider while opencode runs restarts Amplifier,
and its models appear in `/models`.

Check the installation with `amplifier-opencode doctor`.

## Migrating from 0.4.x

0.4.x was a launcher: `amplifier-opencode launch` started a background `amplifier-agent serve` server, wrote a provider
block into opencode 1.x config, and then started opencode. This version is an opencode 2.x plugin. The plugin starts
and stops Amplifier for you, and you run `opencode` directly.

```bash
opencode upgrade                                    # opencode 2.1 or newer
uv tool upgrade amplifier-app-opencode              # or run install.sh again
amplifier-opencode install                          # adds the plugin, removes 0.4.x leftovers
pkill -f "amplifier-agent serve"                    # the old background server
uv tool uninstall amplifier-agent                   # the old amplifier-agent CLI
```

The details of what changed are in [migrating from 0.4.x](docs/migrating-from-0.4.md).

## Configuration

`amplifier-opencode install` writes `~/.config/opencode/amplifier.json` if it does not exist:

```json
{
  "defaultModel": null,
  "setDefaultAgent": true,
  "approvals": "opencode",
  "environment": {},
  "command": "amplifier-opencode"
}
```

```
defaultModel      a model from /v1/models as <provider>/<model>, for example "openai/gpt-6.1-sol". It also sets
                  Amplifier's default provider and model; null uses the first connected provider: OpenAI,
                  Anthropic, Google, ChatGPT, then GitHub Copilot
setDefaultAgent   make amplifier the default agent
approvals         "opencode" asks opencode's permissions before each Amplifier tool call; "allow" runs them
                  all; "deny" refuses every tool call
environment       variables added to Amplifier's environment; they override opencode connections
command           the amplifier-opencode executable the plugin starts
```

Amplifier also reads its own settings from `~/.amplifier-agent/config.json` and `AMPLIFIER_AGENT_*` variables; see
[configuration](https://github.com/microsoft/amplifier-agent/blob/main/docs/configuration.md).

`amplifier-opencode install --project DIR` installs the plugin for one project, in `DIR/.opencode/plugins/amplifier/`.
`amplifier-opencode uninstall` removes it.

## How it works

opencode loads the plugin from its plugins directory. For each opencode location, the plugin starts Amplifier's
[HTTP face](https://github.com/microsoft/amplifier-agent/blob/main/docs/http/quickstart.md) on a local port with a
random token, registers it as the `amplifier` provider, and adds the `amplifier` agent. Each opencode session maps to
a durable Amplifier session. Each opencode turn is one Amplifier turn.
Amplifier's tool calls show as opencode tools (bash, read, write, edit, apply_patch, glob, grep, webfetch, websearch,
task) that Amplifier already ran in the session's directory. opencode's permissions decide each call first: the `bash`
key for commands, `edit` for file changes, `read` for reads and searches, `webfetch` for web requests, and `mcp` for MCP
tools. Esc stops the turn.

A turn that edits or searches spans several opencode steps: opencode's edit, patch, glob, and grep tools show
Amplifier's calls with their diffs and match counts, and never run them again. These tools stay allowed for the
amplifier agent; Amplifier asks for the effects they show.

opencode's skills (`.opencode/skills`, `.claude/skills`, `.agents/skills`, global ones, and config `skills`) and its
enabled MCP servers reach Amplifier too. Amplifier restarts when they change, and once after a crash.

The plugin reports each turn's usage so opencode's context meter shows the size of Amplifier's last model request
against the model's window, and its cost shows what Amplifier reports. opencode's own compaction keeps the transcript
unchanged; Amplifier compacts its session itself.

The sidebar's Amplifier usage panel shows tokens in and out, cache reads and writes, and cost for each provider and
model, for the last turn and the session, and how often Amplifier compacted the session.

## Troubleshooting

- Run `amplifier-opencode doctor`. It checks the opencode version, the installed plugin, and which providers have
  credentials.
- The context meter can pass 100%. It reports Amplifier's context, which Amplifier compacts on its own schedule.
- Logs are in `~/.local/state/amplifier-opencode/`: `plugin.log` and one face log per directory.
- Credentials resolve in this order, later wins: the environment, opencode connections, then `amplifier.json`.
  Connections made in opencode apply at once. Keys exported in your shell reach Amplifier only after
  `opencode service restart`, because opencode 2.x keeps a background service.
- The provider shows as `Amplifier (run: amplifier-opencode install)` when the installed plugin and the package
  versions differ. Run `amplifier-opencode install`.

## Development

```bash
make sync-local     # uv sync against the sibling ../amplifier-agent checkout
make plugin         # build the plugin bundle into the package
make check          # lint, type-check, and confirm the plugin bundle is up to date
make test           # unit tests
make e2e            # end-to-end suites in a Digital Twin Universe; SUITE=<name> runs one
```

The end-to-end suites need Docker and `OPENAI_API_KEY`. They publish this checkout and `../amplifier-agent` through
a local Gitea and install as a user would; see `tests/e2e/conftest.py`.

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
