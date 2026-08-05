"""Fixtures that SEED an adversarial, path-traversing skill NAME into the DTU.

This suite is the adversarial counterpart to ``suites/skills``. Where that suite proves
well-behaved skills get bridged correctly, this one proves a HOSTILE skill name cannot
escape the directory the launcher is supposed to confine its writes to.

Why a skill (and not a mode) carries the payload
------------------------------------------------
Only the skills path can express a real POSIX traversal end-to-end:

* A skill's ``name`` is read verbatim from SKILL.md YAML frontmatter. The upstream
  tool-skills discovery checks it against ``^[a-z0-9]+(-[a-z0-9]+)*$`` but only
  ``logger.warning``s on a mismatch -- the skill is still registered under the bad
  name, so an arbitrary string reaches ``GET /v1/skills``.
* A mode's listed ``name`` is the ``.md`` FILE STEM, which on Linux cannot contain
  ``/``. The modes bridge is therefore not reachable for true traversal on POSIX
  (``..`` and backslashes still get through, which matters on Windows), so the
  reachable, highest-severity case is the skills bridge.

The payload
-----------
``name`` is ``../../../../tmp/e2e-traversal-pwned``. The launcher computes
``command_dir / f"{name}.md"`` with ``command_dir`` = ``{PROJECT_DIR}/.opencode/command``,
so four ``..`` hops land at ``/`` and the write escapes to ``/tmp/e2e-traversal-pwned.md``
-- a path with no relationship to the project. That makes the failure unambiguous: the
file either exists (vulnerable) or it does not (contained).

The skill is marked ``disable-model-invocation: true`` because ``GET /v1/skills`` only
returns user-invoked skills, and only those are bridged into opencode commands.

Seeding runs BEFORE the TUI/server launches (see ``traversal_session``), because
amplifier-agent fixes skill discovery at server STARTUP.
"""

from __future__ import annotations

import shlex
import subprocess
import tempfile
import time
from collections.abc import Generator
from pathlib import Path

import pytest

# PROJECT_DIR / TMUX_SESSION are the in-DTU project dir and tmux session the TUI launches
# under (``/root/oc-e2e`` / ``oc-e2e``). Defined in the e2e root conftest, which puts its
# own directory on sys.path, so they import cleanly here.
from conftest import PROJECT_DIR, TMUX_SESSION
from framework import dtu
from framework.driver import TmuxTuiDriver

# The DTU installs the stack under the root user; ``$HOME`` is ``/root`` there.
DTU_HOME = "/root"

_TEMPLATE_PATH = Path(__file__).parent / "fixtures" / "hostile_skill.md.tmpl"

# The traversal payload used as the skill's frontmatter ``name``.
HOSTILE_SKILL_NAME = "../../../../tmp/e2e-traversal-pwned"

# Where the hostile SKILL.md lives. The DIRECTORY name is deliberately benign and valid --
# the payload rides in the frontmatter, which is the actual unvalidated field. (Discovery
# warns about the name/dir mismatch and continues, which is precisely the gap under test.)
HOSTILE_SKILL_PATH = f"{DTU_HOME}/.amplifier/skills/e2e-traversal-probe/SKILL.md"

# Absolute path the traversal resolves to. Kept as a literal (not computed) so the test
# asserts against an independently-derived expectation rather than re-running the buggy
# arithmetic it is meant to catch.
ESCAPED_WRITE_PATH = "/tmp/e2e-traversal-pwned.md"

# The launcher's intended blast radius, and its ownership manifest (written at
# ``command_dir.parent``). A traversal name recorded here is the delayed-DELETE primitive:
# next run, the prune step unlinks ``command_dir / <recorded name>``.
COMMAND_DIR = f"{PROJECT_DIR}/.opencode/command"
COMMANDS_MANIFEST = f"{PROJECT_DIR}/.opencode/.amplifier-generated-commands.json"


def _seed_hostile_skill(dtu_id: str) -> None:
    """Render and push the hostile ``SKILL.md`` into the DTU's user skills dir."""
    content = _TEMPLATE_PATH.read_text(encoding="utf-8").replace("{NAME}", HOSTILE_SKILL_NAME)
    parent = HOSTILE_SKILL_PATH.rsplit("/", 1)[0]
    dtu.exec_json(dtu_id, ["bash", "-lc", f"mkdir -p {shlex.quote(parent)}"])
    with tempfile.NamedTemporaryFile(
        "w", suffix=".md", prefix="hostile-skill-", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(content)
        local_path = handle.name
    dtu.file_push(dtu_id, local_path, HOSTILE_SKILL_PATH)


def _stop_agent_server(driver: TmuxTuiDriver, *, timeout: float = 30.0) -> None:
    """Kill any running amplifier-agent in the DTU and wait for its port to free.

    Necessary because ``_run_launch`` reuses a healthy server and skill discovery is
    fixed at STARTUP -- a surviving server would never see the freshly-seeded hostile
    skill. Waiting for the port (rather than just sending the signal) closes the race
    where the launcher's ``server_is_running`` probe still succeeds against a process
    that is on its way down.

    The ``[ ]`` bracket regex prevents ``pkill`` from matching its own command line.
    """
    driver.run_command(["bash", "-lc", 'pkill -f "amplifier-agent[ ]serve" || true'])
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        proc = driver.run_command(["bash", "-lc", 'pgrep -f "amplifier-agent[ ]serve" || true'])
        if not proc.stdout.strip():
            return
        time.sleep(1.0)
    raise RuntimeError(
        f"amplifier-agent still running after {timeout}s; refusing to continue because a "
        "stale server would not have discovered the seeded hostile skill (vacuous pass)"
    )


@pytest.fixture(scope="module")
def traversal_session(dtu_id: str) -> Generator[TmuxTuiDriver, None, None]:
    """Seed the hostile skill, then launch the TUI so the launcher bridges it.

    Module-scoped: the launch is the expensive part and every test in this suite asserts
    on the SAME post-launch filesystem state, so one launch serves them all.

    Three preconditions are established before spawning, in order:

    1. ``/tmp/e2e-traversal-pwned.md`` is removed, so its later existence can only be
       explained by THIS run's bridge write (no stale-artifact false positive).
    2. Any already-running amplifier-agent is killed and we wait for port 9099 to free.
       ``_run_launch`` REUSES a healthy server (``cli.py:903``) and skill discovery is
       fixed at server STARTUP, so a server left behind by a previous run or another
       suite would never see the freshly-seeded skill and this suite would pass
       VACUOUSLY. The bracket regex keeps ``pkill`` from matching its own argv.
    3. The hostile SKILL.md is seeded, so startup discovery picks it up.

    Killing the server is what lets this suite reuse the WARM DTU instead of forcing a
    full ``fresh_dtu`` reprovision: unlike the skills suite, we need no launch-time env
    overrides, only a server that starts after the seed. ``test_traversal.py`` asserts
    the bridge actually ran, so a vacuous pass surfaces as a failure rather than silence.
    """
    # Rebind through an f-string so the static type is a plain str: the dynamic
    # ``from conftest import`` confuses the type checker into treating the name as the
    # conftest module (same workaround the skills suite uses).
    tmux_session = f"{TMUX_SESSION}"
    exec_prefix = ["amplifier-digital-twin", "exec", dtu_id, "--"]
    driver = TmuxTuiDriver(tmux_session, exec_prefix=exec_prefix)

    driver.run_command(["bash", "-lc", f"rm -f {shlex.quote(ESCAPED_WRITE_PATH)}"])
    _stop_agent_server(driver)
    _seed_hostile_skill(dtu_id)

    driver.run_command(["bash", "-lc", f"mkdir -p {PROJECT_DIR}"])
    driver.spawn(f"amplifier-opencode launch --project-dir {PROJECT_DIR}")
    try:
        # The bridge runs during launch, before the main screen is painted, so this gate
        # also guarantees the write (or its absence) has already happened.
        driver.wait_for_text("tab agents", timeout=120)
        yield driver
    finally:
        driver.close()


# ---------------------------------------------------------------------------
# Modes-face hostile-name fixture -- the mode-face counterpart to the skills-face
# fixture above. Covers ISSUE-001 in docs/ISSUES.md and the "Known deviation"
# subsection of docs/spec/skills-and-modes-bridge.md: fetch_modes accepts any
# non-empty string as a name, unlike fetch_skills, which routes through the shared
# is_safe_bridge_name validator.
# ---------------------------------------------------------------------------

_MODE_TEMPLATE_PATH = Path(__file__).parent / "fixtures" / "hostile_mode.md.tmpl"

# The unsafe-but-DISCOVERABLE mode name used to probe the modes face. Confirmed
# empirically inside the DTU (seed several unsafe-but-POSIX-legal candidates as
# ``.md`` file stems, restart the agent server, then query ``GET /v1/modes``):
# a name containing a space, a name containing ``$``, and the literal name ``..``
# ALL survive discovery. ``..`` is the clearest choice of the three because it is
# one of only two names ``is_safe_bridge_name`` rejects BY LITERAL VALUE rather
# than by character class, and it is the exact case named in both
# ``fetch_modes``'s own docstring and ISSUE-001.
#
# HONEST FINDING: unlike the skills face (whose payload is a full relative path
# that resolves, via several ``..`` hops, to a real out-of-tree write), a mode's
# name is a ``.md`` file STEM, which cannot itself contain ``/`` on POSIX.
# Prefixing with ``MODE_AGENT_PREFIX`` ("amplifier-") turns ``..`` into the
# single, perfectly ordinary filename component ``amplifier-..md`` -- there is no
# ``/`` for it to introduce, so no directory escape is constructible from this
# name today. The defect this test proves is therefore a VALIDATION-CONTRACT
# violation (a name the skills face would refuse is silently accepted here, and
# reaches both the write step and the ownership manifest), not a working
# traversal exploit. Severity is lower than the skills-face case for exactly
# this reason -- no fabricated exploit is claimed.
HOSTILE_MODE_NAME = ".."

# Seed path: a project-scoped mode whose file STEM is "..". Path("...md").stem is
# ".." -- the first two dots form the stem, the third is where pathlib splits off
# the ".md" suffix.
HOSTILE_MODE_SEED_PATH = f"{PROJECT_DIR}/.amplifier/modes/...md"

# Where the name would land if the modes face applied no validation at all:
# MODE_AGENT_PREFIX ("amplifier-") + the bare name + ".md". "amplifier-" + ".." + ".md"
# concatenates to "amplifier-...md" (THREE dots: the two from the name, plus the one
# that separates the ".md" suffix) -- confirmed empirically in the DTU, not just derived
# on paper, precisely because this arithmetic is easy to get wrong by one dot. Kept as a
# literal (not computed from cli.py) so the test asserts an independently-derived
# expectation rather than re-running the exact code path it is meant to catch.
AGENT_DIR = f"{PROJECT_DIR}/.opencode/agent"
AGENTS_MANIFEST = f"{PROJECT_DIR}/.opencode/.amplifier-generated-agents.json"
REFUSED_AGENT_FILENAME = "amplifier-...md"
REFUSED_AGENT_PATH = f"{AGENT_DIR}/{REFUSED_AGENT_FILENAME}"

# The refusal wording `_usable_bridge_rows` prints for a rejected name (see cli.py
# and docs/spec/skills-and-modes-bridge.md, "Name safety"): "<kind>: refusing
# '<name>' -- unsafe name (must be a bare filename of [A-Za-z0-9._-] characters);
# not bridged". Pinned to the "modes" kind AND this specific name (not a bare
# "unsafe name" substring) because the still-present hostile SKILL seeded by
# ``traversal_session`` above triggers its OWN "skills: refusing ... -- unsafe
# name" line on every server startup in this module -- a bare substring match
# would pass on that unrelated line and never actually probe the modes face.
REFUSAL_MARKER = f"modes: refusing {HOSTILE_MODE_NAME!r} -- unsafe name"


def _seed_hostile_mode(dtu_id: str) -> None:
    """Render and push the hostile mode file (stem "..") into the DTU's project mode dir."""
    content = _MODE_TEMPLATE_PATH.read_text(encoding="utf-8").replace("{NAME}", HOSTILE_MODE_NAME)
    parent = HOSTILE_MODE_SEED_PATH.rsplit("/", 1)[0]
    dtu.exec_json(dtu_id, ["bash", "-lc", f"mkdir -p {shlex.quote(parent)}"])
    with tempfile.NamedTemporaryFile(
        "w", suffix=".md", prefix="hostile-mode-", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(content)
        local_path = handle.name
    dtu.file_push(dtu_id, local_path, HOSTILE_MODE_SEED_PATH)


@pytest.fixture(scope="module")
def hostile_mode_session(dtu_id: str) -> Generator[subprocess.CompletedProcess[str], None, None]:
    """Seed the hostile mode, then run ``amplifier-opencode prepare`` so the launcher bridges it.

    Unlike ``traversal_session`` above (the skills-face fixture), this drives ``prepare``
    directly via ``driver.run_command`` rather than ``launch`` under tmux: ``prepare`` never
    execs opencode, so it returns synchronously with the launcher's own stdout captured --
    exactly the surface the refusal-message assertion in ``test_traversal.py`` needs.

    Preconditions, in the same order as ``traversal_session``:

    1. Any previously-generated refused-name artifact is removed, so its later absence/
       presence can only be explained by THIS run.
    2. Any already-running amplifier-agent is stopped and we wait for its port to free --
       mode discovery is fixed at server STARTUP, so a reused server would never see the
       freshly-seeded hostile mode and this suite would pass VACUOUSLY.
    3. The hostile mode file is seeded, so startup discovery picks it up.
    """
    tmux_session = f"{TMUX_SESSION}"
    exec_prefix = ["amplifier-digital-twin", "exec", dtu_id, "--"]
    driver = TmuxTuiDriver(tmux_session, exec_prefix=exec_prefix)

    driver.run_command(["bash", "-lc", f"rm -f {shlex.quote(REFUSED_AGENT_PATH)}"])
    _stop_agent_server(driver)
    _seed_hostile_mode(dtu_id)

    project_dir = f"{PROJECT_DIR}"
    driver.run_command(["bash", "-lc", f"mkdir -p {shlex.quote(project_dir)}"])
    result = driver.run_command(
        [
            "bash",
            "-lc",
            f"cd {shlex.quote(project_dir)} && "
            f"amplifier-opencode --yes prepare --project-dir {shlex.quote(project_dir)}",
        ]
    )
    try:
        yield result
    finally:
        # Leave a clean slate: remove the seed and any file the (pre-fix) bridge
        # generated for it, so a later suite/run never observes a stale artifact.
        driver.run_command(["bash", "-lc", f"rm -f {shlex.quote(HOSTILE_MODE_SEED_PATH)}"])
        driver.run_command(["bash", "-lc", f"rm -f {shlex.quote(REFUSED_AGENT_PATH)}"])
