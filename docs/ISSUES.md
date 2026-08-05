# Known Issues

Deferred, non-blocking work, recorded so it can be re-opened cold instead of
re-discovered from scratch. This is **not a backlog** to be worked
top-to-bottom. Fix an item when you are already in its neighborhood for
another reason; otherwise it waits here.

---

## ISSUE-002: The version is declared in two hand-edited places with nothing enforcing agreement

**Status:** Open. The failure mode that shipped in 0.2.0 is still reachable.

**Summary:** `pyproject.toml`'s `version` and
`src/amplifier_app_opencode/__init__.py`'s `__version__` are independent
hand-edited strings, currently both `0.4.0`. `--version` reports
`__version__` directly; `uv tool install` and the `update` path report the
packaging metadata derived from `pyproject.toml`. Nothing compares the two.
`tests/test_version.py` asserts `--version`'s output against `__version__`
itself, so it guards that `--version` reads the constant correctly but
cannot catch the constant drifting from `pyproject.toml`. `doctor` reports
neither.

### Why it matters

A silently under- or over-reported version misleads a user (or this tool's
own self-heal logic) into believing a different capability set is installed
than actually is. This is the same failure mode that shipped in 0.2.0
(`__version__` left at `0.1.3` after `pyproject.toml` moved to `0.2.0`; see
`CHANGELOG.md`, 0.3.0 "Fixed") -- nothing added since then prevents a
recurrence, only the one historical instance was corrected.

### Reproducer

Bump `pyproject.toml`'s `version` without touching `__init__.py`'s
`__version__` (or vice versa), then compare `amplifier-opencode --version`
against `pip show amplifier-app-opencode` / `importlib.metadata.version(...)`.
They disagree, and nothing in the test suite or `doctor` catches it.

### What is needed

A guard that reads `importlib.metadata.version("amplifier-app-opencode")`
and asserts equality with `amplifier_app_opencode.__version__`, or removal
of `__version__` in favor of reading metadata at `--version` time.

---

## ISSUE-003: One module carries the whole adapter

**Status:** Open, deferred deliberately.

**Summary:** `src/amplifier_app_opencode/cli.py` (roughly 2000 lines) carries
CLI dispatch, the self-heal preflight orchestration, model discovery,
opencode config generation, both bridges (skills and modes), and `doctor`.
Splitting it along those seams is a reasonable future refactor.

### Why it matters

A single large module is harder to navigate and raises the blast radius of
any one change, but the module is currently coherent (one cohesive adapter,
documented end to end by `docs/spec/`) and not obviously broken by its size.

### Reproducer

N/A, this is a structural observation, not a behavioral defect.

### What is needed

Nothing right now. Recording this so it is not silently done, or silently
avoided, as an accidental side effect of an unrelated change during this
process migration. If a change already touches a large fraction of
`cli.py`, splitting it in that same change is reasonable; do not go looking
for the work otherwise.

---

## ISSUE-004: The e2e provisioning script's header is stale

**Status:** Open, documentation-only, misleading to an auditor.

**Summary:** `tests/e2e/framework/provisioning/install-opencode-stack.sh`
carries this header verbatim:

```
# BEST-EFFORT / SKELETON: this script encodes the intended install story but has NOT yet
# been validated against a live DTU. Correctness of the version pins, installer URLs, and
# PATH wiring is a milestone M2 task. Everything about HOW the stack is installed lives
# in THIS file; the profile skeleton and dtu.py do not change when the install story does.
```

The first three sentences (through "milestone M2 task.") are stale: the
script is demonstrably in active use, the install path every e2e suite
provisions through today, referenced directly by `docs/E2E_TESTING.md`. The
fourth sentence is still accurate and load-bearing: this file remains the
single place the install story lives.

### Why it matters

Anyone auditing the container story for this repo, including a future
contributor or an AI agent doing exactly that, will read the stale sentences
and conclude the e2e provisioning path is unvalidated scaffolding rather than
the load-bearing script every suite run depends on.

### Reproducer

Read the file; the first three sentences of the header are the defect.

### What is needed

Delete the first three sentences (through "milestone M2 task."), next time
this file is touched for any other reason. Keep "Everything about HOW the
stack is installed lives in THIS file; the profile skeleton and dtu.py do
not change when the install story does." -- that part is still accurate.

---

## ISSUE-005: `uv.lock` is gitignored, so consumer installs are unpinned

**Status:** Open, accepted trade-off for now.

**Summary:** `uv.lock` exists on disk (`uv sync` generates it) but is listed
in `.gitignore`. Every `uv tool install --from git+...` install, whether via
`install.sh`, the e2e DTU provisioning script, or a manual install, resolves
dependencies fresh against `pyproject.toml`'s version ranges rather than a
committed lock.

### Why it matters

A dependency release that satisfies `pyproject.toml`'s ranges but breaks
this tool (an httpx or click regression, for instance) can reach a user
between one install and the next with no way to reproduce exactly what a
prior install resolved to.

### Reproducer

`git ls-files | grep uv.lock` returns nothing; `cat .gitignore` shows the
`uv.lock` entry.

### What is needed

A decision on whether to commit the lock for reproducibility (with the
tradeoff of a second thing to keep in sync) or to accept unpinned installs
as the intended behavior of a `uv tool install --from git` consumer. Not
resolving this now; recording the trade-off so it is a decision, not an
oversight.

---

## ISSUE-006: A non-JSON `/v1/models` response body is an uncaught exception, not a clean failure

**Status:** Open, verified.

**Summary:** `fetch_models` calls `r.raise_for_status()` then `r.json()`
with no handling for a response that returns HTTP 200 with a body that is
not valid JSON. `_run_launch`'s `except` clauses around the `fetch_models`
call catch only `httpx.HTTPStatusError` and `httpx.RequestError`; the
`json.JSONDecodeError` `r.json()` raises on a non-JSON body is neither, and
propagates unhandled instead of going through the same `click.ClickException`
path every other `/v1/models` failure mode uses.

### Why it matters

Every other `/v1/models` failure (network error, non-200, malformed `data`
shape) produces the same clean, documented message shape. A malformed body
on a 200 response instead prints a raw Python traceback to the user. The
process still exits 1 in this case (Python's default for an uncaught
exception reaching the interpreter), so the documented exit-code map is not
violated today, but the failure is not routed through this tool's own error
handling at all -- nothing here guarantees that stays true.

### Reproducer

```python
from click.testing import CliRunner
import amplifier_app_opencode.cli as cli

class FakeResp:
    status_code = 200
    def raise_for_status(self): pass
    def json(self):
        import json
        raise json.JSONDecodeError("Expecting value", "not json", 0)

cli.httpx.get = lambda *a, **k: FakeResp()
cli.server_is_running = lambda *a, **k: True
result = CliRunner().invoke(cli.main, ["--no-bootstrap", "prepare"])
print(result.exit_code, repr(result.exception))
```
Prints `1 JSONDecodeError('Expecting value: line 1 column 1 (char 0)')` --
an unhandled exception, not a `click.ClickException` message.

### What is needed

Catch the JSON decode failure alongside `httpx.HTTPStatusError` and
`httpx.RequestError` in `_run_launch`'s model-discovery step, and raise the
same `click.ClickException` shape the other two failure modes already use.

---

## ISSUE-007: The self-heal preflight's install-disabling parameter is unreachable from the command line

**Status:** Open, verified.

**Summary:** `ensure_agent` and `ensure_opencode` in `prereqs.py` both accept
`allow_install: bool` and print a distinct "(bootstrap disabled)" refusal
message when it is `False` and the corresponding tool is missing or below
the version floor. Both call sites of `prereqs.ensure_prerequisites` (the
default launch preflight and the `setup` subcommand) hardcode
`allow_install=True`. No CLI flag or environment variable sets it to
`False`; `--no-bootstrap` skips the preflight call entirely rather than
passing `allow_install=False` into it.

### Why it matters

Three user-facing refusal messages ("amplifier-agent not installed
(bootstrap disabled)", "amplifier-agent ... is below required ... (bootstrap
disabled)", "opencode not installed (bootstrap disabled)") are dead code: no
reachable path can print them. Anyone reading `prereqs.py` and designing
around the `allow_install` parameter -- for example assuming a "check but
don't install" mode is reachable and testable from the CLI -- is reasoning
about behavior nothing can trigger.

### Reproducer

`grep -n "allow_install=True" src/amplifier_app_opencode/cli.py` returns
every call site. `grep -n "allow_install" src/amplifier_app_opencode/prereqs.py
src/amplifier_app_opencode/cli.py` shows no call site and no CLI option that
threads a `False` value in.

### What is needed

Either wire `--no-bootstrap` (or a new flag) to pass `allow_install=False`
through to `ensure_prerequisites`, exercising the three refusal messages
from the command line, or remove the `allow_install=False` branches and the
parameter if a check-only mode is not actually wanted.

---

## ISSUE-008: A base URL without an explicit port spawns the server on a port the readiness probe never checks

**Status:** Open, verified.

**Summary:** `_port_from_url` (`src/amplifier_app_opencode/cli.py:1047`)
returns the base URL's explicit port when there is one and falls back to
`9099` otherwise. `_run_launch` (beginning at `cli.py:1074`) derives that
value at `cli.py:1130` and passes it to the spawn call at `cli.py:1140`, but
`wait_for_server_ready` (`cli.py:1148`) probes `base_url` unchanged. When the
base URL carries an explicit port the two agree. When it does not, the
server is started on `9099` while the probe goes to the URL's scheme-default
port.

### Why it matters

`--base-url http://127.0.0.1/v1` starts the server on `9099` and then polls
port `80` for 120 seconds before failing with a readiness timeout that names
neither port. The message sends the user to the server log, which shows a
server that started correctly, so the log contradicts the error. The
scenario is narrow, since the default base URL carries its port explicitly,
but the failure is silent, slow, and self-contradicting when it happens.

### Reproducer

```bash
amplifier-opencode --no-bootstrap --base-url http://127.0.0.1/v1 prepare
```

Expect the banner to report `port 9099` and the command to fail after 120s
with `amplifier-agent did not become ready within 120s`, while the server
log shows a healthy server bound to `9099`.

### What is needed

Derive the probe target from the same value used to spawn, or reject a base
URL whose port cannot be determined rather than guessing one. The fallback
constant is only correct when the base URL is the default, so silently
applying it to any other host is the actual defect.

---

## Minor defect log

### Stale or incorrect documentation

- A docstring on the opencode-config write path claims the loss of comments
  when rewriting a commented `.jsonc` config is documented in the README. It
  is not; `docs/spec/opencode-config.md` now states it directly.

### Known coverage gaps after removing the mocked unit tier

The seven `tests/test_*.py` modules (`test_host_config.py`,
`test_modes_bridge.py`, `test_skills_bridge.py`, `test_prereqs.py`,
`test_onboarding.py`, `test_platform_utils.py`, `test_version.py`) were
deleted once the e2e tier reached 54 passing tests across the non-TUI
suites. The behaviors below had coverage **only** from those mocked unit
tests and now have **no automated coverage at all**. This is a deliberate,
recorded trade -- not an oversight -- made because a test that reaches
inside the tool (monkeypatching its own internals) asserts something no
caller ever promised, per `AGENTS.md`'s routing rule. It is recorded here so
it can be re-opened cold rather than re-discovered from scratch.

Two categories, per the case-by-case audit:

#### Unreachable from a Linux e2e container (LOST)

These cannot be exercised by the e2e tier regardless of effort, because the
DTU is a Linux container:

- **`platform_utils` OS classification for non-Linux hosts**: `os_label()`
  returning `"darwin"`/`"windows"`, `is_darwin()`/`is_windows()`,
  `can_run_bash_installer()`'s `False` branch on native Windows, and the
  Windows/macOS-specific `opencode_install_method()` labels (`scoop`,
  `choco`, `brew`'s Homebrew-prefix match) -- none of these code paths can
  execute on a Linux container's `sys.platform`.
- **`preferred_opencode_install_method()`'s Windows branches**
  (`windows_npm`, `windows_none`) -- same reason; the posix/curl branch is
  the only one a Linux container can take, and even that branch's own
  install-selection logic is never actually invoked in current e2e runs
  (see below -- opencode is always pre-installed, so `ensure_opencode`
  never reaches its install-method-selection step).

#### Reachable, but not currently exercised (UNCOVERED)

These could be covered by additional e2e work; each entry says what that
work would look like.

- **`build_provider_block()` context-clamp math and `--max-context`**
  (`test_host_config.py`, 5 cases): no e2e case passes `prepare
  --max-context`, and `test_config.py` compares model **keys** against
  `/v1/models` but never compares the `limit.context` **values** against
  the live source, so even the unclamped verbatim-forwarding path is
  unasserted. Would take: an e2e case running `prepare --max-context N`
  and asserting every model's written context is `<= N`, plus a case
  comparing unclamped context values against the live `/v1/models` numbers.
- **`--host-config` pass-through semantics** (`test_host_config.py`, 2
  cases): whether omitting the flag leaves `host_config=None` (no
  auto-generated state dir) is unasserted; passing it is only exercised
  indirectly through the skills suite's host-config-driven skill-discovery
  case, which proves the file reaches the server but not the "no
  auto-generation" contract. Would take: a case running `launch
  --no-launch` with no flag and asserting no `.amplifier-opencode/`
  directory appears.
- **`doctor`'s per-provider-permutation exit logic** (`test_host_config.py`,
  3 of 4 doctor cases): the e2e `test_doctor_exit_code_matches_reported_failures`
  derives its expectation from whatever the live environment reports, so it
  covers the *rule* (`fail_count==0 and resolvable>=1` -> exit 0, else exit
  1) but never constructs the specific permutations (2-of-4 resolvable, a
  broken `providers list --json` call, zero resolvable). Would take:
  DTU scenarios with controlled provider env vars (zero-credential run in
  an isolated project, or a temporarily broken `amplifier-agent` shim on
  `PATH`).
- **`fetch_modes`/`fetch_skills` malformed-response normalization**
  (`test_modes_bridge.py` + `test_skills_bridge.py`, ~20 parametrized
  cases): the agent-integration suite's fake-server case
  (`test_skills_endpoint_failure_does_not_block_prepare`) covers the
  non-200 path for both faces, but connection-refused, 200-with-invalid-JSON,
  and the malformed-`shadowed`-shape variants (missing key, null, wrong
  type, mixed-good/bad list elements) are not reproduced. Would take:
  extending that suite's fake HTTP server to serve those specific malformed
  bodies.
- **Modes-side shadow-conflict reporting** (`test_modes_bridge.py`): the
  skills face's shadow reporting is proven end-to-end by
  `suites/shadowing`; there is no equivalent suite seeding a **mode** name
  collision. Would take: a `modes`-side sibling of `suites/shadowing` using
  the same seed-two-copies pattern.
- **`render_bridge_conflicts()` silence on a clean run** (both faces): no
  e2e assertion checks that a normal `prepare` run's stdout contains *no*
  conflict block. Cheap to add: assert `"name conflict" not in stdout` on
  any of the existing non-conflict fixtures.
- **Mode-side manifest reconciliation edge cases** (`test_modes_bridge.py`):
  stale-mode pruning and user-owned-agent-file preservation are proven for
  the skills/commands face (`suites/bridge`) but not for the modes/agents
  face -- `suites/bridge` seeds and removes a skill, never a mode, for the
  pruning case, and never drops a foreign file in the agent dir for the
  ownership case. Would take: mirroring both `suites/bridge` fixtures onto
  the agent dir.
- **Intra-run duplicate-name first-wins guard** (both faces): requires the
  real agent to report two skills/modes with the identical name in one
  response, which the real amplifier-agent's own shadowing resolution
  should never produce. Would take: extending the fake-server pattern to
  serve a deliberately duplicate-named `/v1/skills` or `/v1/modes` body.
- **`skill_command_description()` idempotency and empty-description
  marking** (`test_skills_bridge.py`): re-running the bridge over a
  description that is already `(Amplifier)`-prefixed, and a skill with an
  empty description, are both unseeded in any suite. Would take: seeding
  a probe skill with an empty description, and a second `prepare` run
  proof that the prefix does not stack.
- **`prereqs` self-heal branches beyond the healthy no-op**
  (`test_prereqs.py`, ~8 cases): `extract_semver`/`version_ge`'s malformed-
  input and fails-closed paths, and `ensure_agent`'s missing/stale/
  force-reinstall/bootstrap-disabled branches, are never hit because the
  DTU always starts with a current, floor-satisfying install (the
  healthy-no-op branch IS implicitly exercised by every plain
  `amplifier-opencode launch` call that skips `--no-bootstrap`). Would
  take: a dedicated DTU scenario that first downgrades or removes
  `amplifier-agent`/`opencode` before invoking `launch`.
- **`update`-path helpers entirely** (`test_prereqs.py`:
  `get_self_install_info`, `update_opencode`, `_run_bash_pipe`,
  `ensure_prerequisites`'s false branch): no e2e suite exercises the
  `update` subcommand at all today. Would take: a new `update` e2e suite.
- **`--version`'s missing-agent and below-minimum-floor renderings**
  (`test_version.py`, 2 of 3 cases): the DTU always has a current,
  floor-satisfying agent installed, so only the "present and sufficient"
  line shape is ever produced. Would take: a scenario with amplifier-agent
  either uninstalled or downgraded below `MIN_AGENT_VERSION`.
- **`onboarding.py` in its entirety** (`test_onboarding.py`, 8 cases,
  including secret-redaction): no e2e suite ever exercises the onboarding
  wizard, because every DTU scenario starts with a resolvable
  `ANTHROPIC_API_KEY` already present, so `needs_onboarding()` is always
  `False` and the wizard path never runs. See the risk call-out below.

#### Highest risk: `onboarding._auth_set()`'s secret redaction

**Confirmed as the highest-risk gap.** `test_auth_set_timeout_does_not_leak_secret`
and `test_auth_set_failure_redacts_secret_from_agent_output` were the only
tests anywhere in the deleted tier -- or in the surviving e2e tier -- that
guard against a provider credential reaching stdout in plaintext:
`_auth_set` calls `amplifier-agent auth set <provider> <key>` as a
subprocess with the key on the argv, and on a timeout or a non-zero exit
whose `stderr` echoes that argv back, the wizard must scrub the key before
printing anything to the user's terminal. With the unit tier gone, a
regression that removes or breaks that scrubbing would print a live API
key to stdout, and nothing in the e2e tier would catch it: onboarding is
never invoked (every DTU scenario starts with a resolvable provider), so
this isn't a case of a weaker substitute test standing guard -- it is zero
coverage of a plaintext-secret-leak class of defect. This is a materially
different kind of gap from the others above, which are mostly functional
or cosmetic contract narrowing; this one is a security regression with no
automated detection at any tier. Re-opening this cold should be the first
priority if `onboarding.py` is ever touched again, ahead of any of the
functional gaps above.
