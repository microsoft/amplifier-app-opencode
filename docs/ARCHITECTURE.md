# Architecture & design notes

Background on *why* it works the way it does. This is contributor/maintainer
material -- end users don't need any of it (see the top-level `README.md`
for install and usage). For *what the contract is* rather than *why*, see
[`docs/SPEC.md`](SPEC.md).

## What this is

`amplifier-opencode` is a launcher and adapter, not a fork or a plugin. It
sits between two independently developed projects it does not modify: the
[opencode](https://opencode.ai) TUI and the
[amplifier-agent](https://github.com/microsoft/amplifier-agent) HTTP server.
**The whole adapter is one binary** (`src/amplifier_app_opencode/cli.py`):
there is no separate daemon, no background service beyond the
amplifier-agent server this tool itself spawns, and no persistent state of
its own (see `spec/file-locations.md`, "no config file for this tool's
own settings"). Every invocation is a fresh discovery-and-translation pass.

## The launch sequence

Every `launch`/`prepare` invocation runs the same five observable stages, in
this order:

```
1. CHECK     is amplifier-agent's chat-completions server already running?
             (an HTTP probe against the base URL, never a PID file)
2. START     if not, spawn `amplifier-agent serve chat-completions` in the
             background, with the user's --workspace / --host-config
3. DISCOVER  GET /v1/models from amplifier-agent
4. WRITE     materialise an opencode provider config from that response
             (global opencode.jsonc/.json, or <project-dir>/opencode.json)
5. EXEC      replace this process with opencode (`prepare` stops before this
             stage; `launch` runs it)
```

Stage 2 and stage 5 are where the two most consequential implementation
choices live. Stage 2 spawns the server with `start_new_session=True`
specifically so it detaches from this process's session and survives stage
5's `exec` (see "Key decisions" below). Stage 5 uses `os.execvp`, replacing
this process's PID with opencode's rather than forking a child and waiting
on it, so opencode inherits the terminal directly and this tool is not on
the hook for forwarding signals (Ctrl-C, terminal resize) to a child it
would otherwise have to supervise.

## Live config from a "static" contract

opencode's documented contract for custom OpenAI-compatible providers (the
["Atomic Chat" pattern](https://opencode.ai/docs/providers/#atomic-chat))
requires a **static** `models` block listing every model ID the upstream
server serves. opencode does not auto-fetch `/v1/models` at runtime for
custom-config providers.

Rather than maintain the model list by hand or attempt fragile runtime
monkey-patches against opencode's internals, this binary **materialises the
static config from the live `/v1/models` response before opencode
launches**. Every invocation re-syncs. The model list is therefore always
live; the "static" nature of opencode's config is just an implementation
detail this tool absorbs on the user's behalf.

A second, independent rationale governs which fields are allowed into the
generated block at all: opencode's per-model schema lenient-strips unknown
keys but fails the *whole config decode* on a type mismatch in a known
field, and when that decode fails, opencode's own loader catches the error
and silently substitutes an empty config, wiping every provider's models
block. This tool is therefore paranoid about types on the way out: a field
that cannot be emitted with the correct type is omitted entirely rather than
emitted with a placeholder, because a missing field is recoverable and a
decode failure is not. See `spec/opencode-config.md` for the exact
per-field rules this produces.

## No forks, no patches

opencode itself is unmodified. The adapter lives entirely outside both
upstream projects: no plugins, no patches, no npm packages, no JavaScript.
Everything this tool does, it does from the outside, by writing files
opencode already knows how to read and by talking to amplifier-agent over
the same HTTP face any client would use.

## The two bridges

amplifier-agent exposes two server-side resource kinds, skills and modes,
over `GET /v1/skills` and `GET /v1/modes`. Each is bridged into a different
opencode-native concept, because each maps onto something opencode already
has:

```
skill -> opencode slash command    a skill is a one-shot, user-invoked action;
                                    opencode's command files are exactly that
mode  -> opencode primary agent    a mode is a standing behavioral stance for
                                    the whole session; opencode's agent
                                    picker is exactly that
```

The mode directive travels in the generated agent file's **body**
(`[amplifier-agent:mode=<name>]`), not in opencode's config, because
opencode forwards a primary agent's body to the backend as a system message
on every turn as part of its own existing behavior. That means mode
persistence across a session is free: nothing in this tool has to track
"which mode is this session in" separately, because opencode re-sends the
directive on every single turn as a side effect of a mechanism it already
has. Putting the same information in config instead would have required
this tool (or amplifier-agent) to read opencode's config back out at request
time, a channel that does not exist in the other direction. See
`spec/skills-and-modes-bridge.md` for the byte-exact file shapes and
the ownership-manifest reconciliation that keeps repeated runs idempotent.

## Where the expertise lives

This tool contains no model logic and no prompt content. There is no system
prompt authored here, no tool-calling logic, no reasoning about what the
user asked for. Every intelligent behavior a user experiences, the actual
reply content, tool use, skill execution, mode behavior, happens server-side
inside amplifier-agent. This tool's entire job is discovery (asking
amplifier-agent what it can currently do) and translation (expressing that
answer in a shape opencode's config and file-based extension points already
understand). That split is deliberate: it is what lets amplifier-agent ship
new skills, modes, or providers without this tool ever needing a code change
to surface them.

## Key decisions

1. **Regenerate the config every launch instead of patching it once.**
   Keeps the model list, and the skills/modes bridge, always in sync with
   whatever amplifier-agent is currently serving, at the cost of the
   `provider.<provider-id>` sub-block always being a full replace rather
   than a merge (see `spec/opencode-config.md`, Preservation).
2. **Delegate all credential storage to amplifier-agent.** This tool has no
   credentials file and no in-memory cache of a secret beyond a single
   subprocess call. One source of truth for what will actually resolve at
   serve time, and one less place a secret could leak from.
3. **Pin the self-heal target to an exact tag, never a moving branch.** The
   silent, launch-time auto-install of amplifier-agent targets
   `AGENT_PINNED_REF` (a specific `vX.Y.Z` tag), so a reliability mechanism
   never drags a user onto un-vetted `main`.
4. **Two independent ownership manifests, not one shared state file.** The
   skills bridge and the modes bridge each reconcile against their own
   manifest. A failure or a stale manifest in one face cannot corrupt the
   other's bookkeeping.
5. **Never stop the spawned server.** The server is meant to outlive a
   single `launch`/`exec` cycle so a later invocation can reuse it (and
   whatever session state it holds) instead of paying a cold start every
   time. This tool owns starting the server; stopping it is explicitly out
   of scope (see `spec/cli.md`, Non-goals).
6. **`exec`, not subprocess-wrap, for opencode itself.** Signal handling and
   terminal control belong to opencode once it is running; a supervising
   wrapper process would have to reproduce that handling itself for no
   benefit.

## Pointers

- [`docs/SPEC.md`](SPEC.md) -- the contracts this architecture implements
- [`docs/E2E_TESTING.md`](E2E_TESTING.md) -- how the whole thing is validated
  end to end, against the real opencode TUI and a real amplifier-agent
  server
- [`ISSUES.md`](ISSUES.md) -- tracked gaps between the stated contract
  and current behavior
- [`../AGENTS.md`](../AGENTS.md) -- the process anchor for working on this
  repo
