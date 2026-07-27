"""Pytest wiring for the DTU-based opencode TUI e2e suite.

The suite attaches to a WARM DTU provisioned out-of-band by
``uv run python tests/e2e/cli.py up`` (or auto-provisioned by ``cli.py run``). Tests
read the shared state file to find the instance rather than launching their own, and
self-skip cleanly whenever the DTU tooling or a warm instance is absent -- so a plain
``uv run pytest`` stays green on any host.
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Generator
from pathlib import Path

import pytest

# Make the `framework` and `suites` packages importable (this file's own directory,
# tests/e2e/, is their shared parent).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from framework import dtu, dtu_manager, state
from framework.driver import TmuxTuiDriver
from framework.judge import AIUserJudge
from framework.progress import log

# In-DTU project dir the TUI launches from.
PROJECT_DIR = "/root/oc-e2e"
TMUX_SESSION = "oc-e2e"


def pytest_configure(config: pytest.Config) -> None:
    """Register the `dtu` and `fresh_dtu` markers (registration satisfies strict-markers)."""
    config.addinivalue_line(
        "markers", "dtu: DTU-based end-to-end tests requiring amplifier-digital-twin"
    )
    config.addinivalue_line(
        "markers",
        "fresh_dtu: requires a freshly-provisioned DTU (clean amplifier-agent server) rather "
        "than the reused warm DTU; forces one fresh provision per session",
    )


@pytest.fixture(scope="session", autouse=True)
def _require_dtu_cli() -> None:
    """Skip the entire e2e suite when the DTU CLI is not installed."""
    if shutil.which("amplifier-digital-twin") is None:
        pytest.skip("amplifier-digital-twin not on PATH", allow_module_level=True)


@pytest.fixture(scope="session")
def _fresh_dtu(request: pytest.FixtureRequest) -> bool:
    """Force ONE fresh DTU provision per session when any collected test wants it.

    amplifier-agent is a long-lived server whose skill discovery is fixed at server
    STARTUP. Tests marked ``fresh_dtu`` (e.g. the skills suite) configure discovery via
    launch-time overrides (``AMPLIFIER_SKILLS_DIR`` / ``--host-config``), which a stale
    server reused from a prior run would ignore. Provisioning a clean DTU guarantees no
    pre-existing server, so the marked suite's launch starts a server that honors them.

    Runs the existing ``dtu_manager.provision()`` lifecycle exactly once per session, and
    only when a ``fresh_dtu``-marked item is collected -- so non-marked suites (chat) keep
    reusing the warm DTU unchanged. ``dtu_id`` depends on this, so the fresh provision
    completes (and rewrites the state file) before any test reads the instance id.
    """
    wants_fresh = any(
        item.get_closest_marker("fresh_dtu") is not None for item in request.session.items
    )
    if not wants_fresh:
        return False
    log("conftest: fresh_dtu-marked tests present; provisioning a clean DTU for this session")
    dtu_manager.provision()
    return True


@pytest.fixture(scope="session")
def dtu_id(_fresh_dtu: bool) -> str:
    """The DTU instance id, skipping if none is provisioned or it is not ready.

    Depends on ``_fresh_dtu`` so that, when the session includes ``fresh_dtu``-marked
    tests, a clean DTU is provisioned first and this reads that fresh instance. Otherwise
    it reads the warm DTU as before.
    """
    current = state.read()
    if current is None:
        pytest.skip("no warm DTU; run `uv run python tests/e2e/cli.py up`")
        raise RuntimeError("unreachable")  # help pyright narrow past pytest.skip
    instance_id = current["dtu_id"]
    if not dtu.check_ready(instance_id):
        pytest.skip(f"DTU {instance_id} not ready; re-run `up`")
    return instance_id


@pytest.fixture
def opencode_session(dtu_id: str) -> Generator[TmuxTuiDriver, None, None]:
    """Launch the opencode TUI inside the DTU and yield a ready driver.

    Spawns ``amplifier-opencode launch --project-dir <dir>`` under tmux, waits for the
    main-screen marker, yields the driver, and kills the session on teardown.
    """
    exec_prefix = ["amplifier-digital-twin", "exec", dtu_id, "--"]
    driver = TmuxTuiDriver(TMUX_SESSION, exec_prefix=exec_prefix)
    driver.run_command(["bash", "-lc", f"mkdir -p {PROJECT_DIR}"])
    driver.spawn(f"amplifier-opencode launch --project-dir {PROJECT_DIR}")
    try:
        driver.wait_for_text("tab agents", timeout=120)
        yield driver
    finally:
        driver.close()


@pytest.fixture
def judge() -> AIUserJudge:
    """The AI-user judge used to evaluate TUI screens."""
    return AIUserJudge()
