# Development

Maintainer guide: how to set up a machine, which command to run when, and how the development
skills drive feature, bugfix, and release work for this repo.

This file owns **prerequisites, setup, the command surface, and the e2e harness**. Two other
files own the rest, and this one links rather than repeats:

- [`AGENTS.md`](AGENTS.md): repo layout, cross-component invariants, commit and PR conventions,
  what "done" looks like
- [`docs/E2E_TESTING.md`](docs/E2E_TESTING.md): how the e2e framework works internally and how to
  add a suite

## Prerequisites

Two tiers. You only need the second when you run the things that use it.

**Core.** Enough for `make check` and `make fmt`.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Python `3.11` or later, resolved by `uv` from `requires-python` in `pyproject.toml`. There is no
`.python-version` file. Verify with:

```bash
make check
```

**The container harness.** Needed only by `make e2e`.

```bash
uv tool install git+https://github.com/microsoft/amplifier-bundle-digital-twin-universe@main
```

Plus the runtimes and tools that CLI, and the local-mirror step, drive:

- **Incus** runs the DTU containers. Verify with `incus version`. First use needs a one-time
  `incus admin init`.
- **Docker** runs the Gitea container used to mirror your local working trees. Verify with
  `docker info`.
- **`amplifier-gitea`** CLI manages that Gitea container's lifecycle (create, status, token,
  destroy); the local-mirror step shells out to it directly.
- **`tmux`** drives the actual TUI keystrokes for the TUI suites. It runs inside the DTU (the
  provisioning script installs it there), not on the host.

**Environment.**

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # required for e2e runs; passed through to the DTU
                                       # and used host-side by the AI-user judge
```

## Setup

```bash
uv sync --all-extras --dev
make check
```

**The e2e harness needs an `amplifier-agent` checkout as a sibling directory**, next to this repo:

```
<parent>/
  amplifier-app-opencode/   # this repo (tests live here)
  amplifier-agent/          # sibling checkout
```

By default, provisioning mirrors **both** working trees (committed, staged, unstaged, and
untracked, minus gitignored) into an in-DTU Gitea and installs from there, so uncommitted
cross-repo changes are what actually gets tested. This is the most common setup failure: if the
sibling checkout is missing, provisioning fails loud, it never silently falls back to published
code. Pass `--published` to skip local mirroring and install published upstream versions instead
(no sibling checkout or Docker needed in that mode).

## The command surface

only gate, local and pre-PR alike.

| Target | Cost | When |
|---|---|---|
| `make check` | seconds, no container | Constantly, while iterating. `ruff check` + `ruff format --check` on `src/` and `tests/` |
| `make fmt` | seconds, no container | Auto-fix formatting and lint |
| `make e2e SUITE=<name>` | minutes, needs a container runtime | One e2e suite against a DTU |
| `make e2e` | several minutes, needs a container runtime | Every suite; prints a warning first |
| `make e2e-up` | minutes | Bring up the warm DTU without running a suite |
| `make e2e-down` | seconds | Tear down the warm DTU |

There is no bare `test` target. `tests/e2e/*.py` self-skip green when `amplifier-digital-twin` is
absent or no warm DTU exists, so a plain `pytest` over the whole tree would report green without
ever going through the harness.

## Development skills

Three skills under `.amplifier/skills/`, user-invocable only (`disable-model-invocation: true`),
invoked by name with the work as the argument, from a session whose working directory is the
repo root:

```
/amplifier-opencode-new-feature <what you want to build>
/amplifier-opencode-bugfix <the bug report, verbatim>
/amplifier-opencode-release <what you are cutting>
```

### `amplifier-opencode-new-feature`

Ideation through working end-to-end in a DTU, using E2E-test-driven development. Orients you in
the specs, makes you choose among options before writing code, walks red then green, and stops
before release. New suites land in `tests/e2e/suites/<feature>/`; nothing under
`tests/e2e/framework/` should need to change.

### `amplifier-opencode-bugfix`

Report through verified fix. Rules out the cheap non-code causes first (a stale `serve` process
holding fixed discovery state, a stale DTU install, a version-floor mismatch), pins a root cause
before touching code, then makes you answer one gate question: why did e2e miss this. Stops at a
verified fix; it does not release.

### `amplifier-opencode-release`

Bounds the commit range, sweeps the diff for anything that should not ship publicly, bumps both
version declarations in agreement, writes the changelog, confirms the agent-version floor
decision (which it never makes on its own), runs the local gate, and opens the release PR.
Merging that PR to main **is** the release; there is no tag or publish step.

## The e2e harness

`tests/e2e/` is the only test tier. It drives the real opencode TUI inside a DTU container against
the real `amplifier-opencode -> opencode -> amplifier-agent` path.

```bash
uv run python tests/e2e/cli.py up                       # provision a warm DTU
uv run python tests/e2e/cli.py run                      # run every suite (auto-provisions if not warm)
uv run python tests/e2e/cli.py run modes                # scope to one suite
uv run python tests/e2e/cli.py run modes --skip-setup   # fast inner loop: reuse the existing warm DTU
uv run python tests/e2e/cli.py refresh                  # re-mirror local trees + reinstall in place
uv run python tests/e2e/cli.py run skills --ephemeral   # tear the DTU down after the run
uv run python tests/e2e/cli.py down                     # destroy the DTU
uv run python tests/e2e/cli.py list                     # list discovered suites, no DTU needed
```

`run` shells out to `uv run pytest tests/e2e/suites/<selected> -m dtu`. Suite names are passed
positionally; an unknown name fails loud with the valid list. Real suites today: `agent_integration`,
`bridge`, `chat`, `cli`, `config`, `modes`, `onboarding`, `shadowing`, `skills`, `traversal`.

**Things that will bite you.**

- **Discovery is fixed at agent server startup.** amplifier-agent populates its skill/mode listing
  once, at boot, not per request. A test (or a person) that seeds a new skill or mode file against
  an already-running server sees nothing until the server restarts; any case that seeds content
  must restart the server after seeding, or it silently passes against stale state instead of
  exercising the new content at all.
- **Long runs must be detached.** A full DTU launch, install, and suite run takes several minutes
  (roughly ten minutes for the full suite). Backgrounding one with a plain `nohup ... &` inside a
  tool call lets a tool timeout kill the whole process group and orphan the DTU container. Launch
  fully detached and poll instead:

  ```bash
  setsid bash -c '<run cmd> > /tmp/run.log 2>&1' < /dev/null > /dev/null 2>&1 &
  ```

## What is not automated

`make check` passing does not mean the contract suite passed: the e2e suite needs a container
runtime CI cannot provide, so it never runs there. Everything that proves a contract holds runs
locally, on a machine with Incus and Docker, and that local run is the only gate it gets.

## Related

- Repo layout, cross-component invariants, commit/PR conventions, what "done" looks like:
  [`AGENTS.md`](AGENTS.md)
- The e2e framework internals and how to add a suite: [`docs/E2E_TESTING.md`](docs/E2E_TESTING.md)
- The contracts these tests prove: [`docs/spec/`](docs/spec/)
