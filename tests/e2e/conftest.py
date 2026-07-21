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

from framework import dtu, state
from framework.driver import TmuxTuiDriver
from framework.judge import AIUserJudge

# In-DTU project dir the TUI launches from.
PROJECT_DIR = "/root/oc-e2e"
TMUX_SESSION = "oc-e2e"


def pytest_configure(config: pytest.Config) -> None:
    """Register the `dtu` marker (also declared in pyproject for strict-markers)."""
    config.addinivalue_line(
        "markers", "dtu: DTU-based end-to-end tests requiring amplifier-digital-twin"
    )


@pytest.fixture(scope="session", autouse=True)
def _require_dtu_cli() -> None:
    """Skip the entire e2e suite when the DTU CLI is not installed."""
    if shutil.which("amplifier-digital-twin") is None:
        pytest.skip("amplifier-digital-twin not on PATH", allow_module_level=True)


@pytest.fixture(scope="session")
def dtu_id() -> str:
    """The warm DTU instance id, skipping if none is provisioned or it is not ready."""
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
