---
name: amplifier-opencode-new-feature
description: "Develop a new amplifier-app-opencode feature from ideation to working end-to-end in a DTU, using E2E-test-driven development with the spec landing in the same commit as the contract change. Covers orientation, planning, spec-first review, red/green E2E TDD, and the loop-until-green discipline. Stops before release."
disable-model-invocation: true
user-invocable: true
---

# Develop a New amplifier-app-opencode Feature

## Feature

$ARGUMENTS

If that is empty, ask what feature to build before doing anything else.

## The Method

E2E-test-driven development. Tests come before implementation, they run against
the real installed `amplifier-opencode` binary inside a DTU container, and they
define the contract. The feature is done when the scoped tests go green and a
human has tried it by hand in a DTU.

This repo has no unit test tier and no CI. `tests/e2e/` is the only test tier,
and `make check` plus the e2e harness are the only gates. That makes the spec
and the e2e case the two durable artifacts a feature produces, and both are
part of the change, not follow-ups.

This skill ends at a working feature. It does not cover PRs, versioning, or
release. When the loop closes, recommend the `amplifier-opencode-release`
skill.

Work through the phases in order. Use the todo tool to track them.

---

## Phase 0: Orient

Read before touching code. Do not start from source.

```
AGENTS.md                       gates, invariants, what-done-looks-like
docs/SPEC.md                    index of contracts, with a reading order
docs/spec/<relevant>.md         the contract(s) this feature changes
docs/E2E_TESTING.md             the harness, in full
```

Skim `docs/ISSUES.md` for the area you are touching too. Several open items
there are recorded gaps in the exact surfaces this tool exposes (version
reporting, host-config pass-through, provider onboarding); building on top of
one without reading it first is how a fix silently reproduces a known issue.

Then answer these explicitly before moving on:

```
Which contract does this feature change, and which docs/spec/*.md owns it?
Which AGENTS.md cross-component invariant does it touch?
  (version floor is upstream's call, version declared in two places,
   e2e needs the amplifier-agent sibling checkout, generated files are
   reconciled through ownership manifests, the spawned server is never
   stopped, discovery is fixed at server startup)
Does it touch generated output (opencode config, command/agent files)?
  If yes, the ownership-manifest reconciliation needs to keep working.
Does it seed a skill or mode file for its own e2e case? If yes, Phase 3's
  test MUST restart the agent server after seeding (see Mechanics).
Which existing e2e suite is closest, and is this a new suite or an addition?
  (agent_integration, bridge, chat, cli, config, modes, shadowing, skills,
   traversal)
```

Delegation is useful here. A recon agent can map the relevant code paths while
you read the specs. Give it an absolute repo path, a narrow scope, and explicit
anti-scope (read-only, do not modify, do not commit, do not stage). Prefer a
clean-slate context and a self-contained instruction over inherited context.

## Phase 1: Plan

Present options before choosing. Enumerate a few real approaches with actual
tradeoffs and let the user pick. Do not propose a single answer.

Write the plan to a scratch file OUTSIDE the repo. Plans are transient working
artifacts, not repo content. Default to `.ai_working/` in
the parent workspace directory, or wherever the user already keeps working
notes. Never commit a plan file.

The plan states:

```
The contract change, in one sentence
Which docs/spec/*.md files get updated, and how
The e2e case(s) to write, by name and suite, with what each asserts
The e2e subset to run while iterating, and the widening ladder
"Iterate until all relevant e2e tests pass"
```

## Phase 2: Spec first

If the change alters an observable contract (a CLI flag, the generated
opencode config, the skills/modes bridge, install/update behavior, anything
`docs/spec/*.md` documents), write the `docs/spec/*.md` update NOW, before the
RED test and before implementation. It lands in the same commit as the
contract change, not after. `AGENTS.md` states this as a "done" criterion, not
a nicety.

If the change adds a new surface (a new flag, a new generated file, a new
bridge behavior), do a **Non-goals review** before writing a line of
implementation: read the Non-goals section of the closest `docs/spec/*.md`.
Every spec file carries one, and per `docs/SPEC.md`, "Non-goals are contracts.
An absent surface stays absent because callers depend on it not existing."
If the new surface contradicts a Non-goals statement someone already depends
on, that is not a green light with a spec update attached, it is a design
question. Stop and put it to the user before proceeding.

## Phase 3: RED

Write the test. Run it. Confirm it fails for the right reason.

```
tests/e2e/suites/<feature>/
  __init__.py
  cases.py            TUICase / Step data, if a TUI-driven suite
  test_<feature>.py   pytestmark = pytest.mark.dtu, parametrized over cases
  conftest.py         only if the suite needs to seed fixture files
  fixtures/*.md.tmpl  seed skill/mode templates, if any
```

A non-TUI suite is also a supported shape (see `shadowing`, `traversal`,
`config`): skip `cases.py`, seed fixture files in `conftest.py`, drive
`amplifier-opencode prepare` via `driver.run_command`, and assert on stdout
and the resulting filesystem state instead of a captured screen.

Nothing under `tests/e2e/framework/` should need to change. If it does, that
is a signal worth raising before proceeding.

Rules that are not negotiable:

```
Assert the PUBLIC contract only. Never assert on internals, log formats, or
  anything that would break on a legitimate refactor.
The test is the contract. If a test looks wrong during implementation, STOP
  and escalate. Do not edit a test to make it pass.
Not-yet-built behavior gets @pytest.mark.xfail(reason=..., strict=True) so an
  unexpected pass is a hard failure the moment the feature lands.
If the case seeds a skill or mode file, kill any running amplifier-agent
  first (see Mechanics). Discovery is fixed at server startup; a case that
  seeds against an already-running server will silently pass against stale
  state instead of exercising the new content at all.
```

Preflight before spending minutes on a DTU run:

```bash
python3 -m py_compile tests/e2e/suites/<feature>/*.py && echo "SYNTAX OK"
uv run pytest tests/e2e/suites/<feature> --collect-only -q 2>&1 | tail -25
```

Then run RED, detached, to its own log:

```bash
rm -f /tmp/red_run.log && setsid bash -c \
  'uv run python tests/e2e/cli.py run <feature> -rxX > /tmp/red_run.log 2>&1' \
  </dev/null >/dev/null 2>&1 &
echo "launched pid $!"
```

Poll it (see Mechanics). When it finishes, verify the failure reason, not just
the failure count. A test that fails because of a typo in the case data is not
a red test.

Report the red result to the user before implementing.

## Phase 4: GREEN

Implement, then loop until the scoped tests pass.

Scope discipline is the point of this phase. Start with the narrowest possible
subset and widen only after the narrow scope is green:

```
1. -k "<the one failing case>"            fastest signal, warm DTU
2. cli.py run <feature> --skip-setup      the whole red set for this feature
3. cli.py run <feature>                   fresh-provisioned, single suite
4. cli.py run <feature> <adjacent>        regression check on neighbors
5. cli.py run                            everything, freshly provisioned:
                                          clean-box confirmation
```

Do not run all suites while iterating. Reuse the warm DTU with `--skip-setup`.

```bash
rm -f /tmp/green_run.log && setsid bash -c \
  'uv run python tests/e2e/cli.py run <feature> --skip-setup -rxX \
   -k "<subset>" > /tmp/green_run.log 2>&1' </dev/null >/dev/null 2>&1 &
echo "launched pid $!"
```

Re-launch the SAME subset to the SAME log name across iterations so runs are
comparable. Only change the log name when the scope changes.

When you widen to adjacent suites, name up front which existing failures are
expected and acceptable, so a known-bad suite is not mistaken for a
regression.

If a test looks wrong, escalate. Do not edit it.

Also keep the fast local gate green as you go:

```bash
make check
```

Delegation works well for the implementation loop. Hand a builder agent the
plan file path and a precise scope, with a clean context and explicit
anti-scope (do not commit, do not stage, do not edit tests). Keep the
test-running and result-interpretation in the driving session so the loop
stays coherent.

## Phase 5: Verify wide

The narrow slice that proved the point is not the finish line. Climb the
widening ladder from Phase 4 all the way to rung 5, a full clean run:

```bash
rm -f /tmp/full_run.log && setsid bash -c \
  'uv run python tests/e2e/cli.py run > /tmp/full_run.log 2>&1' \
  </dev/null >/dev/null 2>&1 &
echo "launched pid $!"
```

This provisions a fresh DTU and runs every suite (`agent_integration`,
`bridge`, `chat`, `cli`, `config`, `modes`, `shadowing`, `skills`,
`traversal`). Roughly ten minutes for the full ~54-test run. Poll rather than
block (see Mechanics).

## Phase 6: Human loop

Automated green is not done. Give the user a way to try the feature by hand.

```bash
uv run python tests/e2e/cli.py up
amplifier-digital-twin exec oc-e2e -- amplifier-opencode doctor
```

Then hand them a concrete command to run, not a description of one:

```bash
amplifier-digital-twin exec oc-e2e -- bash -lc '<the actual command>'
```

For anything on the TUI itself, they can also capture the live pane:

```bash
amplifier-digital-twin exec oc-e2e -- tmux capture-pane -t oc -p
```

Wait for their verdict before calling the feature done.

## Phase 7: Handoff

Confirm each of these, with evidence, not assertion:

```
Scoped e2e suite green, plus adjacent suites, plus one full run
  (uv run python tests/e2e/cli.py run, no scoping, fresh DTU)
make check clean (ruff lint + format check)
docs/spec/*.md updated in this same change, if a contract changed
Non-goals reviewed, if a surface was added
CHANGELOG.md updated under [Unreleased]
If MIN_AGENT_VERSION was touched: it traces to an upstream amplifier-agent
  release-process decision, not to "a newer version exists". State the
  reason explicitly, or state that it was not touched.
No hardcoded paths. Would this work for someone else who checks out the repo?
No secrets in code, tests, fixtures, logs, or committed config
```

If any item cannot be honestly satisfied, report it as a gap. Do not check the
box.

Then STOP.

```
Next step: releasing this work (sweep, version, changelog, PR) is covered by
the amplifier-opencode-release skill. Recommend it. Do not do it here.
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

**Discovery is fixed at server startup.** amplifier-agent reads
`available_skills`/`available_modes` once, at boot. A case that seeds a skill
or mode file against an already-running server passes vacuously against stale
state. Kill it before seeding, using the exact bracket-regex pattern the
existing suites use so `pkill` cannot match its own argv:

```bash
pkill -f "amplifier-agent[ ]serve" || true
```

Every seeding suite (`bridge`, `config`, `modes`, `shadowing`, `skills`,
`traversal`) does this in its `conftest.py`. Copy the pattern; do not invent a
new one.

**There is no unit test tier.** The seven `tests/test_*.py` modules were
deleted once e2e coverage reached parity; `tests/` now contains only `e2e/`.
Do not suggest adding one, and do not write a "unit test" for internal logic
that has no observable surface -- `docs/ISSUES.md`'s "Known coverage gaps"
section records what was intentionally given up and why.

**Evaluation of model output quality lives in `amplifier-agent`.** A
regression in an LLM's judgment or reply quality (worse answers, wrong tool
chosen, gave up early) is measured by that repo's evaluation harness. This
tool's e2e tier proves contracts (a flag works, a file is written, a command
exits 0), not model judgment. If a report is really about reply quality, say
so and route it there; do not invent an e2e assertion for it.

**Killing processes generally.** `pkill -f "<pattern>"` can match the invoking
shell's own argv and kill itself. Use a bracket regex the literal command line
will not match, as shown above.

**Big output.** Pipe every long-running command through `tail -N` with a
sentinel echo after, so a truncated tail is distinguishable from a killed
process:

```bash
timeout 115 uv run python tests/e2e/cli.py run <feature> --skip-setup \
  2>&1 | tail -20; echo "EXIT_CHAIN_DONE"
```

**DTU naming.** `oc-e2e` is this harness's DTU; the local-mirror Gitea
container is also named `oc-e2e` (port `10130`). Never destroy a DTU or Gitea
container you did not create.

**Local mirror needs Docker and a sibling checkout.** By default the harness
installs BOTH `amplifier-app-opencode` and `amplifier-agent` from local
working trees via an in-DTU Gitea mirror, so it validates uncommitted
cross-repo changes. This needs Docker running and `amplifier-agent` checked
out as a sibling directory (`<parent>/amplifier-agent` next to
`<parent>/amplifier-app-opencode`). If the sibling is missing, provisioning
fails loud rather than silently falling back. Pass `--published` to install
published upstream versions instead (no Docker or sibling needed).

**`refresh` vs a full `run`.** `refresh` re-pushes local snapshots and
reinstalls in place inside the warm DTU -- fast, but only for code-only
iteration. `--skip-setup` reuses the warm DTU completely as-is and does NOT
re-mirror; if you edited source since the DTU came up, `refresh` first or the
old code is still what is installed.

**Environment.** `ANTHROPIC_API_KEY` must be in the host env; it is required
for a real model and for the AI-user judge (host-side). The harness needs
`uv`, `amplifier-digital-twin` (Incus-backed), and by default Docker. If
preflight fails, point the user at the Prerequisites section of
`docs/E2E_TESTING.md`.
