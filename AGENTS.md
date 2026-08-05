# AGENTS.md: amplifier-app-opencode

Notes for AI agents and humans working **on** this repo. For what the repo
*produces* and how to install/use it, see [`README.md`](README.md).

## TL;DR

This is a **single-package Python CLI**: one launcher binary
(`amplifier-opencode`) that adapts the [opencode](https://opencode.ai) TUI
onto a local [amplifier-agent](https://github.com/microsoft/amplifier-agent)
HTTP server. There is no engine/wrapper split like the sibling
`amplifier-agent` repo -- this whole repo is the adapter, and the whole
adapter is one binary (`src/amplifier_app_opencode/cli.py`).

The thing that bites people: the agent-version floor (`MIN_AGENT_VERSION` in
`prereqs.py`) is a **requirement statement**, not a mirror of whatever
amplifier-agent last released, and it moves only when amplifier-agent's own
release process decides it should. Read
[Cross-component invariants](#cross-component-invariants) before touching
`prereqs.py`, the version in `pyproject.toml`, or anything under the
ownership-manifest reconciliation in the skills/modes bridge.

---

## What lives where

```
src/amplifier_app_opencode/cli.py            The whole adapter: dispatch, self-heal
                                              preflight, model discovery, config write,
                                              skills/modes bridge, doctor, update
src/amplifier_app_opencode/prereqs.py        Version floors (MIN_AGENT_VERSION), the
                                              installer/self-heal logic for amplifier-agent
                                              and opencode
src/amplifier_app_opencode/onboarding.py     The interactive provider-credential wizard
src/amplifier_app_opencode/platform_utils.py OS/shell detection used by the self-heal
                                              installers
install.sh                                   The one-line installer. Installs ONLY this
                                              CLI; every other component is self-healed
                                              by the CLI itself on first run
docs/spec/                                   The contracts, one file per surface. See
                                              Docs map
docs/ARCHITECTURE.md                         Why the adapter is built this way; internal
                                              structure
tests/e2e/                                   The only test tier. DTU-based end-to-end
                                              suites, against the real installed CLI in
                                              a container. See Routing a change
docs/ISSUES.md                                   Deferred, non-blocking work, recorded so it
                                              can be reopened cold
CHANGELOG.md                                 User-visible changes, Keep a Changelog format
Makefile                                     Canonical local command surface. There is no
                                              CI in this repo; see Build, lint, test
```

## Routing a change: spec, e2e, eval

```
docs/spec/     the contract, in prose
tests/e2e/     proves the contract against the real installed CLI, running
               against a real opencode TUI and a real amplifier-agent server,
               inside a Digital Twin Universe (DTU) container
```

**There is no mocked-unit-test tier.** A test that reaches inside the tool
(monkeypatching its own internals, calling its own private functions
directly) asserts something no caller of this CLI ever promised, and drifts
from the real contract the moment the internals it mocks change shape --
silently, since nothing forces the mock to track the real signature. The
e2e tier is the only test tier, and it runs the real installed binary in a
container, driven the same way an end user drives it: proving the contract,
not the implementation.

A change to a contract updates `docs/spec/`. A change to observable behavior
gets an e2e case. A change to judgment-laden output quality (a model's reply
content, not this tool's own output) is what the eval harness in
`amplifier-agent` measures, not this repo -- see
[Common pitfalls](#common-pitfalls).

Some behavior that used to have mocked-unit coverage now has none -- see
`docs/ISSUES.md`'s "Known coverage gaps after removing the mocked unit
tier" for the full accounting of what was lost versus what is merely
uncovered but reachable.

The e2e suite runs against **the real installed binary**, not an in-process
import: `amplifier-opencode` is installed inside the DTU exactly as an end
user would install it, and the suite drives it as a subprocess (or, for the
TUI suites, inside `tmux`).

---

## Docs map

Two entry points: [`docs/SPEC.md`](docs/SPEC.md) for contracts,
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for structure. Everything else
hangs off one of those.

```
docs/ARCHITECTURE.md    what the system is and why it is built this way
docs/SPEC.md            index of the contracts (docs/spec/*.md)
docs/spec/              the contract specifications, one file per surface
docs/E2E_TESTING.md     the end-to-end test framework and how to add a suite
```

## Build, lint, test

`Makefile` at the repo root is the canonical command surface. **There is no
CI in this repo** -- this Makefile is the only gate, local and pre-PR alike.

```bash
uv sync --all-extras --dev

make check              # ruff lint + ruff format --check. Seconds. No container needed.
make fmt                # ruff format + ruff lint --fix. No container needed.
make e2e SUITE=<name>   # run one e2e suite against a DTU. Needs a container runtime.
make e2e                # run every suite. Slow (several minutes); prints a warning.
make e2e-up             # bring up the warm DTU without running a suite
make e2e-down           # tear the warm DTU down
```

See [`docs/E2E_TESTING.md`](docs/E2E_TESTING.md) for what a DTU run actually
needs (Incus, `ANTHROPIC_API_KEY`, and by default Docker for the local-mirror
Gitea) and for the fast inner loop (`--skip-setup`, `refresh`).

## Cross-component invariants

These are the rules that have bitten contributors, or would bite the next
one silently. Honor them.

### 1. The version floor is a requirement statement, not a mirror

`MIN_AGENT_VERSION` in `prereqs.py` states the **minimum amplifier-agent
version whose behavior this tool depends on**, with the exact dependency
named in a comment above it (currently: the HTTP face honoring
`provider.config` from `--host-config` under `serve`). `AGENT_PINNED_REF`
(`f"v{MIN_AGENT_VERSION}"`) and `AGENT_HARD_FLOOR` (`= MIN_AGENT_VERSION`)
both derive from it mechanically; there is exactly one dial.

**Do not bump this opportunistically** just because a newer amplifier-agent
exists. `amplifier-agent`'s own release skills
(`amplifier-agent-start-release-process` and
`amplifier-agent-finish-release-process`) already own the full decision of
whether a given amplifier-agent release moves this floor, and execute the
bump and the downstream PR here when it does. A floor bump landing in this
repo should trace back to that upstream decision, not to "let's use the
latest."

### 2. The version is declared in two places that must agree

`pyproject.toml`'s `version` and `src/amplifier_app_opencode/__init__.py`'s
`__version__` are independent hand-edited strings. `--version` reports the
latter; the installer and `update` report the former, read from package
metadata. `doctor` reports neither. Nothing compares the two, so bumping one
without the other under-reports the installed version to whichever surface
reads the stale one -- the exact failure mode a past release shipped (see
`CHANGELOG.md`). Bump both in the same commit; see `docs/ISSUES.md` for the
tracked gap.

### 3. The e2e harness needs a sibling checkout, and mirrors BOTH repos

The DTU install story resolves `amplifier-agent` as a **sibling checkout** of
this repo (`<parent>/amplifier-agent` next to `<parent>/amplifier-app-opencode`).
By default, the harness mirrors **both** local working trees (committed +
staged + unstaged + untracked) into an in-DTU Gitea and installs from there,
so the suite validates uncommitted cross-repo changes before either side is
published. If the sibling checkout is missing, provisioning fails loud; it
never silently falls back to published code. Pass `--published` to install
published upstream versions instead (no sibling checkout or Docker needed in
that mode).

### 4. Generated files are reconciled through ownership manifests

The skills and modes bridges each keep their own manifest
(`.amplifier-generated-commands.json`, `.amplifier-generated-agents.json`)
recording exactly which files in their output directory this tool generated.
**Never write into `~/.config/opencode/command/`, `.../agent/`, or their
project-scoped equivalents by hand.** A file not recorded as generated is
treated as user-owned and is never overwritten, never reclaimed, and never
deleted on prune -- even if it was actually this tool's own output from
before the manifest existed or was lost.

### 5. The spawned server is never stopped by this tool

`start_amplifier_agent` spawns `amplifier-agent serve chat-completions` with
`start_new_session=True` specifically so it survives this process's `exec`
into opencode. There is no shutdown/stop path anywhere in this codebase for
that server; stopping it is left to the user or to amplifier-agent's own
lifecycle. Do not add one without first reading `docs/spec/cli.md`'s
Non-goals -- this is a stated contract, not an oversight.

### 6. Discovery is fixed at server startup

amplifier-agent populates `app.state.available_skills` /
`available_modes` once, at server boot, not per-request. A test (or a
person) that seeds a new skill or mode file against an **already-running**
server will not see it until the server restarts. Any e2e case that seeds
content must restart the server after seeding, or it will silently pass
against stale state rather than exercising the new content at all.

---

## Specs are the durable output, not design docs

Design docs are **transient working artifacts**, not repo content. Write one
if it helps you think, share it in the PR description, then throw it away.
Do not check one in.

What is durable is [`docs/spec/`](docs/SPEC.md). **A change to a contract and
the spec update for it belong in the same change.** If your PR alters a CLI
flag, the generated opencode config, the skills/modes bridge, install/update
behavior, or any other documented contract, the matching `docs/spec/*.md`
edit is part of that PR, not a follow-up.

The spec is the record of *what the contract is*; the code is the record of
*how it is met*; git history is the record of *why*.

---

## Commits and PRs

Conventional commits, mostly **without** a scope since this is a single
package: `feat: ...`, `fix: ...`, `chore: ...`, `docs: ...`, `refactor: ...`.
A scope is added only when narrowing to one specific area, and observed
scopes in history are named after the surface they touch, not a subsystem:
`feat(cli)`, `fix(cli)`, `feat(update)`, `feat(launch)`, `docs(readme)`,
`docs(prereqs)`. A release-worthy commit's PR title carries the resulting
version in parentheses, e.g. `chore: raise amplifier-agent floor, document raw
LLM payload capture (X.Y.Z)`.

PRs should go in a stack when they build on each other (see the `gh-stack`
skill in the workspace); each PR in the stack is reviewed and merged in
order.

---

## Common pitfalls

- **Hand-editing a generated command/agent file, or the opencode config's
  provider block.** Both are overwritten on the next launch; see invariant
  4. Edit the source skill/mode in amplifier-agent, or the config keys
  outside `provider.<provider-id>`, instead.
- **Assuming `--yes` is shared.** `update`'s `-y/--yes` is local to that
  subcommand and independent of the global `--yes`; setting one does not set
  the other (see `docs/spec/cli.md`).
- **Running `update` against a dev checkout without `--force`.** Stage 1
  refuses to overwrite an editable install; this is a hard failure of the
  whole `update` invocation, not a warning.
- **Bumping the agent-version floor because a newer release exists.** See
  invariant 1. The decision lives upstream.
- **Forgetting the sibling checkout for e2e.** If `amplifier-agent` is not
  checked out next to this repo, local-mirror provisioning fails loud. Clone
  it as a sibling, or pass `--published`.
- **Backgrounding a long e2e run without detaching it.** DTU launch, install,
  and a full suite run take several minutes. Backgrounding one with a plain
  `nohup ... &` inside a tool call lets a tool timeout kill the whole process
  group and orphan the DTU container. Launch fully detached and poll instead:

  ```bash
  setsid bash -c '<run cmd> > /tmp/run.log 2>&1' < /dev/null > /dev/null 2>&1 &
  ```
- **Treating `uv.lock` as pinning consumer installs.** It is gitignored (see
  `docs/ISSUES.md`); every `uv tool install --from git+...` install resolves
  dependencies fresh against `pyproject.toml`'s ranges, not a committed lock.

---

## What "done" looks like

For a typical change:

1. `make check` passes clean (ruff lint + format-check)
2. If user-facing behavior changed: the relevant `tests/e2e/suites/<feature>/`
   passes (`make e2e SUITE=<feature>`)
3. If a documented contract changed, the matching `docs/spec/*.md` is updated
   in the same PR
4. If the change touches `MIN_AGENT_VERSION`, it traces to an upstream
   amplifier-agent release-process decision (see invariant 1), stated in the
   PR description
5. `CHANGELOG.md` updated under `[Unreleased]` (or a new version section) if
   user-visible

---

## When in doubt

- Read the relevant [`docs/spec/*.md`](docs/SPEC.md) first, but verify
  against `src/amplifier_app_opencode/cli.py` before relying on a detail: the
  spec records intent, and one known gap (the modes-face name-validation
  deviation, see `docs/ISSUES.md`) is already recorded as a place where the code
  and the stated contract disagree.
- For the e2e framework itself (how the TUI is driven, how the AI judge
  works, how to add a suite): [`docs/E2E_TESTING.md`](docs/E2E_TESTING.md).
- For whether an amplifier-agent release moves this repo's version floor:
  that decision and its execution live in amplifier-agent's own release
  skills, not here.
