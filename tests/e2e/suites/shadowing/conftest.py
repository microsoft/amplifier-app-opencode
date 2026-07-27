"""Fixtures that SEED a name collision into two amplifier-agent discovery roots.

The same skill name (``e2e-shadow-probe``) is written into BOTH:

    {PROJECT_DIR}/.amplifier/skills/e2e-shadow-probe/SKILL.md   (project root)
    /root/.amplifier/skills/e2e-shadow-probe/SKILL.md           (user root)

amplifier-agent walks its skill roots in priority order and the first match wins, so one
of these runs and the other is shadowed. ``GET /v1/skills`` reports both: the winner as
``source``, the loser in ``shadowed``. Which one wins is the AGENT's business -- the
tests assert only that the launcher names both sides, so a future precedence change
does not turn this suite red for the wrong reason.

Both files are ``disable-model-invocation: true``: ``GET /v1/skills`` returns only
user-invoked skills, and only those are bridged into opencode commands.

Two preconditions the fixture establishes before running ``prepare``:

* Any running amplifier-agent is killed first. Skill discovery is fixed at server
  STARTUP and ``_run_launch`` REUSES a healthy server, so a server left behind by a
  previous run would never see the freshly-seeded probes and this suite would pass
  vacuously. Same technique as ``suites/traversal``, and the same reason this suite is
  marked ``dtu`` only rather than ``fresh_dtu``: it needs no launch-time env overrides,
  only a server that starts after the seed, so it can reuse the WARM DTU.
* The generated command file and manifest are removed, so their post-run state can only
  be explained by THIS run.

Teardown removes the seeded dirs and the generated artifacts, then stops the server --
the DTU home is shared with the other suites, and a surviving server would keep
reporting a skill whose files no longer exist.
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

# The colliding skill name, and the two SKILL.md paths that claim it.
SHADOW_SKILL_NAME = "e2e-shadow-probe"
PROJECT_SKILL_DIR = f"{PROJECT_DIR}/.amplifier/skills/{SHADOW_SKILL_NAME}"
USER_SKILL_DIR = f"{DTU_HOME}/.amplifier/skills/{SHADOW_SKILL_NAME}"
PROJECT_SKILL_PATH = f"{PROJECT_SKILL_DIR}/SKILL.md"
USER_SKILL_PATH = f"{USER_SKILL_DIR}/SKILL.md"

# What the bridge should produce for the winning skill, and where ownership is recorded.
COMMAND_DIR = f"{PROJECT_DIR}/.opencode/command"
GENERATED_COMMAND = f"{COMMAND_DIR}/{SHADOW_SKILL_NAME}.md"
COMMANDS_MANIFEST = f"{PROJECT_DIR}/.opencode/.amplifier-generated-commands.json"

_TEMPLATE_PATH = Path(__file__).parent / "fixtures" / "shadow_skill.md.tmpl"


class PreparedRun(NamedTuple):
    """One ``amplifier-opencode prepare`` run: its output plus a driver to probe the DTU."""

    driver: TmuxTuiDriver
    stdout: str
    returncode: int


def _seed_skill(dtu_id: str, dest: str, origin: str) -> None:
    """Render the probe template for ``origin`` and push it to ``dest`` in the DTU."""
    content = (
        _TEMPLATE_PATH.read_text(encoding="utf-8")
        .replace("{NAME}", SHADOW_SKILL_NAME)
        .replace("{ORIGIN}", origin)
    )
    parent = dest.rsplit("/", 1)[0]
    dtu.exec_json(dtu_id, ["bash", "-lc", f"mkdir -p {shlex.quote(parent)}"])
    with tempfile.NamedTemporaryFile(
        "w", suffix=".md", prefix="shadow-skill-", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(content)
        local_path = handle.name
    dtu.file_push(dtu_id, local_path, dest)


def _stop_agent_server(driver: TmuxTuiDriver, *, timeout: float = 30.0) -> None:
    """Kill any running amplifier-agent in the DTU and wait for the process to go away.

    Mirrors ``suites/traversal``'s helper (kept local rather than shared: it is a
    six-line suite precondition, and ``framework/`` is deliberately stable). Waiting for
    the process -- rather than just signalling -- closes the race where the launcher's
    ``server_is_running`` probe still succeeds against a process on its way down.

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
        "stale server would not have discovered the seeded probe skills (vacuous pass)"
    )


@pytest.fixture(scope="module")
def shadowed_prepare(dtu_id: str) -> Generator[PreparedRun, None, None]:
    """Seed the collision, run ``prepare``, and yield its captured output.

    Module-scoped: the run is the expensive part and every test in this suite asserts on
    the SAME output and post-run filesystem state, so one run serves them all.

    ``prepare`` rather than ``launch``: it performs the identical setup (start server ->
    discover -> write config -> bridge skills/modes) but does not exec opencode, so its
    stdout is capturable. The conflict block is printed during that setup, which under
    ``launch`` scrolls away the instant the TUI paints -- unreadable via tmux, which is
    why this suite does not drive the TUI at all.
    """
    # Rebind through f-strings so the static type is a plain str: the dynamic
    # ``from conftest import`` confuses the type checker into treating the names as the
    # conftest module (same workaround the skills and traversal suites use).
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
            f"rm -rf {shlex.quote(PROJECT_SKILL_DIR)} {shlex.quote(USER_SKILL_DIR)} && "
            f"rm -f {shlex.quote(GENERATED_COMMAND)} {shlex.quote(COMMANDS_MANIFEST)}",
        ]
    )
    _stop_agent_server(driver)
    _seed_skill(dtu_id, PROJECT_SKILL_PATH, "project")
    _seed_skill(dtu_id, USER_SKILL_PATH, "user")

    # ``--yes`` keeps the bootstrap preflight non-interactive (there is no tty here).
    # ``2>&1`` folds stderr in so a failure is visible in the same captured text.
    proc = driver.run_command(
        [
            "bash",
            "-lc",
            f"cd {shlex.quote(project_dir)} && "
            f"amplifier-opencode --yes prepare --project-dir {shlex.quote(project_dir)} 2>&1",
        ]
    )
    try:
        yield PreparedRun(driver=driver, stdout=proc.stdout, returncode=proc.returncode)
    finally:
        # The DTU home is shared with the other suites: remove the seeds and the files
        # this run generated, then stop the server so it stops reporting a deleted skill.
        driver.run_command(
            [
                "bash",
                "-lc",
                f"rm -rf {shlex.quote(PROJECT_SKILL_DIR)} {shlex.quote(USER_SKILL_DIR)} && "
                f"rm -f {shlex.quote(GENERATED_COMMAND)}",
            ]
        )
        _stop_agent_server(driver)
