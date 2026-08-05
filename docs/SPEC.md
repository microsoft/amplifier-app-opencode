# Specification

The contracts of `amplifier-opencode`. Each file under `spec/` defines one contract surface:
what callers may depend on and what is deliberately absent.

These are behavior specs. They describe observable behavior and external contracts, and they
stay true regardless of how the adapter is implemented internally.

## Index

### Surfaces users drive

```
spec/cli.md                        the complete command surface: dispatch, every global
                                    option and subcommand flag with defaults and env vars,
                                    --version output, and the exit-code map
spec/install-and-update.md         the one-line installer, the launch-time self-heal that
                                    brings amplifier-agent and opencode up to date, and the
                                    three-stage `update` command
spec/providers-and-credentials.md  how provider credentials are configured, stored, and
                                    reported: the onboarding wizard, provider discovery,
                                    secret hygiene, and doctor's provider section
```

### What it generates

```
spec/opencode-config.md            the generated opencode provider block: config file
                                    selection, model entry construction from live discovery,
                                    context clamping, preservation of existing keys, and
                                    atomic write guarantees
spec/skills-and-modes-bridge.md    how server-supplied skills and modes become opencode
                                    command and agent files: templates, ownership manifests,
                                    reconciliation, name safety, and conflict reporting
```

### What it depends on

```
spec/agent-integration.md          this tool's contract with the amplifier-agent process it
                                    drives: the version floor, spawn/reuse/shutdown lifecycle,
                                    and degradation when a consumed agent surface fails
```

### On disk

```
spec/file-locations.md             the complete inventory of every path this tool reads or
                                    writes, in both global and project scope, plus adjacent
                                    paths owned by other components
```

## Reading order

New to the codebase: start with `ARCHITECTURE.md`, then `cli.md` for the command surface,
then `agent-integration.md` for how this tool relates to the process it drives.

Debugging a failed launch: `install-and-update.md` for the self-heal preflight, then
`agent-integration.md` for the version floor and degradation behavior, then
`file-locations.md` for where the spawned server's log actually lives.

Changing what gets generated: `opencode-config.md` for the provider block and model
entries, then `skills-and-modes-bridge.md` for the command and agent files, then
`file-locations.md` for exactly where each lands.

Bumping the agent floor: `agent-integration.md` for the version-floor contract and its
ratchet history, then `install-and-update.md` for how the self-heal installs the pinned
tag, then `../CHANGELOG.md` for how the bump itself gets recorded.

## Conventions

Every spec file carries the same sections:

```
Scope             what it covers and what it does not
<contract>        the contract itself
Non-goals         surfaces deliberately absent or unsupported
```

**Non-goals are contracts.** An absent surface stays absent because callers depend on it not
existing. Do not introduce a surface listed under Non-goals without treating it as a breaking
change.

**Specs are implementation-agnostic.** They describe what a caller can observe: commands,
flags, generated file shapes, error messages, ordering guarantees. They do not name internal
functions, cite source lines, or describe how the behavior is produced. A spec should still be
correct if this tool were rewritten in another language. Implementation detail belongs in
`ARCHITECTURE.md`.

## Related documents

```
ARCHITECTURE.md      what the system is and how the pieces connect
E2E_TESTING.md       the DTU end-to-end test framework and how to add a suite
../AGENTS.md         the process anchor for working on this repo
ISSUES.md         tracked gaps between the stated contract and current behavior
../README.md         what this tool produces and how to install it, for end users
../CHANGELOG.md      the version history, including each version-floor bump and its reason
```
