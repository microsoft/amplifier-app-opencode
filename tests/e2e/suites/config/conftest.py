"""Fixtures that clean, restart, and run `prepare` so tests assert on ONE known-good config.

The generated `opencode.json` is deterministic only relative to a clean slate: any
config left over from a previous suite run (or another suite sharing the DTU home)
must not be mistaken for THIS run's output, and the model list embedded in it is fixed
at server STARTUP, so a server left running from a prior run would not reflect a fresh
`prepare`. Both are addressed before the single `prepare` invocation every test in this
suite reads from.
"""

from __future__ import annotations

import shlex
import time
from collections.abc import Generator
from typing import NamedTuple

import pytest

# PROJECT_DIR / TMUX_SESSION are the in-DTU project dir and tmux session name
# (``/root/oc-e2e`` / ``oc-e2e``). Defined in the e2e root conftest, which puts its own
# directory on sys.path, so they import cleanly here.
from conftest import PROJECT_DIR, TMUX_SESSION
from framework.driver import TmuxTuiDriver

# Project scope has no candidate search (unlike global scope): the target is always the
# fixed path ``<project-dir>/opencode.json`` (see docs/spec/opencode-config.md).
CONFIG_PATH = f"{PROJECT_DIR}/opencode.json"


class PreparedConfig(NamedTuple):
    """One ``amplifier-opencode prepare`` run: its output, plus a driver+dtu_id to probe with."""

    driver: TmuxTuiDriver
    dtu_id: str
    stdout: str
    returncode: int


def _stop_agent_server(driver: TmuxTuiDriver, *, timeout: float = 30.0) -> None:
    """Kill any running amplifier-agent in the DTU and wait for the process to go away.

    Mirrors ``suites/shadowing``/``suites/traversal``'s helper (kept local rather than
    shared: it is a six-line suite precondition, and ``framework/`` is deliberately
    stable). Waiting for the process -- rather than just signalling -- closes the race
    where the launcher's ``server_is_running`` probe still succeeds against a process
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
    raise RuntimeError(
        f"amplifier-agent still running after {timeout}s; refusing to continue because a "
        "stale server would not reflect a clean model-discovery run (vacuous pass)"
    )


@pytest.fixture(scope="module")
def prepared_config(dtu_id: str) -> Generator[PreparedConfig, None, None]:
    """Clean slate, restart the server, run ``prepare``, and yield its captured output.

    Module-scoped: the run is the expensive part and every test in this suite asserts on
    the SAME generated config, so one run serves them all.

    Two preconditions are established before running ``prepare``:

    * Any prior generated config at ``CONFIG_PATH`` is removed, so its content can only
      be explained by THIS run.
    * Any already-running amplifier-agent is killed first, since model discovery is
      fixed at server STARTUP and a server left behind by a previous run would embed
      whichever models it discovered then, not a fresh listing.

    ``prepare`` rather than ``launch``: it performs the identical setup (start server ->
    discover -> write config) but does not exec opencode, so its stdout is capturable
    and no TUI needs to be driven at all.

    Teardown removes the generated config, then stops the server -- the DTU home is
    shared with the other suites, and a surviving server or stale config would leak
    into whichever suite runs next.
    """
    tmux_session = f"{TMUX_SESSION}"
    project_dir = f"{PROJECT_DIR}"
    exec_prefix = ["amplifier-digital-twin", "exec", dtu_id, "--"]
    # Only ``run_command`` is used -- no TUI is spawned; the session name is never bound.
    driver = TmuxTuiDriver(tmux_session, exec_prefix=exec_prefix)

    driver.run_command(
        [
            "bash",
            "-lc",
            f"mkdir -p {shlex.quote(project_dir)} && rm -f {shlex.quote(CONFIG_PATH)}",
        ]
    )
    _stop_agent_server(driver)

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
        yield PreparedConfig(
            driver=driver, dtu_id=dtu_id, stdout=proc.stdout, returncode=proc.returncode
        )
    finally:
        driver.run_command(["bash", "-lc", f"rm -f {shlex.quote(CONFIG_PATH)}"])
        _stop_agent_server(driver)
