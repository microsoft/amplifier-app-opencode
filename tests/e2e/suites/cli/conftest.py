"""Fixtures for the CLI smoke-test suite: a driver bound to the warm DTU.

No fixture here seeds files, stops the server, or launches a TUI -- every test in this
suite runs a single non-interactive `amplifier-opencode` invocation and reads its exit
code / stdout, so the only shared setup worth factoring out is the driver itself (to
avoid re-deriving the exec prefix per test) and one shared `doctor` run (two tests
assert on that single, slightly more expensive invocation).
"""

from __future__ import annotations

from typing import NamedTuple

import pytest

# TMUX_SESSION is the in-DTU tmux session name convention from the e2e root conftest
# (``oc-e2e``). Defined in the e2e root conftest, which puts its own directory on
# sys.path, so it imports cleanly here. No tmux session is actually spawned by this
# suite (only ``run_command`` is used), but the name is suffixed to keep it visibly
# distinct from the TUI suites' session if anyone ever inspects `tmux ls` in the DTU.
from conftest import TMUX_SESSION
from framework.driver import TmuxTuiDriver

_CLI_SESSION_SUFFIX = "-cli"


class DoctorRun(NamedTuple):
    """One ``amplifier-opencode doctor`` invocation: output plus the driver used."""

    driver: TmuxTuiDriver
    stdout: str
    returncode: int


@pytest.fixture(scope="module")
def cli_driver(dtu_id: str) -> TmuxTuiDriver:
    """A driver bound to the warm DTU, for read-only command-surface checks.

    Module-scoped and never spawns a tmux session: every test here only needs
    ``run_command`` (a bare ``amplifier-digital-twin exec ... -- bash -lc '...'``), so
    binding the exec prefix once and reusing it avoids repeating that wiring per test.
    """
    # Rebind through an f-string so the static type is a plain str: the dynamic
    # ``from conftest import`` confuses the type checker into treating the name as the
    # conftest module (same workaround the shadowing/traversal suites use).
    session = f"{TMUX_SESSION}{_CLI_SESSION_SUFFIX}"
    exec_prefix = ["amplifier-digital-twin", "exec", dtu_id, "--"]
    return TmuxTuiDriver(session, exec_prefix=exec_prefix)


@pytest.fixture(scope="module")
def doctor_run(cli_driver: TmuxTuiDriver) -> DoctorRun:
    """Run ``amplifier-opencode doctor`` once; two tests assert on the same output.

    Module-scoped because ``doctor`` probes the live server and provider credentials
    over the network -- re-running it per assertion would double that (cheap but
    non-zero) round trip for no benefit, since both tests read the same report.
    """
    proc = cli_driver.run_command(["bash", "-lc", "amplifier-opencode doctor 2>&1"])
    return DoctorRun(driver=cli_driver, stdout=proc.stdout, returncode=proc.returncode)
