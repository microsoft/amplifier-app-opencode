"""Shared plumbing for the agent-lifecycle suite: no seeding, just server control.

Unlike ``suites/bridge``, ``suites/shadowing``, and ``suites/traversal``, this suite seeds
nothing -- it drives the amplifier-agent process directly (stop it, confirm it is down,
start it via ``prepare``, confirm reuse, confirm the ``--no-start`` refusal) against the
real installed ``amplifier-opencode`` binary. The one fixture here (``driver``) is just a
thin, module-scoped wrapper for running commands inside the DTU; no TUI is ever spawned.
"""

from __future__ import annotations

import shlex
import subprocess
import time

import pytest

# PROJECT_DIR / TMUX_SESSION are the in-DTU project dir and tmux session name
# (``/root/oc-e2e`` / ``oc-e2e``). Defined in the e2e root conftest, which puts its own
# directory on sys.path, so they import cleanly here. Rebound through an f-string so the
# static type is a plain str: the dynamic ``from conftest import`` confuses the type
# checker into treating the name as the conftest module (same workaround the other DTU
# suites use), and so the test module can import a plain string from THIS conftest
# instead of reaching past it to the root one.
from conftest import PROJECT_DIR as _PROJECT_DIR
from conftest import TMUX_SESSION as _TMUX_SESSION
from framework.driver import TmuxTuiDriver

PROJECT_DIR = f"{_PROJECT_DIR}"
TMUX_SESSION = f"{_TMUX_SESSION}"

# The agent base URL / API key this tool defaults to (see
# ``amplifier_app_opencode.cli.DEFAULT_BASE_URL`` / ``DEFAULT_API_KEY``); every test in
# this suite drives the server at this address rather than overriding it, so "the agent"
# means the same process throughout.
BASE_URL = "http://127.0.0.1:9099/v1"
API_KEY = "local-dev-secret"


def _stop_agent_server(driver: TmuxTuiDriver, *, timeout: float = 30.0) -> None:
    """Kill any running amplifier-agent in the DTU and wait for the process to go away.

    Mirrors ``suites/shadowing``, ``suites/traversal``, and ``suites/bridge``'s helper
    (kept local rather than shared: it is a six-line suite precondition, and
    ``framework/`` is deliberately stable). Waiting for the process -- rather than just
    signalling -- closes the race where a liveness probe still succeeds against a process
    on its way down.

    The ``[ ]`` bracket regex prevents ``pkill`` from matching its own command line.
    """
    driver.run_command(["bash", "-lc", 'pkill -f "amplifier-agent[ ]serve" || true'])
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        proc = driver.run_command(["bash", "-lc", 'pgrep -f "amplifier-agent[ ]serve" || true'])
        if not proc.stdout.strip():
            return
        time.sleep(1.0)
    raise RuntimeError(f"amplifier-agent still running after {timeout}s; refusing to continue")


def _agent_pid(driver: TmuxTuiDriver) -> str:
    """Return the amplifier-agent serve PID(s) as reported by ``pgrep`` (empty if down)."""
    proc = driver.run_command(["bash", "-lc", 'pgrep -f "amplifier-agent[ ]serve" || true'])
    return proc.stdout.strip()


def _run_prepare(
    driver: TmuxTuiDriver, project_dir: str, *, extra_args: str = ""
) -> subprocess.CompletedProcess[str]:
    """Run ``amplifier-opencode --yes prepare`` (plus any ``extra_args``) and return it.

    ``--yes`` keeps the bootstrap preflight non-interactive (there is no tty here).
    ``2>&1`` folds stderr in so a failure is visible in the same captured text.
    """
    return driver.run_command(
        [
            "bash",
            "-lc",
            f"cd {shlex.quote(project_dir)} && "
            f"amplifier-opencode --yes prepare --project-dir {shlex.quote(project_dir)} "
            f"{extra_args} 2>&1",
        ]
    )


@pytest.fixture(scope="module")
def driver(dtu_id: str) -> TmuxTuiDriver:
    """A driver bound to the DTU, with the shared project dir present.

    Module-scoped: every test in this suite only ever runs plain commands through it
    (never spawns a tmux-hosted TUI), so a single instance is safely reused.
    """
    tmux_session = f"{TMUX_SESSION}"
    project_dir = f"{PROJECT_DIR}"
    exec_prefix = ["amplifier-digital-twin", "exec", dtu_id, "--"]
    d = TmuxTuiDriver(tmux_session, exec_prefix=exec_prefix)
    d.run_command(["bash", "-lc", f"mkdir -p {shlex.quote(project_dir)}"])
    return d
