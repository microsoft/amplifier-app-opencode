---
name: amplifier-opencode-release
description: "Release amplifier-app-opencode: bound the commit range, sweep the diff for anything that should not ship, bump both version declarations in agreement, write the changelog, confirm the agent-version floor decision, run the local gate, and open the release PR. Merging to main IS the release; there is no tag or publish step."
disable-model-invocation: true
user-invocable: true
---

# Release amplifier-app-opencode

## Release

$ARGUMENTS

If that is empty, do not ask what to release yet. Run Phase 0 first and
propose a scope from the evidence, then confirm it with the user.

## The Method

A release is a public statement. Everything in the diff becomes permanent,
readable by anyone, and attributed to the project. So the work is ordered:
establish what actually changed, remove what should never have been
committed, then version it, describe it, confirm the upstream floor
decision, verify it, and propose it.

Users install `amplifier-opencode` straight from git (`install.sh`,
`uv tool install --from git+...`, or `amplifier-opencode update`), so
**merging the release PR to main is the release**. There is no second,
later step that makes it real, and no gate after the merge to catch what
was missed before it.

Two rules override everything below.

**This skill never decides the agent-version floor.** `MIN_AGENT_VERSION` in
`src/amplifier_app_opencode/prereqs.py` is a requirement statement owned by
`amplifier-agent`'s own release process. This skill confirms whether that
process delivered a floor change and folds it in if so; it never bumps the
floor on its own initiative just because a newer amplifier-agent exists.

**The release completes when the PR merges.** There is no later step.

Work through the phases in order. Use the todo tool to track them.

---

## Phase 0: Scope

You cannot sweep or version a release you have not bounded. Establish the
range first.

```bash
git fetch origin
git log --oneline origin/main -5
git merge-base origin/main HEAD
```

This repo has no release tags, so the range is whatever landed on `main`
since the previous release commit. If a prior
release PR is identifiable in history (its `chore: cut ... X.Y.Z` commit),
diff from there:

```bash
git log --oneline --grep='^chore: cut amplifier-opencode' -5
git diff <previous-release-commit>..origin/main --stat
```

If nothing since the last release-adjacent commit is otherwise findable,
diff from the last version bump in `pyproject.toml`:

```bash
git log -p -- pyproject.toml | grep -m1 -A2 '^-version'
```

State the scope back to the user before proceeding: which commits, what
changed at a glance, and the version you propose moving to. Get agreement.
A release nobody agreed the shape of is not ready to have its version set.

---

## Phase 1: Sweep

This is the phase that justifies the skill existing. Everything in the
range becomes public. Read the actual diff, not the file list.

```bash
git diff <range-start>..origin/main
```

For a large range, walk it file by file rather than dumping it all at once.

### Files that should not be in the repo

`AGENTS.md` is explicit that design docs are transient working artifacts,
not repo content. Hunt for:

```
plan / PLAN / *-plan.md / implementation-plan.md / phase-*.md
.ai_working/  scratch/  tmp/  WIP*
*.log  *.tmp  *.bak  *.orig  *.rej  .DS_Store
```

If a plan file exists and the user still wants it, it moves outside the
repo, into `.ai_working/` or wherever they keep working notes. It does not
ship.

`docs/ISSUES.md` is durable and must NOT be swept -- it is intentional,
checked-in tracked-gap content, not scratch.

### Comments and code that leak the process

The tell is a comment that only makes sense to someone who watched the work
happen. Grep the diff, then read the hits in context:

```bash
git diff <range-start>..origin/main \
  | grep -nE '^\+' \
  | grep -inE 'TODO|FIXME|XXX|HACK|phase [0-9]|per the plan|as discussed|step [0-9]+ of|for now|temporar|placeholder|remove (this|before)|/home/|/Users/|localhost:[0-9]|ngrok|session[_-]?id'
```

Remove or rewrite:

- References to plan phases or the sequence work was done in
- "For now", "temporary", "will fix later" without an issue behind it
- Commented-out code left as a fallback
- Absolute host paths, personal directories, machine names
- Anything resembling a key, token, or credential. If one is found, stop.
  It needs rotation, not deletion.

### Two invariant checks specific to this repo

**No new stdout writes that would break a scripted caller.**
`onboarding.py`'s secret-redaction path is the highest-risk regression
surface in the whole codebase, because a credential that reaches stdout in
plaintext is exposed the moment it ships and cannot be un-shipped (see
`docs/ISSUES.md`'s risk call-out): any diff touching `_auth_set` or the
onboarding wizard needs a specific check that a provider credential can
never reach stdout in plaintext, on a timeout or a non-zero exit.

```bash
git diff <range-start>..origin/main -- src/amplifier_app_opencode/onboarding.py
```

**Generated-file ownership manifests.** If the diff touches the skills or
modes bridge (`.amplifier-generated-commands.json`,
`.amplifier-generated-agents.json` handling), confirm the reconciliation
logic still never overwrites an unrecorded (user-owned) file -- this is
AGENTS.md invariant 4, and a regression here silently deletes or clobbers a
user's own config.

### Report before you change

Show the user what you found and what you propose to do about each item.
Do not silently delete things from someone else's commits.

---

## Phase 2: Versions

Check whether the bump already happened. The version in the manifest is the
target of the next release, so it may already be ahead of the last shipped
version.

```bash
grep -m1 '^version' pyproject.toml
grep -m1 '__version__' src/amplifier_app_opencode/__init__.py
```

**Both must agree, and nothing enforces that on its own.** `AGENTS.md`
invariant 2 states this plainly: these are two independent hand-edited
strings. `--version` reports `__init__.py`'s `__version__`; the installer
and `update` report `pyproject.toml`'s version via package metadata; `doctor`
reports neither. A past release (0.2.0) shipped these out of sync --
`__version__` was left at `0.1.3` while `pyproject.toml` moved to `0.2.0` --
and under-reported the installed version until 0.3.0 fixed it (see
`CHANGELOG.md`). `docs/ISSUES.md` ISSUE-002 tracks that this failure mode is
still reachable; there is no guard against it recurring. Treat both edits as
one atomic step, not two, and diff both files in the same review pass
before moving on:

```bash
git diff -- pyproject.toml src/amplifier_app_opencode/__init__.py
```

If a bump is needed, pick it by SemVer against the actual change set:

```
breaking CLI flag/behavior change, removed surface   -> major
new capability, new flag, new bridge behavior         -> minor
fix only, no new surface                              -> patch
```

Edit BOTH files in the same commit:

```
pyproject.toml                              [project] version
src/amplifier_app_opencode/__init__.py      __version__
```

---

## Phase 3: Changelog

`CHANGELOG.md` is Keep a Changelog with SemVer. The existing entries are
deliberately prose-heavy: they explain the mechanism and the reason, not
just the surface. Match that register -- a one-line bullet in a file full of
paragraphs reads as an afterthought.

Move whatever sits under `## [Unreleased]` into a new version heading, then
fill the gaps from the diff. Leave `## [Unreleased]` in place and empty.

```
## [Unreleased]

## [X.Y.Z] — YYYY-MM-DD

### Added
### Changed
### Fixed
### Security
### Notes
```

Write for a user of the tool, not a reader of the diff. Each entry should
answer: what can I now do, or what stopped being broken, and what do I have
to do to get it. If a change alters a default, moves the agent floor, or
requires action, say so plainly -- every existing floor bump in this file
names the specific capability that forced it (`GET /v1/skills`/`/v1/modes`,
`auth set --stdin`, namespaced reseller model ids, `--host-config` honoring
`provider.config`). Match that pattern for a new floor bump.

Do not describe internal refactors no consumer can observe. Confirm today's
date rather than assuming it.

---

## Phase 4: Floor check

Confirm, explicitly, whether `MIN_AGENT_VERSION` needs to move for this
release. State the rule plainly in the PR either way:

**This decision is made upstream, not here.** Whether a given amplifier-agent
release moves this repo's floor is owned by amplifier-agent's own upstream
release process, which executes the bump and opens the downstream PR here
when it does. This repo's release does not re-decide it and does not bump
it opportunistically because a newer amplifier-agent happens to exist --
`AGENTS.md` calls this out by name as a documented pitfall.

```bash
grep -m1 -A3 'MIN_AGENT_VERSION = ' src/amplifier_app_opencode/prereqs.py
```

If this release's scope (Phase 0) already includes a downstream PR from that
upstream process (i.e. `MIN_AGENT_VERSION`, `AGENT_PINNED_REF`, or
`AGENT_HARD_FLOOR` changed as part of the commits being released), confirm
the comment block above `MIN_AGENT_VERSION` names the specific capability
that forced it, in the same style as the existing entries, and fold that
into this release's changelog entry (Phase 3).

If it did not change, say so in the PR: "the agent floor is unchanged;
nothing in this release depends on new amplifier-agent behavior."

---

## Phase 5: Gate

`make check` plus a full clean e2e run are the ONLY gates. There is no CI
here to catch anything after this. Both must be green before a PR opens.

```bash
make check
```

Then the full suite, freshly provisioned, detached:

```bash
rm -f /tmp/release_e2e.log && setsid bash -c \
  'uv run python tests/e2e/cli.py run > /tmp/release_e2e.log 2>&1' \
  </dev/null >/dev/null 2>&1 &
echo "launched pid $!"
```

Poll rather than block (see Mechanics). This provisions a fresh DTU and runs
every suite: `agent_integration`, `bridge`, `chat`, `cli`, `config`,
`modes`, `shadowing`, `skills`, `traversal`. Roughly ten minutes for the
full ~54-test run.

If something fails, fix it or stop and report it. Do not open a release PR
on a red tree and plan to fix it in the PR -- there is no later gate that
will catch it, since merging IS the release.

---

## Phase 6: PR

Branch, commit, push, open.

```bash
git checkout -b chore/release-X.Y.Z origin/main
```

Conventional commit, mostly without a scope since this is a single package
(`AGENTS.md`'s convention). The version bump itself is usually folded into
the same descriptive commit as the change it accompanies, not a bare
version-only commit -- that is the actual pattern in this repo's history:

```
chore: raise amplifier-agent floor to 0.12.0, document raw LLM payload capture (0.4.0)
feat: GitHub Copilot passthrough + 0.3.0, agent floor to 0.11.0
feat: bridge amplifier-agent skills and modes into opencode (0.2.0)
```

If the sweep in Phase 1 removed things unrelated to the version bump, that
is a separate concern and reads better as its own commit. Stage explicitly;
never stage with `git add -A`.

Per `AGENTS.md`'s commit convention, a release-worthy PR title carries the
resulting version in parentheses at the end, describing what changed, e.g.:

```
feat: <what changed, in plain language> (X.Y.Z)
```

The PR body states facts and rationale only, never the steps of this
session. Cover:

- The user-visible change, in the same voice as the changelog entry
- Whether the agent-version floor moved, and the Phase 4 conclusion either
  way (moved because of upstream decision `<ref>`, or unchanged and why)
- That `pyproject.toml` and `__init__.py` versions agree
- That `make check` and a full clean e2e run are both green
- That **merging this PR is the release**: there is no tag push and no
  further publish step; the next `install.sh` run or `amplifier-opencode
  update` picks it up directly from `main`

Open it with `gh pr create`. Do not merge it yourself unless the user says
to.

---

## Report

Tell the user, plainly:

- What shipped: the version, and a one-line summary of the user-visible
  change
- The floor decision from Phase 4 and its reason, explicitly, either way
- That `make check` and the full e2e suite were both green before the PR
  opened
- The PR URL
- That once merged, users get the release on their next `install.sh` run or
  `amplifier-opencode update`

---

## Mechanics

The parts that silently waste the most time.

**The PR merge is the entire release mechanism.**

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

**Never stage with `git add -A`.** Stage the paths you intend. A version
bump plus a sweep plus an unrelated experiment in the working tree is easy
to conflate otherwise.

**Two version files, one commit.** `pyproject.toml`'s `version` and
`src/amplifier_app_opencode/__init__.py`'s `__version__` are independent
strings with no automated check tying them together. Diff both in the same
review pass every time; the historical failure mode (0.2.0 shipping out of
sync) is exactly this step skipped.

**The floor is upstream's call, always.** Do not bump `MIN_AGENT_VERSION`,
`AGENT_PINNED_REF`, or `AGENT_HARD_FLOOR` in this repo's release process
because a newer `amplifier-agent` release exists. That decision and its
execution belong to `amplifier-agent`'s own upstream release process; this
skill only confirms and folds in what that process already delivered.

**Big output.** Pipe every long-running command through `tail -N` with a
sentinel echo after, and walk a large diff file by file rather than dumping
it all:

```bash
timeout 115 uv run python tests/e2e/cli.py run 2>&1 | tail -30; echo "EXIT_CHAIN_DONE"
```

**Sibling checkout and Docker for the full e2e gate.** The default local
mirror install needs `amplifier-agent` checked out as a sibling of this repo
and Docker running for the Gitea container. `ANTHROPIC_API_KEY` must be in
the host env.

**DTU naming.** `oc-e2e` is this harness's DTU and Gitea mirror name. Never
destroy a DTU or Gitea container you did not create.
