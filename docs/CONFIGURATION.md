# Configuration

amplifier-opencode itself stores no credentials and holds almost no configuration of its own.
Nearly everything here configures the amplifier-agent backend it talks to. This doc covers
providers, credentials, where the generated opencode config lands, and the two escape hatches
(a different agent, a custom host config), plus raw payload capture for debugging.

## Providers

amplifier-agent talks to upstream model APIs and needs credentials for at least one provider.
**You usually don't need to do anything here**: on first run (or via `amplifier-opencode setup`),
if no provider is connected, amplifier-opencode walks you through picking one and pasting a key,
then stores it for you via amplifier-agent.

The set of providers offered, and everything `doctor` reports, comes from asking amplifier-agent
itself which providers it knows about and which currently resolve. amplifier-opencode never
inspects environment variables or a credentials file directly to answer that question;
amplifier-agent is the only thing that can truthfully say what it will auto-enable at its own
server startup.

Four provider ids get richer wizard wording (label, prompt, secret vs. not); any other provider
amplifier-agent reports still appears in the menu with a generic `<provider id> API key` prompt:

| Provider | Env var | Notes |
|---|---|---|
| Anthropic (Claude) | `ANTHROPIC_API_KEY` | |
| OpenAI (GPT) | `OPENAI_API_KEY` | |
| Azure OpenAI | `AZURE_OPENAI_API_KEY` or `AZURE_OPENAI_KEY` | wizard also asks for the endpoint URL |
| Ollama (local models) | `OLLAMA_HOST` | value shown while typing, not treated as a secret |
| GitHub Copilot | `GITHUB_TOKEN` | see GitHub Copilot below |

Run `amplifier-opencode doctor` to see which providers will actually be served:

```
  Providers (via `amplifier-agent providers list`):
    ✓ anthropic resolvable (source=env) → will be served
    ✗ openai not resolvable → set OPENAI_API_KEY or run `amplifier-agent auth set openai <key>` to enable it

    → 1 provider will be auto-enabled on launch (anthropic)
```

The onboarding wizard only ever triggers on a confident "zero resolvable providers" signal. If the
provider report can't be obtained at all (agent missing, unreachable, output unreadable), the
wizard does not run, so a merely slow or unreachable setup is never mistaken for one with no
credentials, and a working setup is never interrupted.

## Credentials

To set a key yourself instead of running the wizard, export the matching environment variable
before launching. amplifier-agent picks it up automatically at server startup.

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENAI_API_KEY="sk-..."
export AZURE_OPENAI_API_KEY="..."
export OLLAMA_HOST="http://localhost:11434"   # if running ollama locally
```

Alternative: amplifier-agent's persistent credential file. Run once, and the key is available
from every future invocation, from any directory:

```bash
amplifier-agent auth set anthropic sk-ant-...
amplifier-agent auth list                       # confirm it's stored
```

Resolution is **env-first**: a shell environment variable always wins over the credentials file,
so existing shell-rc workflows keep working unchanged.

amplifier-opencode stores no credentials of its own, anywhere. Every credential write from the
onboarding wizard is delegated to amplifier-agent's own credential-set command as a subprocess,
with the secret value passed over that subprocess's stdin, never as a command-line argument, so
it never appears in the process listing. On failure, any occurrence of the secret that gets
echoed back in the error output is scrubbed to `***` before being shown to you.

## GitHub Copilot

The `gh` CLI bridge is the easiest way to connect Copilot:

```bash
export GITHUB_TOKEN=$(gh auth token)
```

An existing `gh` or VS Code login may already authenticate it through cached OAuth, so try
`amplifier-opencode doctor` before exporting anything.

**Copilot is environment-only, and amplifier-opencode has no Copilot-specific code path of its
own.** `GITHUB_TOKEN` is read and resolved entirely on the amplifier-agent side; amplifier-opencode
never reads it directly and never mentions it except by inheriting whatever amplifier-agent's own
provider report says about it.

Copilot resells models from several vendors, so a model id like `claude-sonnet-5` is served both
by Copilot and by its original vendor. Copilot's models are namespaced as `github-copilot/<model>`
(shown with a `(GitHub)` suffix in the opencode picker) so the two stay separately addressable and
don't collide in the model list. amplifier-opencode passes these ids through verbatim from what
amplifier-agent reports; it has no namespacing logic of its own to keep in sync.

## Where the config is written

amplifier-opencode writes a fresh opencode config on every launch, re-synced to whatever
amplifier-agent is currently serving. By default this is global:

```
~/.config/opencode/opencode.jsonc
```

and applies from every directory. Each launch also bridges amplifier-agent's skills and modes into
opencode, written next to the config: `~/.config/opencode/command/` (slash commands, one per
user-invocable skill) and `~/.config/opencode/agent/` (one agent per mode, shown as
`<mode> (Amplifier)`).

amplifier-opencode only manages files it generated itself, tracked in
`.amplifier-generated-commands.json` / `.amplifier-generated-agents.json` next to each directory.
A command or agent file you wrote yourself is never overwritten; it's skipped with a warning, and
name conflicts (a skill or mode name found in more than one place) are reported at launch.

### Writing to a project's config instead

```bash
cd my-project
amplifier-opencode --project-dir .          # bridge only, into ./opencode.json
amplifier-opencode launch --project-dir .   # bridge + TUI from this directory
```

The generated config lives in `./opencode.json`; opencode walks up from `cwd` to find it.

## Pointing at a different agent

```bash
# Server running on another machine or port
amplifier-opencode --base-url http://my-server:9099/v1 --api-key my-token

# Server already running elsewhere; don't auto-start
amplifier-opencode --no-start
```

`--base-url` (env `AMPLIFIER_AGENT_BASE_URL`, default `http://127.0.0.1:9099/v1`) and `--api-key`
(env `AMPLIFIER_AGENT_API_KEY`, default `local-dev-secret`) are global flags, so they apply to
every subcommand.

Before spawning a server, amplifier-opencode checks whether one is already reachable at the
configured base URL, using a liveness probe alone, and reuses it as-is if so; nothing is spawned
in that case. **No version check is performed against a reused server.** The version floor is
enforced only against the locally installed amplifier-agent binary, and only during the preflight
that runs before a spawn decision is made.

## Custom host config

For fine-grained control (custom MCP servers, approval policies, per-provider config overrides),
write your own `host_config.json` and pass it with `--host-config`. amplifier-opencode passes it
straight through to `amplifier-agent serve --config`:

```bash
amplifier-opencode launch --host-config /path/to/host_config.json
```

`--host-config` (env `AMPLIFIER_AGENT_HOST_CONFIG`) is only consulted when amplifier-opencode is
the one starting the server; a reused, already-running server keeps whatever config it was started
with.

The file's schema is owned by amplifier-agent, not by this tool. See the
[amplifier-agent documentation](https://github.com/microsoft/amplifier-agent) for the full set of
keys it accepts.

## Capturing raw LLM payloads

When you need to see exactly what went to the model and came back (a bad reply, a misfired tool
call, a prompt that didn't look right), turn on raw payload capture in your `host_config.json`:

```json
{
  "provider": { "module": "anthropic" },
  "debug": { "rawLlmPayloads": true }
}
```

```bash
amplifier-opencode launch --host-config /path/to/host_config.json
```

Every turn then records the complete outbound request (full message list, system prompt, and tool
schemas) and the complete response (content blocks, usage, stop reason) into the session's event
log:

```
~/.amplifier-agent/state/workspaces/opencode/sessions/http-<session-id>/context-intelligence/events.jsonl
```

The payloads ride on the `llm:request` and `llm:response` events under a `raw` key. Those lines are
large, so pull the fields you want rather than opening the file whole:

```bash
python3 - <<'PY'
import glob, json, os
f = max(glob.glob(os.path.expanduser(
    "~/.amplifier-agent/state/workspaces/opencode/sessions/*/context-intelligence/events.jsonl"
)), key=os.path.getmtime)
for line in open(f):
    e = json.loads(line); d = e.get("data") or {}
    if isinstance(d, dict) and "raw" in d and e.get("event", "").startswith("llm:"):
        print(e["event"], "->", sorted(d["raw"])[:8])
PY
```

Three things to know before you turn this on:

- **It writes your full conversation text to disk, unredacted.** Secret redaction matches by key
  name only and never scans string values, so prompts, tool results, and file contents are stored
  as-is. There is no truncation and no size cap. Do not leave it on for routine work, and be
  careful where those session directories end up.
- **The value must be a real JSON boolean.** `"true"` as a string is rejected with a config error
  rather than silently accepted.
- **Coverage depends on the provider.** `anthropic`, `openai`, and `azure-openai` record full
  payloads. `ollama` records full payloads but does not redact secrets. `github-copilot` accepts
  the flag but only emits counts and lengths, so it will not give you prompt or response bodies.

Requires an amplifier-agent at or above the floor amplifier-opencode enforces. Older agent
versions reject `debug` as an unknown config key, and their HTTP face ignored `provider.config`
entirely.

## Related

- Provider discovery, the onboarding wizard, and secret hygiene contract:
  [`spec/providers-and-credentials.md`](spec/providers-and-credentials.md)
- Install and self-heal for amplifier-agent and opencode: [`INSTALL.md`](INSTALL.md)
- Agent version floor and why it moves:
  [`spec/agent-integration.md`](spec/agent-integration.md)
