"""Fixtures that SEED one probe skill and one probe mode, then run ``prepare`` over them.

One probe of each face, both sharing the safe, unique name ``e2e-bridge-probe`` (they
live in different discovery roots -- a skill directory vs. a mode file stem -- so the
shared name is not itself a collision):

    {PROJECT_DIR}/.amplifier/skills/e2e-bridge-probe/SKILL.md   (project skill)
    {PROJECT_DIR}/.amplifier/modes/e2e-bridge-probe.md          (project mode)

Mode seeding mirrors ``suites/modes/conftest.py`` exactly: a single flat ``<name>.md``
file under the amplifier modes directory, file stem as the mode name. That suite already
proves this seeding path works end to end (its ``_warm_modes_server`` fixture polls
``GET /v1/modes`` until a project-seeded mode appears), so this suite reuses it directly
rather than falling back to a shipped-in mode.

Three ``prepare`` runs build on each other, each a module-scoped fixture depending on the
previous one so pytest's fixture graph -- not test collection order -- guarantees the
sequence regardless of which test asks for which fixture first:

    bridge_prepare        seed skill + mode -> prepare               (tests 1-3)
    pruned_prepare        remove the skill source -> prepare again   (test 4: reconciliation prunes)
    user_owned_prepare    drop a foreign file in command/ -> prepare again
                                                                      (test 5: never overwritten)

Like ``suites/shadowing`` and ``suites/traversal``, any running amplifier-agent is killed
before each seed so a stale server -- which fixed its skill/mode discovery at STARTUP --
cannot make this suite pass vacuously against unseeded state.
"""

from __future__ import annotations

import shlex
import tempfile
import time
from collections.abc import Generator
from pathlib import Path
from typing import NamedTuple

import pytest

# PROJECT_DIR / TMUX_SESSION are the in-DTU project dir and tmux session name
# (``/root/oc-e2e`` / ``oc-e2e``). Defined in the e2e root conftest, which puts its own
# directory on sys.path, so they import cleanly here.
from conftest import PROJECT_DIR, TMUX_SESSION
from framework import dtu
from framework.driver import TmuxTuiDriver

# The DTU installs the stack under the root user; ``$HOME`` is ``/root`` there.
DTU_HOME = "/root"

# The shared probe name for both faces (see module docstring for why one name is safe
# across two discovery roots).
BRIDGE_NAME = "e2e-bridge-probe"

PROJECT_SKILL_DIR = f"{PROJECT_DIR}/.amplifier/skills/{BRIDGE_NAME}"
PROJECT_SKILL_PATH = f"{PROJECT_SKILL_DIR}/SKILL.md"
PROJECT_MODE_PATH = f"{PROJECT_DIR}/.amplifier/modes/{BRIDGE_NAME}.md"

COMMAND_DIR = f"{PROJECT_DIR}/.opencode/command"
AGENT_DIR = f"{PROJECT_DIR}/.opencode/agent"
COMMANDS_MANIFEST = f"{PROJECT_DIR}/.opencode/.amplifier-generated-commands.json"
AGENTS_MANIFEST = f"{PROJECT_DIR}/.opencode/.amplifier-generated-agents.json"

GENERATED_COMMAND = f"{COMMAND_DIR}/{BRIDGE_NAME}.md"
GENERATED_AGENT = f"{AGENT_DIR}/amplifier-{BRIDGE_NAME}.md"

# A filename no seeded skill or mode ever produces, standing in for a user's own file
# that happens to live in the command directory the bridge owns.
USER_OWNED_NAME = "zz-user-owned.md"
USER_OWNED_COMMAND = f"{COMMAND_DIR}/{USER_OWNED_NAME}"
USER_OWNED_CONTENT = (
    "---\n"
    'description: "zz-user-owned sentinel; amplifier-opencode must never touch this file"\n'
    "---\n"
    "echo not-generated-by-amplifier-opencode\n"
)

_SKILL_TEMPLATE_PATH = Path(__file__).parent / "fixtures" / "probe_skill.md.tmpl"
_MODE_TEMPLATE_PATH = Path(__file__).parent / "fixtures" / "probe_mode.md.tmpl"


class PreparedRun(NamedTuple):
    """One ``amplifier-opencode prepare`` run: its output plus a driver to probe the DTU."""

    driver: TmuxTuiDriver
    stdout: str
    returncode: int


def _push_rendered(dtu_id: str, template_path: Path, dest: str) -> None:
    """Render ``template_path`` (substituting ``{NAME}``) and push it to ``dest``."""
    content = template_path.read_text(encoding="utf-8").replace("{NAME}", BRIDGE_NAME)
    parent = dest.rsplit("/", 1)[0]
    dtu.exec_json(dtu_id, ["bash", "-lc", f"mkdir -p {shlex.quote(parent)}"])
    with tempfile.NamedTemporaryFile(
        "w", suffix=".md", prefix="bridge-probe-", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(content)
        local_path = handle.name
    dtu.file_push(dtu_id, local_path, dest)


def _seed_skill(dtu_id: str) -> None:
    """Write the probe SKILL.md into the project skills directory."""
    _push_rendered(dtu_id, _SKILL_TEMPLATE_PATH, PROJECT_SKILL_PATH)


def _seed_mode(dtu_id: str) -> None:
    """Write the probe mode file into the project modes directory."""
    _push_rendered(dtu_id, _MODE_TEMPLATE_PATH, PROJECT_MODE_PATH)


def _stop_agent_server(driver: TmuxTuiDriver, *, timeout: float = 30.0) -> None:
    """Kill any running amplifier-agent in the DTU and wait for the process to go away.

    Mirrors ``suites/shadowing`` and ``suites/traversal``'s helper (kept local rather than
    shared: it is a six-line suite precondition, and ``framework/`` is deliberately
    stable). Waiting for the process -- rather than just signalling -- closes the race
    where the launcher's ``server_is_running`` probe still succeeds against a process on
    its way down.

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
        "stale server would not have discovered the seeded probe skill/mode (vacuous pass)"
    )


def _run_prepare(driver: TmuxTuiDriver, project_dir: str):
    """Run ``amplifier-opencode --yes prepare`` and return the captured process.

    ``--yes`` keeps the bootstrap preflight non-interactive (there is no tty here).
    ``2>&1`` folds stderr in so a failure is visible in the same captured text.
    """
    return driver.run_command(
        [
            "bash",
            "-lc",
            f"cd {shlex.quote(project_dir)} && "
            f"amplifier-opencode --yes prepare --project-dir {shlex.quote(project_dir)} 2>&1",
        ]
    )


@pytest.fixture(scope="module")
def bridge_prepare(dtu_id: str) -> Generator[PreparedRun, None, None]:
    """Seed the probe skill + mode, run ``prepare``, and yield its captured output.

    Module-scoped: the run is the expensive part and every test that consumes this
    fixture asserts on the SAME output and post-run filesystem state, so one run serves
    them all.
    """
    # Rebind through f-strings so the static type is a plain str: the dynamic
    # ``from conftest import`` confuses the type checker into treating the names as the
    # conftest module (same workaround the other DTU suites use).
    tmux_session = f"{TMUX_SESSION}"
    project_dir = f"{PROJECT_DIR}"
    exec_prefix = ["amplifier-digital-twin", "exec", dtu_id, "--"]
    # Only ``run_command`` is used -- no TUI is spawned; the session name is never bound.
    driver = TmuxTuiDriver(tmux_session, exec_prefix=exec_prefix)

    # Clean slate: the artifacts asserted on must be attributable to THIS run.
    driver.run_command(
        [
            "bash",
            "-lc",
            f"mkdir -p {shlex.quote(project_dir)} && "
            f"rm -rf {shlex.quote(PROJECT_SKILL_DIR)} && "
            f"rm -f {shlex.quote(PROJECT_MODE_PATH)} && "
            f"rm -f {shlex.quote(GENERATED_COMMAND)} {shlex.quote(GENERATED_AGENT)} && "
            f"rm -f {shlex.quote(COMMANDS_MANIFEST)} {shlex.quote(AGENTS_MANIFEST)}",
        ]
    )
    _stop_agent_server(driver)
    _seed_skill(dtu_id)
    _seed_mode(dtu_id)

    proc = _run_prepare(driver, project_dir)
    try:
        yield PreparedRun(driver=driver, stdout=proc.stdout, returncode=proc.returncode)
    finally:
        # The DTU home is shared with the other suites: remove the seeds and the files
        # this run generated, then stop the server so it stops reporting deleted content.
        driver.run_command(
            [
                "bash",
                "-lc",
                f"rm -rf {shlex.quote(PROJECT_SKILL_DIR)} && "
                f"rm -f {shlex.quote(PROJECT_MODE_PATH)} && "
                f"rm -f {shlex.quote(GENERATED_COMMAND)} {shlex.quote(GENERATED_AGENT)} && "
                f"rm -f {shlex.quote(USER_OWNED_COMMAND)}",
            ]
        )
        _stop_agent_server(driver)


@pytest.fixture(scope="module")
def pruned_prepare(bridge_prepare: PreparedRun) -> Generator[PreparedRun, None, None]:
    """Remove the seeded skill's source, then run ``prepare`` again.

    Depends on ``bridge_prepare`` so the initial generated files exist first; this
    fixture's own state (the skill source gone) is what test 4 asserts against.
    """
    project_dir = f"{PROJECT_DIR}"
    driver = bridge_prepare.driver
    driver.run_command(["bash", "-lc", f"rm -rf {shlex.quote(PROJECT_SKILL_DIR)}"])
    _stop_agent_server(driver)
    proc = _run_prepare(driver, project_dir)
    yield PreparedRun(driver=driver, stdout=proc.stdout, returncode=proc.returncode)


@pytest.fixture(scope="module")
def user_owned_prepare(
    dtu_id: str, pruned_prepare: PreparedRun
) -> Generator[PreparedRun, None, None]:
    """Drop a file the bridge does not own into ``command/``, then run ``prepare`` again.

    Depends on ``pruned_prepare`` so it runs after that scenario, keeping the three
    ``prepare`` runs in a single deterministic chain regardless of test collection order.
    """
    with tempfile.NamedTemporaryFile(
        "w", suffix=".md", prefix="user-owned-", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(USER_OWNED_CONTENT)
        local_path = handle.name
    dtu.file_push(dtu_id, local_path, USER_OWNED_COMMAND)

    project_dir = f"{PROJECT_DIR}"
    driver = pruned_prepare.driver
    proc = _run_prepare(driver, project_dir)
    try:
        yield PreparedRun(driver=driver, stdout=proc.stdout, returncode=proc.returncode)
    finally:
        driver.run_command(["bash", "-lc", f"rm -f {shlex.quote(USER_OWNED_COMMAND)}"])
