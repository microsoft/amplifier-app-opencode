---
name: amplifier-opencode-bugfix
description: "Triage and fix an amplifier-app-opencode bug, from report to verified fix, using E2E-test-driven development. Covers intake, ruling out non-code causes (stale server, stale install, mismatched floor), root-cause pinning, the coverage-gap gate that classifies why e2e missed it, red/green regression TDD, and scoped verification. Stops before release."
disable-model-invocation: true
user-invocable: true
---

# Fix an amplifier-app-opencode Bug

## Bug

$ARGUMENTS

If that is empty, ask what is broken before doing anything else. Ask for the
exact command that failed and the exact output, not a paraphrase.

## The Method

A bug is a contract that was under-specified. The fix is not just working
code, it is an extended contract that makes this class of bug unable to
recur silently.

So the work is ordered: rule out the cheap non-code causes, pin the root
cause before touching a line, decide honestly why the e2e suite did not
catch this, and only then write the failing test and the fix.

Two rules override everything below. Fixing stays frozen until the root
cause is pinned. Tests are never edited to make a fix pass.

This repo has no unit test tier and no CI. `tests/e2e/` is the only test
tier and the only place a regression test can live. That makes Phase 3, the
coverage-gap gate, unusually load-bearing here: there is no fallback tier to
quietly catch what e2e misses.

This skill ends at a verified fix. It does not cover PRs, versioning, or
release. When the loop closes, recommend the `amplifier-opencode-release`
skill.

Work through the phases in order. Use the todo tool to track them.

---

## Phase 0: Intake

Capture the report before interpreting it. You need enough to attempt a
repro.

```
The exact command that failed, verbatim
The exact output or error, verbatim
Which surface: launch/prepare preflight, generated opencode config, the
  skills/modes bridge, doctor, update, onboarding?
Which version is it running? (amplifier-opencode --version reports
  __version__; doctor reports neither -- see Mechanics)
Reproducible every time, or intermittent?
```

Ask for whatever is missing. Do not proceed on a paraphrase.

Do not form a hypothesis yet. Do not open source files yet.

## Phase 1: Rule out the cheap causes

Most bugs are in our code, and that is where this usually ends up. This
phase is a sweep over the handful of causes that reading the source cannot
find, followed by a repro on a clean box.

The question to ask is: what in this setup, other than the source under
test, could differ from what I am assuming? Below are the causes specific
to this repo. Run the ones that plausibly apply to the reported surface,
skip the rest, and add whatever the actual report suggests.

```
Is a server already running from BEFORE the change, holding stale
  discovery state? amplifier-agent fixes available_skills/available_modes
  at server STARTUP, not per-request. If the report involves a skill, mode,
  or model that "doesn't show up", this is the first thing to check --
  see Mechanics for the exact kill command every seeding suite uses.

Is the install stale inside the DTU (or your local venv)? `refresh`
  reinstalls the CLI in place inside the warm DTU but wipes the lazily
  installed provider module, which can look like a regression that is
  actually a half-applied refresh.
  uv run python tests/e2e/cli.py refresh

Is the reported version floor mismatched? MIN_AGENT_VERSION in prereqs.py
  is a requirement statement (see AGENTS.md invariant 1), not a mirror of
  whatever amplifier-agent last released. If the report is "a flag/endpoint
  doesn't do what the docs say", check whether the installed amplifier-agent
  actually meets the floor this tool declares, not just whether one is
  installed:
  amplifier-digital-twin exec oc-e2e -- amplifier-agent --version
  grep -m1 'MIN_AGENT_VERSION' src/amplifier_app_opencode/prereqs.py

Is the DTU itself misprovisioned rather than the code under test?
  amplifier-digital-twin exec oc-e2e -- amplifier-opencode doctor

Is a generated file being hand-edited? A command/agent file not tracked
  in .amplifier-generated-commands.json / .amplifier-generated-agents.json
  is treated as user-owned and never touched -- a "my change keeps getting
  reverted" report is often this, not a reconciliation bug (AGENTS.md
  invariant 4).

For a quality report (the reply is worse, less thorough, wrong tool used):
  this is not this repo's tier. See Phase 3's routing note.
```

Do not linger on the checks above. If they do not settle it, reproduce on a
clean box: destroy and re-provision the DTU, which is also the box you will
probe in Phase 2.

```bash
uv run python tests/e2e/cli.py down
uv run python tests/e2e/cli.py up
amplifier-digital-twin exec oc-e2e -- amplifier-opencode doctor
amplifier-digital-twin exec oc-e2e -- bash -lc '<the exact reported command>'
```

If it does not reproduce, say so and find out what differs before going
further; an unreproducible bug cannot be verified fixed.

If one of the checks does explain it, the defect is not in the logic you
were about to read, but that does not always mean there is nothing to
change. A clearer error message or a guardrail against the stale-state
scenario is often the right fix, and it is still code. Decide that in
Phase 3.

Either way, record what you ruled out and how you reproduced it. Phase 2
should not re-litigate either.

## Phase 2: Pin the root cause

Fixing is frozen for this entire phase. No source edits, no speculative
patches, no "let me just try changing this."

State at most two named hypotheses, and they must imply DIFFERENT fixes. If
two hypotheses lead to the same patch, the distinction does not matter and
you are stalling.

```
(A) <hypothesis>   -> if true, the fix is <X>
(B) <hypothesis>   -> if true, the fix is <Y>

DECISION RULE: <the specific observation that selects A over B>
```

Start with the artifacts you already have: the server log
(`/tmp/amplifier-agent.log` inside the DTU), stdout envelopes, the
generated `opencode.json`, the ownership manifests, session event logs
(`~/.amplifier-agent/state/workspaces/opencode/sessions/http-<id>/context-intelligence/events.jsonl`).

Then run experiments. This phase is hands-on, not desk analysis. What earns
the answer is deliberately constructing the two states that tell A and B
apart:

```
Single-variable toggle    same command with and without one flag or env var
Two-box A/B               a DTU on published amplifier-agent (--published)
                          vs. one on local main
Probe the live box        exec into the running DTU and read the actual
                          state (generated file, manifest, log line)
Diff archaeology          git diff origin/main..HEAD -- <suspect files>
                          git diff HEAD --  (uncommitted work counts too)
```

Most of this happens in a DTU, because that is where the software runs as
installed rather than as imported.

```
One box, many probes    reuse the warm DTU from Phase 1 and exec probes
                        into it; push probe scripts with file-push, do not
                        write them at exec time (see Mechanics)

Two boxes, one variable launch a second DTU that differs in EXACTLY one
                        thing (e.g. --published) and run the identical
                        command in both. Destroy both when done.
```

Delegation works well for recon. Dispatch read-only explorer agents in
parallel with disjoint scopes and clean-slate context. Label static code
reading as provisional: it is a source of hypotheses, not conclusions. Give
each agent one fact to determine empirically, with explicit anti-scope (do
not modify, do not commit, do not stage, do not destroy any DTU).

Report the pinned cause and the evidence that pins it to the user before
moving on. "I think it is X" is not pinned. "X, because this observation
rules out Y" is pinned.

## Phase 3: Why did e2e miss it? (gate)

This is a gate, not a reflection. You cannot proceed to Phase 4 until this
question is answered and the answer names where the regression test goes.

Answering this is usually a read, not a run. Open the `cases.py` /
`test_<suite>.py` of the suite closest to the broken behavior and compare
what it asserts against the root cause pinned in Phase 2.

Run the suite only if reading leaves it genuinely ambiguous:

```bash
uv run python tests/e2e/cli.py run <suite> --skip-setup -rxX -k "<case>"
```

Classify the miss into exactly one of these:

```
1. NO COVERAGE
   The contract is testable by the harness and simply was never asserted.
   -> Add a case to the closest existing suite, or a new
      tests/e2e/suites/<area>/. The normal case. Proceed to Phase 4.

2. SHALLOW COVERAGE
   A test exists and passes, but asserts less than the contract. Exit 0
   was checked; the behavior underneath was not.
   -> Strengthen the existing case rather than adding a new one. Proceed to
      Phase 4, which confirms the strengthened form goes red.

3. PASSED VACUOUSLY
   A test exists, asserts the right thing, and still passed -- against
   STALE STATE. In this repo the canonical instance is a case that seeds a
   skill/mode file without first killing an already-running amplifier-agent:
   discovery is fixed at server startup, so the assertion ran against the
   OLD skill/mode set and happened to pass, or happened to fail for the
   wrong reason. Check whether the suite's conftest.py kills the server
   before seeding (see Mechanics for the exact pattern every existing
   seeding suite uses).
   -> Add the missing kill-before-seed step. Proceed to Phase 4.

4. SPEC NEVER STATED IT
   The code contract genuinely was undefined: docs/spec/*.md does not
   commit to the behavior the bug report assumed. The defect is in the
   spec, not (only) the code.
   -> The regression test AND the docs/spec/*.md update are both required
      output of this fix. Write the spec clarification in Phase 5 alongside
      the code fix, in the same change. Proceed to Phase 4.

5. GENUINELY UNTESTABLE FROM A LINUX CONTAINER
   The e2e harness runs inside a Linux DTU. Some behavior structurally
   cannot be exercised there: macOS/Windows-specific install methods in
   platform_utils.py (os_label(), is_darwin(), is_windows(), the
   Windows/macOS branches of opencode_install_method()) are the known
   instances -- see docs/ISSUES.md's "Known coverage gaps" section for the
   full accounting of what was already given up when the mocked unit tier
   was removed.
   -> Do NOT invent an e2e test that cannot actually run in this harness.
      Record the gap explicitly in docs/ISSUES.md (new entry, or append
      evidence to an existing one) and say so plainly in the handoff. This
      is an honest gap, not a failure to write a test.
```

Write the classification down explicitly. One line, naming the class and
the destination file.

**A quality report routes to `amplifier-agent`'s evaluation harness.** If
the actual complaint is that a model's reply got worse, was less thorough,
or picked the wrong tool, but every command still exits correctly and every
documented contract still holds, that is not an e2e miss at all -- e2e
proves contracts, not judgment. Say so, and route the report to
`amplifier-agent`'s evaluation harness. Do not build an e2e assertion for
it here; grading output quality is that harness's job, not this repo's.

## Phase 4: RED

Write the regression test. Run it. Confirm it fails for the right reason.

The test is a CONTRACT, not a repro script:

```
Repro script    reproduces the exact conditions of this one incident
Contract        asserts the behavior that was violated, so the whole CLASS
                of bug fails the suite, not just the instance you hit
```

Name the case after the contract it protects, not after the bug or its
issue number.

```
tests/e2e/suites/<area>/
  __init__.py
  cases.py            TUICase / Step data, if a TUI-driven suite
  test_<area>.py      pytestmark = pytest.mark.dtu, parametrized over cases
  conftest.py         only if the suite needs to seed fixture files
  fixtures/*.md.tmpl  seed skill/mode templates, if any
```

Nothing under `tests/e2e/framework/` should need to change. If it does,
raise it before proceeding.

Rules that are not negotiable:

```
Assert the PUBLIC contract only. Never assert on internals, log formats,
  or anything a legitimate refactor would break.
Prefer extending an existing suite over creating a new one.
If the suite seeds a skill or mode file, kill any running amplifier-agent
  first (see Mechanics). This is exactly the pitfall from classification 3.
```

Preflight before committing to a DTU run:

```bash
python3 -m py_compile tests/e2e/suites/<area>/*.py && echo "SYNTAX OK"
uv run pytest tests/e2e/suites/<area> --collect-only -q 2>&1 | tail -25
```

Then run RED, detached, to its own log:

```bash
rm -f /tmp/red_run.log && setsid bash -c \
  'uv run python tests/e2e/cli.py run <area> -rxX > /tmp/red_run.log 2>&1' \
  </dev/null >/dev/null 2>&1 &
echo "launched pid $!"
```

Poll it (see Mechanics). When it finishes, verify the failure REASON
matches the pinned root cause from Phase 2. A test that fails for an
unrelated reason is not capturing the bug, and a test that passes on the
first run is not capturing anything at all.

If the new test passes against the broken code, the test is wrong. Fix the
test, not the expectation, and re-run until it is red for the right reason.

Report the red result to the user before implementing.

## Phase 5: GREEN

Implement the fix. Fix the root cause pinned in Phase 2, not the symptom
the test happens to catch.

If Phase 3's answer was class 5 (genuinely untestable), there is no test
and there will not be one. Skip the iteration loop below and verify the fix
by re-running the Phase 1 repro on a clean box instead.

If Phase 3's answer was class 4, write the `docs/spec/*.md` clarification
in this same phase, alongside the code. The spec and the fix are one
change, not sequential ones.

```
Confirm the mechanism CAUSALLY before believing the fix. A test going
  green right after an edit is correlation. Know WHY it went green.
Do NOT refactor adjacent code you noticed along the way. Note it separately.
Do NOT commit or stage.
Check for drift every few iterations: git diff --stat
```

The named failure mode of this phase is test-weakening.

```
If a NEW test looks wrong, you wrote it wrong. Fix it, re-confirm red.
If a PRE-EXISTING test starts failing, that test is the contract. Revert
  the source and escalate. Do NOT relax the assertion to fit your fix.
If you believe a pre-existing test is genuinely wrong, that is the one
  case to come back to the user. Bring evidence.
```

Iterate narrow, against the warm DTU:

```bash
rm -f /tmp/green_run.log && setsid bash -c \
  'uv run python tests/e2e/cli.py run <area> --skip-setup -rxX \
   -k "<the one case>" > /tmp/green_run.log 2>&1' </dev/null >/dev/null 2>&1 &
echo "launched pid $!"
```

Keep the fast local gate green as you go:

```bash
make check
```

Delegation works for the implementation loop. Hand a builder agent the
pinned root cause and a precise scope, with clean context and explicit
anti-scope (do not commit, do not stage, do not edit tests).

## Phase 6: Verify

Never declare done on the `-k` slice that proved the point. Widen:

```
1. -k "<the regression case>"          the fix works
2. cli.py run <area> --skip-setup      the suite it lives in
3. cli.py run <area> <adjacent>        the neighbors it could have broken
4. make check                          lint + format stay clean
5. cli.py run                         all e2e suites, freshly provisioned:
                                       clean-box confirmation
```

If Phase 3's answer was class 5, there is no regression case; start the
ladder at rung 2 with the suite nearest the change.

Name up front which existing failures are expected, so a known-bad suite is
not mistaken for a regression you caused. There is no non-e2e tier to fall
back on for a baseline; the e2e run itself is the baseline.

If the bug was actually a quality report routed to the evaluation harness in
`amplifier-agent`, that harness's own re-run is the verification; it does
not happen here.

## Phase 7: Handoff

Confirm each of these, with evidence, not assertion:

```
The root cause is stated in one sentence, and the evidence that pins it
The Phase 3 classification is recorded, with the destination it implied
The regression test fails on the old code and passes on the new
Scoped suite green, plus adjacent suites, plus one full clean run
make check clean (ruff lint + format check)
Every hunk in the diff traces to the root cause. No unrelated refactors.
No pre-existing test was weakened or deleted
docs/spec/*.md updated if the fix clarified or changed a contract (Phase 3
  class 4 always implies this)
CHANGELOG.md updated under [Unreleased]
Every DTU created for this hunt is destroyed
git status --short shows no stray probe scripts, logs, or patch files
```

If the Phase 3 answer was 5, state plainly that this bug has no regression
coverage and why, and confirm the `docs/ISSUES.md` entry exists. That is an
honest gap. Do not let it read as covered.

If any item cannot be honestly satisfied, report it as a gap. Do not check
the box.

Then STOP.

```
Next step: releasing this work (sweep, version, changelog, PR) is covered
by the amplifier-opencode-release skill. Recommend it. Do not do it here.
```

---

## Mechanics

The parts that silently waste the most time.

**Always detach long runs.** A bash tool timeout kills the process group and
orphans the DTU container. `nohup ... &` is not enough.

```bash
setsid bash -c '<cmd> > /tmp/x.log 2>&1' </dev/null >/dev/null 2>&1 &
echo "launched pid $!"
```

**Poll, never block.** Intervals of 60 to 115 seconds. Never above 120.

```bash
sleep 90; echo "=== $(date +%H:%M:%S) ==="; tail -n 25 /tmp/x.log; \
  pgrep -f "e2e/cli.py run" >/dev/null && echo RUNNING || echo DONE
```

**Discovery is fixed at server startup.** This is the single most common way
a test here silently passes vacuously (Phase 3 class 3). Every existing
seeding suite (`bridge`, `config`, `modes`, `shadowing`, `skills`,
`traversal`) kills the server before seeding, using a bracket regex so
`pkill` cannot match its own argv:

```bash
pkill -f "amplifier-agent[ ]serve" || true
```

If you write a case that seeds a skill or mode file and skip this step, the
case will run against whatever was discovered at the LAST server boot, not
your seeded content, and can pass for the wrong reason.

**There is no unit test tier and no CI.** `tests/e2e/` is the only test
tier; `make check` and the e2e harness are the only gates. There is no
fallback CI run that will catch something the e2e suite missed on a tag
push or PR -- everything is decided locally, before the PR.

**Version reporting.** `amplifier-opencode --version` reads `__version__`
from `src/amplifier_app_opencode/__init__.py`. `uv tool install` / `update`
read `pyproject.toml`'s `version` via package metadata. `doctor` reports
neither. If a bug report is about a wrong-looking version, check which of
these three the user actually looked at (`docs/ISSUES.md` ISSUE-002 tracks
this exact gap).

**The DTU filesystem does not persist between `exec` calls.** Files written
in one exec are gone in the next. Push probe scripts with `file-push`, do
not write them at exec time.

**Killing processes generally.** `pkill -f "<pattern>"` can match the
invoking shell's own argv and kill itself. Use a bracket regex the literal
command line will not match, as shown above.

**Big output.** Pipe long-running commands through `tail -N` with a
sentinel echo after:

```bash
timeout 115 uv run python tests/e2e/cli.py run <area> --skip-setup \
  2>&1 | tail -20; echo "EXIT_CHAIN_DONE"
```

**DTU naming.** `oc-e2e` is this harness's DTU and Gitea mirror name. A
throwaway box for one experiment gets its own name and is destroyed when
the experiment ends. Never destroy a DTU you did not create.

**`refresh` vs a full `run`.** `refresh` re-pushes local snapshots and
reinstalls in place -- fast, but wipes the lazily installed provider module.
A plain `--skip-setup` run does NOT re-mirror at all; if source changed
since the DTU came up, `refresh` first or you are testing stale code.

**Sibling checkout and Docker.** The default local-mirror install needs
`amplifier-agent` checked out as a sibling of this repo and Docker running
for the Gitea container. Pass `--published` to skip both and install
published upstream versions instead.

**Environment.** `ANTHROPIC_API_KEY` must be in the host env for a real
model and for the AI-user judge. If preflight fails, point the user at the
Prerequisites section of `docs/E2E_TESTING.md`.
