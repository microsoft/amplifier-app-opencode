"""DTU-backed test: a shadowed skill must be REPORTED by the launcher, not hidden.

Ground-truth assertions only -- no TUI, no AI judge. Two things are checked against one
``amplifier-opencode prepare`` run over a seeded name collision:

* its output carries a conflict block naming BOTH the file that runs and the file that
  was shadowed, and
* the bridge still produced exactly one command file for the name, recorded once in the
  ownership manifest.

Which of the two seeded files wins is the AGENT's precedence decision; these tests assert
only that both are named and that they land on opposite sides of the report, so a future
precedence change does not turn this suite red for the wrong reason.
"""

from __future__ import annotations

import json
import re
import shlex

import pytest
from framework.driver import TmuxTuiDriver
from suites.shadowing.conftest import (
    COMMANDS_MANIFEST,
    GENERATED_COMMAND,
    PROJECT_SKILL_PATH,
    SHADOW_SKILL_NAME,
    USER_SKILL_PATH,
    PreparedRun,
)

# ``dtu`` only -- deliberately NOT ``fresh_dtu``. amplifier-agent fixes skill discovery at
# server STARTUP, but this suite needs no launch-time env overrides, so the fixture kills
# the running server and lets ``prepare`` start a fresh one. That gets the same guarantee
# against the WARM DTU without a full reprovision per run (same trade as suites/traversal).
pytestmark = pytest.mark.dtu

_CONFLICT_HEADER = re.compile(r"skills: (\d+) name conflicts?")


def _labelled_paths(stdout: str, label: str) -> list[str]:
    """Return the path on every ``<label>: <path>`` line of the conflict report."""
    prefix = f"{label}:"
    return [
        line.split(prefix, 1)[1].strip() for line in stdout.splitlines() if prefix in line.strip()
    ]


def _read_text(driver: TmuxTuiDriver, path: str) -> str:
    """Return the contents of ``path`` inside the DTU, or ``""`` if unreadable."""
    proc = driver.run_command(["bash", "-lc", f"cat {shlex.quote(path)} 2>/dev/null"])
    return proc.stdout


def test_prepare_reports_the_shadowed_skill(shadowed_prepare: PreparedRun) -> None:
    """``prepare`` prints a conflict block naming the winner AND every shadowed file.

    This is the whole point of the feature: before shadow reporting, the losing copy
    vanished with no trace anywhere in the user-visible output.
    """
    stdout = shadowed_prepare.stdout
    assert shadowed_prepare.returncode == 0, f"prepare failed:\n{stdout}"

    header = _CONFLICT_HEADER.search(stdout)
    assert header, (
        "expected a 'skills: N name conflict(s)' block naming the seeded collision on "
        f"{SHADOW_SKILL_NAME!r}; full prepare output:\n{stdout}"
    )
    assert stdout.count(SHADOW_SKILL_NAME) >= 1

    # Restrict to the two seeded paths: the DTU home is shared, so unrelated conflicts
    # from other suites' leftovers must not make this assertion pass or fail spuriously.
    seeded = {PROJECT_SKILL_PATH, USER_SKILL_PATH}
    runs = [p for p in _labelled_paths(stdout, "runs") if p in seeded]
    shadowed = [p for p in _labelled_paths(stdout, "shadowed") if p in seeded]

    assert len(runs) == 1, f"expected exactly one winner among {seeded}, got {runs}\n{stdout}"
    assert len(shadowed) == 1, (
        f"expected exactly one loser among {seeded}, got {shadowed}\n{stdout}"
    )
    # Both seeded files are accounted for, on opposite sides of the report.
    assert set(runs) | set(shadowed) == seeded


def test_shadowed_skill_still_yields_one_command_file(shadowed_prepare: PreparedRun) -> None:
    """The collision is reported, not fatal: exactly one command file, listed once.

    Reporting must not change what gets bridged -- the winning skill is still a usable
    ``/<name>`` command, and the ownership manifest records its file exactly once (a
    duplicate entry would make the next run's prune step operate on a stale view).
    """
    driver = shadowed_prepare.driver
    exists = driver.run_command(["bash", "-lc", f"test -f {shlex.quote(GENERATED_COMMAND)}"])
    assert exists.returncode == 0, (
        f"expected the bridge to generate {GENERATED_COMMAND} for the winning copy of "
        f"{SHADOW_SKILL_NAME!r}; prepare output:\n{shadowed_prepare.stdout}"
    )

    raw = _read_text(driver, COMMANDS_MANIFEST)
    assert raw.strip(), (
        f"expected an ownership manifest at {COMMANDS_MANIFEST}; the bridge writes one on "
        "every run, so an empty/absent manifest means the skills bridge never ran and this "
        f"suite would pass vacuously. prepare output:\n{shadowed_prepare.stdout}"
    )
    commands = json.loads(raw).get("commands", [])
    assert commands.count(f"{SHADOW_SKILL_NAME}.md") == 1, (
        f"expected {SHADOW_SKILL_NAME}.md exactly once in the manifest, got {commands!r}"
    )
