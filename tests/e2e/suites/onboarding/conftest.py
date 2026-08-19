"""Fixture: a fake ``amplifier-agent`` executable prepended to PATH for one command.

The real amplifier-agent cannot be made to (a) report zero resolvable providers AND (b)
fail ``auth set`` while echoing the secret back in its own error output, on demand, in a
shared warm container other suites also depend on. This fixture stands up a minimal
fake CLI that does both, deliberately, so the test can drive ``_auth_set``'s
secret-redaction branch end-to-end -- the same "control the external boundary" idiom
``suites/agent_integration`` uses with a fake HTTP server standing in for the agent's
HTTP face (see ``test_skills_endpoint_failure_does_not_block_prepare``).

Safety, since this DTU is shared with every other suite:

* The fake binary is written to its OWN scratch directory (``FAKE_BIN_DIR``), never to
  a location on the default PATH, and never named/placed so it could be picked up by a
  command that does not explicitly prepend that directory first. The real
  ``amplifier-agent`` install is never touched, moved, or overwritten.
* ``amplifier-opencode setup`` (the command this suite drives) never starts the
  amplifier-agent server and never writes any opencode config -- it only runs the
  self-heal preflight and, when triggered, the credential wizard. There is nothing here
  for another suite to collide with.
* The scratch directory is removed in fixture teardown (``finally``), so a failed test
  does not leave the fake binary behind for a later suite to trip over.
"""

from __future__ import annotations

import shlex
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

# PROJECT_DIR / TMUX_SESSION are the in-DTU project dir and the tmux session name the
# root conftest uses for the TUI suites. This suite spawns its OWN tmux session (a
# different name) since it drives a different command entirely; only the DTU exec
# machinery is shared.
from conftest import TMUX_SESSION as _ROOT_TMUX_SESSION
from framework.driver import TmuxTuiDriver

# Rebound through an f-string so the static type is a plain str (the dynamic
# ``from conftest import`` otherwise confuses the type checker into treating the name
# as the conftest module -- same workaround ``suites/agent_integration`` and
# ``suites/shadowing`` use).
TMUX_SESSION = f"{_ROOT_TMUX_SESSION}-onboarding"

# Scratch directory for the fake binary. A fixed, suite-specific path (mirrors
# ``suites/agent_integration``'s ``_FAKE_SCRIPT_PATH`` convention) rather than a
# generated temp name, so a leftover from a crashed run is easy to spot and clean up
# by hand if it ever comes to that.
FAKE_BIN_DIR = "/tmp/e2e-onboarding-fake-agent-bin"
FAKE_BIN_PATH = f"{FAKE_BIN_DIR}/amplifier-agent"

# Sentinel secret. Deliberately distinctive (not a plausible real key prefix used
# anywhere else in this tool's own output or click's formatting) so its appearance on
# screen can only be explained by this test's own leak, never an accidental match.
SENTINEL = "sk-e2e-SENTINEL-DO-NOT-LEAK-abc123"

# Version the fake agent reports for ``--version``. Comfortably above
# ``prereqs.MIN_AGENT_VERSION`` (0.14.0 as of this writing) so the preflight's
# version-floor check passes without this suite needing to track that constant.
FAKE_AGENT_VERSION = "99.0.0"

_TEMPLATE_PATH = Path(__file__).parent / "fixtures" / "fake_amplifier_agent.py.tmpl"


@pytest.fixture(scope="module")
def onboarding_driver(dtu_id: str) -> Generator[TmuxTuiDriver, None, None]:
    """Push the fake ``amplifier-agent`` binary into a scratch dir and yield a driver.

    Module-scoped: writing the fake binary is the expensive part and this suite's tests
    only ever spawn/inspect their own tmux session through the returned driver, so one
    instance safely serves the whole module.
    """
    exec_prefix = ["amplifier-digital-twin", "exec", dtu_id, "--"]
    driver = TmuxTuiDriver(TMUX_SESSION, exec_prefix=exec_prefix)

    content = _TEMPLATE_PATH.read_text(encoding="utf-8").replace("__VERSION__", FAKE_AGENT_VERSION)
    driver.run_command(
        [
            "bash",
            "-lc",
            f"rm -rf {shlex.quote(FAKE_BIN_DIR)} && mkdir -p {shlex.quote(FAKE_BIN_DIR)}",
        ]
    )
    with tempfile.NamedTemporaryFile(
        "w", suffix=".py", prefix="fake-amplifier-agent-", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(content)
        local_path = handle.name

    # dtu.file_push shells out to `amplifier-digital-twin file-push`; imported locally
    # (rather than at module scope) mirrors suites/shadowing's usage of `framework.dtu`
    # only from within the fixture that needs it.
    from framework import dtu

    dtu.file_push(dtu_id, local_path, FAKE_BIN_PATH)
    chmod = driver.run_command(["bash", "-lc", f"chmod +x {shlex.quote(FAKE_BIN_PATH)}"])
    assert chmod.returncode == 0, f"failed to chmod +x the fake agent binary:\n{chmod.stdout}"

    try:
        yield driver
    finally:
        # Kill any tmux session this suite's tests spawned (best-effort; a session that
        # was already closed by the test itself is a harmless no-op here), then remove
        # the scratch directory so nothing is left behind in the shared container.
        driver.close()
        driver.run_command(["bash", "-lc", f"rm -rf {shlex.quote(FAKE_BIN_DIR)}"])
