"""DTU-backed adversarial test: a hostile skill NAME must not escape the command dir.

Covers open issue #5 (launcher path traversal). Unlike the other suites, these are not
TUI cases -- there is nothing to read off the screen. The bridge's effect is a FILESYSTEM
side effect, so the assertions are ground truth gathered with ``driver.run_command``, in
the same spirit as ``framework/assertions.py``.

Two primitives are asserted, both stemming from the same missing validation:

* WRITE -- ``command_dir / f"{name}.md"`` with a traversing ``name`` lands outside
  ``command_dir``. Immediate, silent (``os.replace``), and arbitrary.
* DELETE (delayed) -- the same traversing name is recorded in the ownership manifest.
  On a LATER run, the prune step calls ``(command_dir / stale).unlink()`` on it, which
  is an arbitrary-delete primitive armed by this run. This test asserts the manifest is
  clean rather than waiting a run to observe the deletion, so the arming is caught at
  the point it happens.

Both are expected to FAIL until a shared name validator is applied inside
``fetch_skills``/``fetch_modes`` (reject ``name != Path(name).name``, reject absolute,
whitelist ``[A-Za-z0-9._-]+``).
"""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import PurePosixPath

import pytest
from framework import dtu
from framework.driver import TmuxTuiDriver
from suites.traversal.conftest import (
    AGENT_DIR,
    AGENTS_MANIFEST,
    COMMAND_DIR,
    COMMANDS_MANIFEST,
    ESCAPED_WRITE_PATH,
    HOSTILE_MODE_NAME,
    HOSTILE_SKILL_NAME,
    REFUSAL_MARKER,
    REFUSED_AGENT_FILENAME,
    REFUSED_AGENT_PATH,
)

# ``dtu`` only -- deliberately NOT ``fresh_dtu``. amplifier-agent fixes skill discovery at
# server STARTUP, but unlike the skills suite this one needs no launch-time env overrides,
# so ``traversal_session`` kills the running server and lets the launch start a fresh one.
# That gets the same guarantee against the WARM DTU, without a full reprovision per run.
pytestmark = pytest.mark.dtu


def _path_exists(driver: TmuxTuiDriver, path: str) -> bool:
    """True if ``path`` exists inside the DTU."""
    proc = driver.run_command(["bash", "-lc", f"test -e {shlex.quote(path)}"])
    return proc.returncode == 0


def _read_text(driver: TmuxTuiDriver, path: str) -> str:
    """Return the contents of ``path`` inside the DTU, or ``""`` if unreadable."""
    proc = driver.run_command(["bash", "-lc", f"cat {shlex.quote(path)} 2>/dev/null"])
    return proc.stdout


def test_hostile_skill_name_does_not_escape_command_dir(
    traversal_session: TmuxTuiDriver,
) -> None:
    """The bridge must not write outside ``command_dir``.

    The hostile skill's name is ``../../../../tmp/e2e-traversal-pwned``. Naive
    ``command_dir / f"{name}.md"`` resolves to ``/tmp/e2e-traversal-pwned.md``. The
    fixture deletes that path before launching, so its existence afterwards can only be
    this run's bridge write.
    """
    driver = traversal_session
    escaped = _path_exists(driver, ESCAPED_WRITE_PATH)

    # Pull the written body into the failure message: it proves the file is OURS (it
    # carries the generated `!amplifier:skill` line) rather than an unrelated artifact.
    body = _read_text(driver, ESCAPED_WRITE_PATH) if escaped else ""
    assert not escaped, (
        f"path traversal: the launcher wrote OUTSIDE {COMMAND_DIR} to "
        f"{ESCAPED_WRITE_PATH}, driven by the skill name {HOSTILE_SKILL_NAME!r}.\n"
        f"written content:\n{body}"
    )


def test_hostile_skill_name_is_not_recorded_in_manifest(
    traversal_session: TmuxTuiDriver,
) -> None:
    """The ownership manifest must never record a traversing filename.

    A traversal name in the manifest arms the DELETE primitive: the next run's prune
    step unlinks ``command_dir / <recorded name>`` for any entry no longer backed by a
    skill. Asserting the manifest is clean catches the arming without needing a second
    launch to observe the detonation.
    """
    driver = traversal_session
    raw = _read_text(driver, COMMANDS_MANIFEST)
    if not raw.strip():
        pytest.fail(
            f"expected an ownership manifest at {COMMANDS_MANIFEST}; the bridge writes one "
            "on every run, so an empty/absent manifest means the launch did not reach the "
            "skills bridge and this suite would pass vacuously"
        )

    entries = json.loads(raw).get("commands", [])
    # Safe == a bare filename: identical to its own basename, and not absolute. This is
    # the same predicate the fix should enforce at fetch time.
    unsafe = [e for e in entries if e != PurePosixPath(e).name or PurePosixPath(e).is_absolute()]
    assert not unsafe, (
        f"manifest {COMMANDS_MANIFEST} records traversing filename(s) {unsafe!r}. "
        "On the next run the prune step calls (command_dir / stale).unlink() on these, "
        f"which is an arbitrary-delete primitive.\nfull manifest:\n{raw}"
    )


# ---------------------------------------------------------------------------
# Modes-face: the mode name ".." is a value the skills face's is_safe_bridge_name
# would refuse (it is one of the two names rejected BY LITERAL VALUE), but fetch_modes
# applies no validation at all -- covers ISSUE-001 in docs/ISSUES.md.
#
# Unlike the skills-face case above, no working directory-traversal write is
# constructible here: a mode's name is a ``.md`` file STEM, which cannot itself
# contain "/" on POSIX, so MODE_AGENT_PREFIX + ".." + ".md" resolves to the single,
# ordinary filename "amplifier-..md" INSIDE agent_dir, not outside it. What these
# tests prove instead is the stated CONTRACT violation: a name the skills face
# would refuse is silently accepted by the modes face, reaching both the write
# step and the ownership manifest, and printing none of the refusal diagnostics
# the skills face would print for the same name.
# ---------------------------------------------------------------------------


def _dtu_path_exists(dtu_id: str, path: str) -> bool:
    """True if ``path`` exists inside the DTU (queried directly, no tmux session needed)."""
    res = dtu.exec_json(dtu_id, ["bash", "-lc", f"test -e {shlex.quote(path)}"])
    return res.get("exit_code") == 0


def _dtu_read_text(dtu_id: str, path: str) -> str:
    """Return the contents of ``path`` inside the DTU, or ``""`` if unreadable."""
    res = dtu.exec_json(dtu_id, ["bash", "-lc", f"cat {shlex.quote(path)} 2>/dev/null"])
    return res.get("stdout", "")


def test_hostile_mode_name_is_not_bridged_to_agent_file(
    hostile_mode_session: subprocess.CompletedProcess[str],
    dtu_id: str,
) -> None:
    """The bridge must refuse the mode name ``..`` rather than write an agent file for it.

    The mode name ``..`` is one of only two names ``is_safe_bridge_name`` rejects by literal
    value (the other being ``.``); the skills face already refuses it via the shared
    validator, and ``fetch_modes`` now routes through the same validator (ISSUE-001).
    """
    generated = _dtu_path_exists(dtu_id, REFUSED_AGENT_PATH)
    body = _dtu_read_text(dtu_id, REFUSED_AGENT_PATH) if generated else ""
    assert not generated, (
        f"the modes bridge accepted the unsafe name {HOSTILE_MODE_NAME!r} and wrote "
        f"{REFUSED_AGENT_PATH} inside {AGENT_DIR}, instead of refusing it the way the "
        f"skills face refuses the same name.\nwritten content:\n{body}"
    )


def test_hostile_mode_name_is_not_recorded_in_manifest(
    hostile_mode_session: subprocess.CompletedProcess[str],
    dtu_id: str,
) -> None:
    """The modes ownership manifest must never record a filename generated from an unsafe name.

    A manifest entry for the refused name arms the DELETE primitive on a later run: the
    prune step unlinks ``agent_dir / <recorded name>`` for any entry no longer backed by a
    mode. Asserting the manifest is clean catches the arming without needing a second run.
    """
    raw = _dtu_read_text(dtu_id, AGENTS_MANIFEST)
    if not raw.strip():
        pytest.fail(
            f"expected an ownership manifest at {AGENTS_MANIFEST}; the bridge writes one "
            "on every run, so an empty/absent manifest means the launch did not reach the "
            "modes bridge and this suite would pass vacuously"
        )

    entries = json.loads(raw).get("agents", [])
    assert REFUSED_AGENT_FILENAME not in entries, (
        f"manifest {AGENTS_MANIFEST} records {REFUSED_AGENT_FILENAME!r}, generated from the "
        f"unsafe mode name {HOSTILE_MODE_NAME!r}. On the next run the prune step calls "
        "(agent_dir / stale).unlink() on this entry if the mode no longer exists.\n"
        f"full manifest:\n{raw}"
    )


def test_hostile_mode_name_refusal_is_reported_on_stdout(
    hostile_mode_session: subprocess.CompletedProcess[str],
) -> None:
    """The refusal must be reported on stdout, matching the skills face's wording.

    ``_usable_bridge_rows`` prints ``"<kind>: refusing '<name>' -- unsafe name (must be a "
    "bare filename of [A-Za-z0-9._-] characters); not bridged"`` for the skills face. The
    modes face should print the equivalent (``kind="modes"``) for the same name.
    """
    stdout = hostile_mode_session.stdout
    assert REFUSAL_MARKER in stdout, (
        f"expected the launcher's stdout to report the refusal of {HOSTILE_MODE_NAME!r} "
        f"with wording containing {REFUSAL_MARKER!r} (matching the skills face's refusal "
        f"message), but it did not.\nfull stdout:\n{stdout}"
    )
