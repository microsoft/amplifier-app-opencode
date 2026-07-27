"""Fixtures that SEED uniquely-named probe modes into amplifier mode dirs in the DTU.

Each probe is a single flat ``<name>.md`` file with a top-level ``mode:`` frontmatter block
-- the exact on-disk layout amplifier-agent's mode discovery expects (it globs ``*.md`` and
takes the file stem as the mode name; see amplifier-agent ``resources.list_modes`` +
hooks-mode ``ModeDiscovery``). Seeding happens BEFORE the opencode TUI/server launches (see
``modes_session``), so the modes exist when the launcher fetches ``GET /v1/modes`` at startup.

The probe forces a DETERMINISTIC sentinel (``MODE-PROBE-OK::<name>``) when active, so both
discovery (the ``<name> (Amplifier)`` agent in the "Select agent" dialog) and activation (the
sentinel in the reply) are verifiable via the AI judge alone -- no log or filesystem
inspection.

Seeded sources (bare mode name -> path in DTU; the launcher adds the `` (Amplifier)`` suffix
for the opencode agent identity, so ``e2e-proj`` -> agent ``e2e-proj (Amplifier)``):
    e2e-proj  -> {PROJECT_DIR}/.amplifier/modes/e2e-proj.md   (amplifier project mode)
    e2e-user  -> /root/.amplifier/modes/e2e-user.md           (amplifier user mode)
"""

from __future__ import annotations

import json
import shlex
import tempfile
import time
from collections.abc import Generator
from pathlib import Path

import pytest

# PROJECT_DIR is the in-DTU project dir the TUI launches from (``/root/oc-e2e``). It is
# defined in the e2e root conftest, which sits on sys.path, so it imports cleanly here.
from conftest import PROJECT_DIR
from framework import dtu

# The DTU installs the stack under the root user; ``$HOME`` is ``/root`` there.
DTU_HOME = "/root"

_TEMPLATE_PATH = Path(__file__).parent / "fixtures" / "probe_mode.md.tmpl"

# bare mode name -> dest ``<name>.md`` path inside the DTU (single flat file per mode).
_SEED_MAP: dict[str, tuple[str, str]] = {
    "amplifier_project": ("e2e-proj", f"{PROJECT_DIR}/.amplifier/modes/e2e-proj.md"),
    "amplifier_user": ("e2e-user", f"{DTU_HOME}/.amplifier/modes/e2e-user.md"),
}


def _render(name: str) -> str:
    """Render the probe template for ``name`` (substitute the ``{NAME}`` placeholder)."""
    return _TEMPLATE_PATH.read_text(encoding="utf-8").replace("{NAME}", name)


def _seed_mode(dtu_id: str, name: str, dest: str) -> None:
    """Write a rendered probe ``<name>.md`` at ``dest`` inside the DTU.

    Creates the parent dir first (belt-and-suspenders next to ``file_push``'s own parent
    creation) then pushes the rendered file across the DTU boundary.
    """
    parent = dest.rsplit("/", 1)[0]
    dtu.exec_json(dtu_id, ["bash", "-lc", f"mkdir -p {shlex.quote(parent)}"])
    with tempfile.NamedTemporaryFile(
        "w", suffix=".md", prefix="probe-mode-", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(_render(name))
        local_path = handle.name
    dtu.file_push(dtu_id, local_path, dest)


# amplifier-agent mode discovery (and the ``mode-<name>`` model aliases the launcher turns
# into ``<name> (Amplifier)`` agents) is captured ONCE, at server startup. Two independent
# hazards make a naive launch flaky:
#   1. STALENESS -- a warm DTU reuses a single amplifier-agent across suites, so a server that
#      started before this suite seeded its modes never sees them.
#   2. COLD START -- the very first launch on a freshly-provisioned DTU installs provider
#      modules (~1-2 min); if the launcher's readiness wait is out-raced, its ``GET /v1/modes``
#      fetch comes back empty and no ``<name> (Amplifier)`` agent files get written for the early
#      tests.
# The module-scoped ``_warm_modes_server`` fixture defeats both: it seeds the modes, stops any
# stale server, starts a FRESH one headlessly (``--no-launch``) so it discovers the just-seeded
# modes, then polls ``GET /v1/modes`` until every expected mode is present. Only then do the
# per-test ``opencode_session`` launches run -- against a warm, correct server.

# The api key the e2e launcher/serve uses inside the DTU (see the provisioning stack).
_AGENT_API_KEY = "local-dev-secret"
_AGENT_MODES_URL = "http://127.0.0.1:9099/v1/modes"
# Bare mode names every launch of this suite must surface (built-ins + both seeded probes).
_EXPECTED_MODES = {"plan", "brainstorm", "e2e-proj", "e2e-user"}
# tmux session the headless warmup launch runs under, so it survives the exec that starts it.
_WARM_TMUX = "oc-e2e-modes-warm"
# Cold provider install on a fresh DTU can be slow; give the warmup a generous ceiling.
_WARMUP_TIMEOUT_S = 300.0


def _discovered_modes(dtu_id: str) -> set[str]:
    """Return the bare mode names ``GET /v1/modes`` currently reports (empty on any error)."""
    res = dtu.exec_json(
        dtu_id,
        [
            "bash",
            "-lc",
            f"curl -s --max-time 5 -H {shlex.quote('Authorization: Bearer ' + _AGENT_API_KEY)} "
            f"{shlex.quote(_AGENT_MODES_URL)}",
        ],
    )
    try:
        data = json.loads(res.get("stdout", "") or "{}").get("data", [])
    except (json.JSONDecodeError, TypeError):
        return set()
    return {m["name"] for m in data if isinstance(m, dict) and isinstance(m.get("name"), str)}


@pytest.fixture(scope="module", autouse=True)
def _warm_modes_server(dtu_id: str) -> Generator[None, None, None]:
    """Seed the probe modes, then guarantee a warm amplifier-agent that has discovered them.

    Runs once per module, before any ``opencode_session`` is set up (module scope precedes the
    function-scoped session fixture). Steps:
      1. Seed each probe ``<name>.md`` into its amplifier mode dir.
      2. Stop any warm/stale server so the fresh one starts AFTER the seeds exist.
      3. Start a fresh server headlessly via ``amplifier-opencode launch --no-launch`` (under
         tmux so it outlives the exec), with cwd == PROJECT_DIR so the project mode is found.
      4. Poll ``GET /v1/modes`` until every expected mode is present (fail loud on timeout).
    """
    # Rebind PROJECT_DIR through an f-string so its static type is a plain str: the dynamic
    # ``from conftest import PROJECT_DIR`` confuses the type checker into treating it as the
    # conftest module (same idiom the skills suite uses).
    project_dir = f"{PROJECT_DIR}"

    for name, dest in _SEED_MAP.values():
        _seed_mode(dtu_id, name, dest)

    dtu.exec_json(
        dtu_id,
        [
            "bash",
            "-lc",
            "pkill -f 'amplifier-agent serve' 2>/dev/null || true; "
            "for _ in $(seq 1 40); do pgrep -f 'amplifier-agent serve' >/dev/null || break; "
            "sleep 0.5; done",
        ],
    )

    launch = (
        f"cd {shlex.quote(project_dir)} && "
        f"amplifier-opencode launch --project-dir {shlex.quote(project_dir)} --no-launch "
        "> /tmp/modes-warm.log 2>&1"
    )
    dtu.exec_json(
        dtu_id,
        [
            "bash",
            "-lc",
            f"tmux kill-session -t {_WARM_TMUX} 2>/dev/null || true; "
            f"tmux new-session -d -s {_WARM_TMUX} {shlex.quote(launch)}",
        ],
    )

    deadline = time.monotonic() + _WARMUP_TIMEOUT_S
    discovered: set[str] = set()
    ready = False
    while time.monotonic() < deadline:
        discovered = _discovered_modes(dtu_id)
        if _EXPECTED_MODES.issubset(discovered):
            ready = True
            break
        time.sleep(3.0)

    if not ready:
        log_tail = dtu.exec_json(
            dtu_id, ["bash", "-lc", "tail -60 /tmp/modes-warm.log 2>/dev/null"]
        ).get("stdout", "")
        missing = _EXPECTED_MODES - discovered
        raise RuntimeError(
            f"amplifier-agent did not report modes {sorted(missing)} within "
            f"{_WARMUP_TIMEOUT_S:.0f}s (saw {sorted(discovered)}).\n--- warmup log ---\n{log_tail}"
        )

    yield

    # Leave a clean slate for any later suite (e.g. skills) whose own launch must start a
    # server configured with ITS overrides: stop the modes-warmed server we started here.
    dtu.exec_json(
        dtu_id,
        [
            "bash",
            "-lc",
            f"tmux kill-session -t {_WARM_TMUX} 2>/dev/null || true; "
            "pkill -f 'amplifier-agent serve' 2>/dev/null || true",
        ],
    )


@pytest.fixture
def modes_session(_warm_modes_server: None, opencode_session):
    """The live TUI driver, launched against the pre-warmed, modes-aware amplifier-agent.

    ``_warm_modes_server`` (module scope, autouse) has already seeded the modes and warmed a
    fresh server that discovered them, so this per-test launch reuses that warm server and its
    ``fetch_modes``/agent-file bridge is fast and complete.
    """
    return opencode_session
