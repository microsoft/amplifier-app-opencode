# E2E Testing

End-to-end tests that drive the real `opencode` TUI inside an isolated Digital Twin
Universe (DTU) container, against the whole `amplifier-opencode -> opencode ->
amplifier-agent` path. They prove the integration actually works from a realistic
install, the way a user drives it: launch the TUI, pick a model, send a message, read
the reply.

Unlike a headless API test, these exercise the actual terminal UX: the model picker,
keyboard navigation, and streamed responses on screen. The framework drives the TUI via
`tmux` and judges the on-screen result with an AI user, so assertions are about what a
person would actually see, not brittle substring matches.

This document describes the framework. The individual tests live under
`tests/e2e/suites/<name>/` and are the source of truth for what is covered.

## Layout

```
tests/e2e/
  cli.py                        # the up / run / down / refresh / list entry point
  conftest.py                   # pytest fixtures (warm-DTU, opencode TUI session, AI judge)
  framework/                    # the machinery (stable; rarely touched)
    dtu.py                      # amplifier-digital-twin subprocess wrappers (launch, exec, update, file-push, destroy)
    dtu_manager.py              # provision / refresh / teardown orchestration
    local_mirror.py             # mirrors local working trees into an in-DTU Gitea
    state.py                    # warm-DTU state file
    driver.py                   # TmuxTuiDriver: drives tmux locally or over the DTU exec boundary
    judge.py                    # AIUserJudge: yes/no verdict on a captured screen
    harness.py                  # Step / TUICase model + run_case + reusable step builders
    assertions.py               # ground-truth checks (agent log, session events)
    progress.py                 # timestamped progress logging
    provisioning/               # how the opencode stack is installed in the DTU
      profile.yaml
      install-opencode-stack.sh
  suites/                       # the tests (grows per feature)
    <name>/
      cases.py                  # TUICase data (TUI suites)
      test_<name>.py            # thin pytest wrapper parametrizing the cases
      conftest.py                # fixtures (non-TUI suites; also used by TUI suites that seed content)
      fixtures/*.md.tmpl         # seed skill/mode file templates
```

Suites today: `chat`, `modes`, `shadowing`, `skills`, `traversal`. `chat`, `modes`, and
`skills` follow the `cases.py` + `test_<name>.py` TUI shape above. `shadowing` and
`traversal` are non-TUI: there's nothing to read off a screen, so their `conftest.py`
seeds files from `fixtures/*.md.tmpl`, drives `amplifier-opencode prepare` via
`driver.run_command`, and the tests assert on its stdout and the resulting filesystem
state.

Framework code is the reusable half; `suites/` is where features add tests. To test a
new area, add a `suites/<name>/` package. Nothing in `framework/` needs to change.

## Prerequisites

The harness shells out to the DTU CLI, which needs a container runtime. All of this is
host-side.

```bash
# uv (runs everything; this harness is never installed, always `uv run`)
curl -LsSf https://astral.sh/uv/install.sh | sh

# DTU CLI (Incus-backed environments)
uv tool install git+https://github.com/microsoft/amplifier-bundle-digital-twin-universe@main
```

Transitive runtimes:

- Incus (DTU container runtime). Verify `incus version`. One-time `incus admin init`.
- `ANTHROPIC_API_KEY` must be set in your host env. It is passed through to the DTU and is
  required to run a real model. The AI-user judge also uses it (host-side).

The provisioned DTU installs `opencode` at a pinned version (`1.17.20`), plus
`amplifier-agent` (from latest main; must satisfy amplifier-opencode's minimum,
currently `>= 0.10.0`) and `amplifier-opencode`, plus `git`, `curl`, `uv`, and `tmux`.

## Running

```bash
# from the amplifier-app-opencode repo root
uv run python tests/e2e/cli.py up          # provision a warm DTU (installs the opencode stack)
uv run python tests/e2e/cli.py run          # run all suites (auto-provisions if not warm)
uv run python tests/e2e/cli.py run modes    # scope to one suite (chat, modes, shadowing, skills, traversal)
uv run python tests/e2e/cli.py down         # destroy the DTU
uv run python tests/e2e/cli.py list         # list discovered suites (no DTU needed)
```

`run` ensures a warm DTU, then shells `uv run pytest tests/e2e/suites/<selected> -m dtu`.
Feature selection is directory-based: bare words matching a `suites/` subdirectory scope
the run; an unknown name fails loud with the valid list.

A normal `uv run pytest` (without the harness) stays green: the e2e tests self-skip when
`amplifier-digital-twin` is absent or no warm DTU exists.

### Fast inner loop

```bash
uv run python tests/e2e/cli.py run modes --skip-setup    # re-run against the existing warm DTU (no reprovision)
uv run python tests/e2e/cli.py refresh                   # re-mirror local trees + reinstall in place
uv run python tests/e2e/cli.py run skills --ephemeral    # tear the DTU down after the run
```

`--skip-setup` reuses the warm DTU as-is (fastest; ~20s). `refresh` re-pushes your local
snapshots and reinstalls inside the running DTU so code edits propagate without a full
relaunch (see "Running against local code" below).

## How the TUI is driven

`framework/driver.py` (`TmuxTuiDriver`) is the heart. It runs the opencode TUI under
`tmux` at a fixed size (120x40) and drives it with `send-keys` / `capture-pane`. The same
code path works locally (`tmux ...`) and inside the DTU
(`amplifier-digital-twin exec <id> -- tmux ...`) via an `exec_prefix`.

Three rules the driver enforces (do not remove):

- `remain-on-exit on` is set right after `new-session`, so a crashed or exited opencode
  leaves a readable final screen instead of the session vanishing.
- Each tmux argument is passed as a separate token; the driver never wraps tmux in
  `bash -c` (quoting hazards, and the DTU exec boundary shlex-joins args).
- `capture-pane -p` pads with trailing blank lines up to the row count; the driver strips
  them before returning the screen.

Readiness and completion are detected with `wait_for_text` gates on known screen markers,
never fixed sleeps. Key markers:

- Main screen ready: `tab agents`.
- Model picker open: `Select model`.
- Response settled: the completion duration (regex `\d+\.\d+s`) or the bottom-right token
  counter (regex `\d+(\.\d+)?K \(\d+%\)`).

## Verification: the AI user

All pass/fail decisions about response content go through `framework/judge.py`
(`AIUserJudge`). It hands the captured screen plus a natural-language question to a model
(`claude-sonnet-5`) and gets back a strict `{passed, reason}` verdict. Navigation
keystrokes are deterministic; only the semantic judgment of an arbitrary screen is
delegated to the model. This avoids brittle substring assertions on a rendered TUI.

Ground-truth (non-AI) assertions live in `framework/assertions.py` and read the
integration's own artifacts inside the DTU:

- `agent_log_contains(...)` greps the server log at `/tmp/amplifier-agent.log` (for lines
  like `POST /v1/chat/completions HTTP/1.1" 200 OK`).
- `session_events_exist(...)` finds the session event log at
  `~/.amplifier-agent/state/workspaces/opencode/sessions/http-<ses-id>/context-intelligence/events.jsonl`.

## The case model

A test case is data: a `TUICase` (a named list of `Step`s) in `framework/harness.py`.
Step kinds:

```python
Step("wait", "Select model")                          # gate on a screen marker (regex=True for a pattern)
Step("send_keys", "Enter")                            # named keys, space-separated (e.g. "Left Left Enter")
Step("send_text", "sonnet 5 amplifier")               # literal text into the focused input
Step("judge", "Does the reply greet the user?")       # AI-user yes/no about the current screen
Step("assert_log", "POST /v1/chat/completions")       # server log contains a string
Step("assert_events", "")                             # session events.jsonl exists
```

Reusable builders compose the common flow so new tests stay short:

- `setup_select_sonnet5()` -> open the `/models` picker, filter to the single
  `Claude Sonnet 5 / Amplifier` row, select it, confirm on the footer.
- `send_and_settle(text)` -> type a message, send it, wait for the response-settled marker.

`run_case(case, driver, judge)` dispatches each step in order. A `judge` failure raises
with the reason and the full screen attached, so failures are diagnosable.

## Adding a suite (4 steps)

1. `mkdir tests/e2e/suites/<name>` and add an `__init__.py`.
2. In `cases.py`, build a `list[TUICase]` from `Step`s, reusing `setup_select_sonnet5()`
   and `send_and_settle(text)` from `framework.harness`.
3. In `test_<name>.py`, parametrize the cases and mark the module `pytestmark =
   pytest.mark.dtu`:

```python
import pytest
from framework.harness import run_case
from suites.myfeature.cases import CASES

pytestmark = pytest.mark.dtu

@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_myfeature(case, opencode_session, judge):
    run_case(case, opencode_session, judge)
```

4. Run it: `uv run python tests/e2e/cli.py run <name>`.

A non-TUI suite is also a supported shape (see `shadowing` / `traversal`): skip
`cases.py`, seed fixture files from `fixtures/*.md.tmpl` in `conftest.py`, drive
`amplifier-opencode prepare` with `driver.run_command`, and assert on its stdout and
the resulting filesystem state instead of a captured screen.

## Running against local code (default)

By default the framework installs BOTH `amplifier-app-opencode` and `amplifier-agent`
from your LOCAL working trees, so the suite validates uncommitted changes before either
repo is published. This is the whole point of the harness, so it is the default.

Requires Docker running (for the Gitea container). How it works:

1. `framework/local_mirror.py` stands up (or reuses) a dedicated Gitea container named
   `oc-e2e` on port `10130`, and snapshot-pushes each repo's working tree (committed +
   staged + unstaged + untracked, minus gitignored, plus tracked deletions) into it. Your
   source repos are never mutated (no add/commit/stash).
2. The DTU profile's `url_rewrites` redirect `github.com/microsoft/amplifier-agent` and
   `github.com/microsoft/amplifier-app-opencode` to that mirror, so the install script's
   `uv tool install --from git+...` lines pull your local trees. `GITEA_URL` /
   `GITEA_TOKEN` are passed as launch `--var` values to activate the rewrite.
3. `amplifier-agent` is resolved as a sibling checkout of `amplifier-app-opencode`. If it
   is missing, provisioning fails loud (it never silently falls back to published code).

Repo layout expected:

```
<parent>/
  amplifier-app-opencode/   # this repo (tests live here)
  amplifier-agent/          # sibling checkout, mirrored alongside
```

`refresh` re-pushes the local snapshots and reinstalls in place via the DTU engine's
`update` verb (which re-applies `url_rewrites`), so edits propagate without a full
relaunch. A plain re-exec of the install script would reinstall from GitHub and silently
miss local changes, which is why `refresh` routes through `update`.

### Installing published versions instead

To run against published upstream versions instead of your local trees, pass
`--published` (sets `OC_E2E_PUBLISHED=1` for that invocation). Docker/Gitea are not needed
in this mode.

```bash
uv run python tests/e2e/cli.py up --published
uv run python tests/e2e/cli.py run chat --published
```

## Troubleshooting

- Tests skip with "no warm DTU": run `... cli.py up` first, or just use `run` (it
  auto-provisions).
- Inspect the live DTU directly:
  `amplifier-digital-twin exec oc-e2e -- amplifier-opencode doctor`,
  `amplifier-digital-twin exec oc-e2e -- tmux capture-pane -t oc -p`.
- A crashed TUI leaves a readable final screen (`remain-on-exit on`); capture the pane to
  see the last state.
- Local mirror not updating: Docker must be running (Gitea container). `refresh` re-pushes
  and reinstalls; a plain `--skip-setup` run does NOT re-mirror.
- Cleanup only touches this harness's resources: the `oc-e2e` DTU and the `oc-e2e` Gitea.
  Never destroy other DTUs or Gitea envs.
