"""Ground-truth assertions run inside the same target the TUI lives in.

These shell into the DTU (or the local host, in local-driver mode) via the driver's
``run_command`` so they observe the exact filesystem the opencode/amplifier-agent
process wrote to. Two signals, both proven by hand:

* ``agent_log_contains`` -- the amplifier-agent server log at ``/tmp/amplifier-agent.log``
  records inbound ``POST /v1/chat/completions`` lines when a turn round-trips.
* ``session_events_exist`` -- context-intelligence writes ``events.jsonl`` under
  ``~/.amplifier-agent/state/workspaces/<workspace>/sessions/http-<ses-id>/``.
"""

from __future__ import annotations

import shlex

from .driver import TmuxTuiDriver

AGENT_LOG_PATH = "/tmp/amplifier-agent.log"


def agent_log_contains(driver: TmuxTuiDriver, pattern: str) -> bool:
    """True if the amplifier-agent server log contains ``pattern`` (fixed string)."""
    cmd = f"grep -F -- {shlex.quote(pattern)} {shlex.quote(AGENT_LOG_PATH)}"
    proc = driver.run_command(["bash", "-lc", cmd])
    return proc.returncode == 0


def session_events_exist(driver: TmuxTuiDriver, workspace: str = "opencode") -> bool:
    """True if at least one session ``events.jsonl`` exists for ``workspace``."""
    sessions = f'"$HOME"/.amplifier-agent/state/workspaces/{shlex.quote(workspace)}/sessions'
    cmd = f"find {sessions} -name events.jsonl -type f 2>/dev/null | head -1"
    proc = driver.run_command(["bash", "-lc", cmd])
    return bool(proc.stdout.strip())
