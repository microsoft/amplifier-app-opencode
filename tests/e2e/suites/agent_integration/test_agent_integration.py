"""DTU-backed tests for this tool's amplifier-agent lifecycle contract
(``docs/spec/agent-integration.md``).

Ground-truth assertions only -- no TUI, no AI judge. Every assertion is either a process
fact gathered via ``pgrep``/``curl`` inside the DTU, or captured CLI stdout, in the same
spirit as ``suites/shadowing``, ``suites/traversal``, and ``suites/bridge``.

Each test is SELF-SUFFICIENT: it establishes its own precondition (server up or down)
rather than relying on what an earlier test left behind, so the suite is not sensitive to
collection order.
"""

from __future__ import annotations

import json
import re
import shlex
import time

import pytest
from framework.driver import TmuxTuiDriver
from suites.agent_integration.conftest import (
    API_KEY,
    BASE_URL,
    PROJECT_DIR,
    _agent_pid,
    _run_prepare,
    _stop_agent_server,
)

# ``dtu`` only -- deliberately NOT ``fresh_dtu``. This suite drives the amplifier-agent
# process directly (stopping/starting it itself) rather than depending on discovery
# fixed at a prior server's startup, so it needs no launch-time env overrides and can
# reuse the WARM DTU (same trade as the other non-TUI suites).
pytestmark = pytest.mark.dtu

_MODELS_URL = f"{BASE_URL}/models"


def _models_response(driver: TmuxTuiDriver) -> dict | None:
    """Curl ``/v1/models`` and return the parsed JSON body, or ``None`` on any failure."""
    proc = driver.run_command(
        [
            "bash",
            "-lc",
            f"curl -s --max-time 5 -H {shlex.quote('Authorization: Bearer ' + API_KEY)} "
            f"{shlex.quote(_MODELS_URL)}",
        ]
    )
    try:
        data = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _wait_until(predicate, *, timeout: float, poll: float = 1.0) -> bool:
    """Poll ``predicate()`` until it returns truthy or ``timeout`` elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(poll)
    return predicate()


def test_prepare_starts_the_agent_when_none_is_running(driver: TmuxTuiDriver) -> None:
    """With no server reachable, ``prepare`` spawns one and it becomes reachable.

    Per the Lifecycle section: when no server answers the liveness probe and starting one
    is not disabled, this tool spawns the agent's chat-completions server directly, then
    polls for readiness. This test establishes "no server" itself (self-sufficient), then
    proves both the exit code and the resulting reachability of ``/v1/models``.
    """
    _stop_agent_server(driver)
    down = driver.run_command(["bash", "-lc", 'pgrep -f "amplifier-agent[ ]serve" || true'])
    assert not down.stdout.strip(), (
        f"expected amplifier-agent to be down before this test, still saw pid(s): {down.stdout!r}"
    )

    proc = _run_prepare(driver, PROJECT_DIR)
    assert proc.returncode == 0, f"prepare failed to start amplifier-agent:\n{proc.stdout}"

    reachable = _wait_until(lambda: _models_response(driver) is not None, timeout=30.0)
    body = _models_response(driver)
    assert reachable and body is not None, (
        f"expected /v1/models to become reachable after prepare started the agent; "
        f"prepare output:\n{proc.stdout}"
    )


def test_prepare_reuses_an_already_running_agent(driver: TmuxTuiDriver) -> None:
    """With a server already reachable, ``prepare`` reuses it rather than restarting it.

    Per the Reuse section: "Before considering a spawn, this tool checks whether a server
    is already reachable ... If that probe succeeds, the existing server is reused as-is
    and nothing is spawned." This test first guarantees a running server itself (so it
    does not depend on a previous test having started one), captures its PID, reruns
    ``prepare``, and asserts the PID is unchanged.
    """
    if not _wait_until(lambda: bool(_agent_pid(driver)), timeout=1.0, poll=0.5):
        first = _run_prepare(driver, PROJECT_DIR)
        assert first.returncode == 0, f"could not establish a running agent:\n{first.stdout}"
        assert _wait_until(lambda: bool(_agent_pid(driver)), timeout=30.0), (
            "amplifier-agent did not report a PID after prepare started it"
        )

    pid_before = _agent_pid(driver)
    assert pid_before, "expected a running amplifier-agent PID before the reuse check"

    proc = _run_prepare(driver, PROJECT_DIR)
    assert proc.returncode == 0, f"prepare failed against an already-running agent:\n{proc.stdout}"

    pid_after = _agent_pid(driver)
    assert pid_after == pid_before, (
        f"expected the amplifier-agent PID to be unchanged (reuse, not restart); "
        f"before={pid_before!r} after={pid_after!r}\nprepare output:\n{proc.stdout}"
    )


def test_no_start_refuses_when_the_agent_is_down(driver: TmuxTuiDriver) -> None:
    """``--no-start`` must fail loudly, rather than silently spawn, when nothing is up.

    Per the Lifecycle section's ``--no-start`` behavior: the tool raises a
    ``click.ClickException`` naming the base URL and stating ``--no-start`` was passed,
    rather than spawning the server it would otherwise start. This test stops the server
    itself first (self-sufficient), then restores it afterward so later tests are
    unaffected by this test's deliberate down-state.
    """
    _stop_agent_server(driver)
    try:
        proc = driver.run_command(
            [
                "bash",
                "-lc",
                f"cd {shlex.quote(PROJECT_DIR)} && "
                "amplifier-opencode --no-bootstrap prepare --no-start "
                f"--project-dir {shlex.quote(PROJECT_DIR)} 2>&1",
            ]
        )
        assert proc.returncode != 0, (
            f"expected a non-zero exit when --no-start is passed and the agent is down; "
            f"output:\n{proc.stdout}"
        )
        assert "is NOT running at" in proc.stdout, (
            f"expected the documented refusal text naming the agent as not running; "
            f"output:\n{proc.stdout}"
        )
        assert "--no-start" in proc.stdout and "was passed" in proc.stdout, (
            f"expected the refusal to name --no-start as the reason spawning was skipped; "
            f"output:\n{proc.stdout}"
        )
    finally:
        # Restore a running agent so later tests are not affected by this test's
        # deliberate down-state.
        restart = _run_prepare(driver, PROJECT_DIR)
        assert restart.returncode == 0, (
            f"failed to restart amplifier-agent after the --no-start refusal test:\n"
            f"{restart.stdout}"
        )


def test_doctor_reports_the_agent_version_against_the_floor(driver: TmuxTuiDriver) -> None:
    """``doctor``'s reported agent version must meet the floor ``--version`` declares.

    Per The version floor section, there is a single declared minimum amplifier-agent
    version. This test reads the installed version off ``doctor``'s output and the
    declared minimum off ``--version``'s output independently, then compares them as
    tuples of ints -- never as strings -- so a floor like ``0.9.3`` is not mistakenly
    read as greater than ``0.10.0``.
    """
    doctor_proc = driver.run_command(["bash", "-lc", "amplifier-opencode doctor 2>&1"])
    agent_lines = [
        line for line in doctor_proc.stdout.splitlines() if "amplifier-agent" in line.lower()
    ]
    assert agent_lines, f"expected an amplifier-agent line in doctor output:\n{doctor_proc.stdout}"

    installed: tuple[int, int, int] | None = None
    for line in agent_lines:
        match = re.search(r"(\d+)\.(\d+)\.(\d+)", line)
        if match:
            installed = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
            break
    assert installed is not None, (
        f"expected the amplifier-agent doctor line to report a dotted version, got:\n"
        f"{agent_lines!r}\nfull doctor output:\n{doctor_proc.stdout}"
    )

    version_proc = driver.run_command(["bash", "-lc", "amplifier-opencode --version 2>&1"])
    minimum_match = re.search(r"minimum required (\d+)\.(\d+)\.(\d+)", version_proc.stdout)
    assert minimum_match, (
        f"expected 'minimum required X.Y.Z' in --version output, got:\n{version_proc.stdout}"
    )
    minimum = (
        int(minimum_match.group(1)),
        int(minimum_match.group(2)),
        int(minimum_match.group(3)),
    )

    assert installed >= minimum, (
        f"installed amplifier-agent {installed} is below the declared minimum {minimum}\n"
        f"doctor output:\n{doctor_proc.stdout}\n--version output:\n{version_proc.stdout}"
    )


# --- test 5: skills-endpoint degradation ------------------------------------------ #

_FAKE_SERVER_TMUX = "oc-e2e-fake-agent"
_FAKE_SERVER_PORT = 9199
_FAKE_PROJECT_DIR = "/root/oc-e2e-skills-failure"
_FAKE_SCRIPT_PATH = "/tmp/e2e-fake-agent-server.py"
_FAKE_SERVER_LOG = "/tmp/e2e-fake-agent-server.log"

# A minimal HTTP server that answers `/v1/models` (the one REQUIRED surface) with a
# valid, non-empty model list, and 404s everything else -- including `/v1/skills` and
# `/v1/modes` -- so those two best-effort surfaces fail exactly the way a real
# unreachable/non-200 response would, without touching the real amplifier-agent at all.
_FAKE_SERVER_SCRIPT = """\
import http.server
import json


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/v1/models"):
            body = json.dumps({"data": [{"id": "fake-e2e-model"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


http.server.HTTPServer(("127.0.0.1", 9199), Handler).serve_forever()
"""


def test_skills_endpoint_failure_does_not_block_prepare(driver: TmuxTuiDriver) -> None:
    """A failing skills/modes listing must never block ``prepare`` from succeeding.

    Per the Degradation section: only the model listing is required; skills and modes
    listings are best-effort, and a fetch failure "yields an empty result SILENTLY ...
    the bridge proceeds as though the server had reported zero skills/modes." This test
    points ``prepare`` at a minimal fake HTTP server that answers ``/v1/models`` (so the
    one REQUIRED surface succeeds) but 404s ``/v1/skills`` and ``/v1/modes`` -- exactly
    the best-effort failure the spec describes -- without touching the real
    amplifier-agent process at all, so no other test in this suite is affected.

    If ``python3`` is not available inside the DTU this scenario cannot be driven
    end-to-end; the test then skips loudly rather than fake a pass.
    """
    has_python = driver.run_command(["bash", "-lc", "command -v python3"])
    if has_python.returncode != 0:
        pytest.skip(
            "python3 not found inside the DTU; cannot host the fake agent server needed "
            "to drive the skills-endpoint-failure scenario end-to-end"
        )

    fake_base_url = f"http://127.0.0.1:{_FAKE_SERVER_PORT}/v1"
    driver.run_command(
        ["bash", "-lc", f"tmux kill-session -t {_FAKE_SERVER_TMUX} 2>/dev/null || true"]
    )
    driver.run_command(
        [
            "bash",
            "-lc",
            f"rm -rf {shlex.quote(_FAKE_PROJECT_DIR)} && mkdir -p {shlex.quote(_FAKE_PROJECT_DIR)}",
        ]
    )
    write_script = driver.run_command(
        ["bash", "-lc", f"cat > {_FAKE_SCRIPT_PATH} <<'PYEOF'\n{_FAKE_SERVER_SCRIPT}PYEOF"]
    )
    assert write_script.returncode == 0, (
        f"failed to write the fake agent server script:\n{write_script.stdout}"
    )

    try:
        driver.run_command(
            [
                "bash",
                "-lc",
                f"tmux new-session -d -s {_FAKE_SERVER_TMUX} "
                f"'python3 {_FAKE_SCRIPT_PATH} > {_FAKE_SERVER_LOG} 2>&1'",
            ]
        )

        def _fake_models_up() -> bool:
            probe = driver.run_command(
                [
                    "bash",
                    "-lc",
                    "curl -s -o /dev/null -w '%{http_code}' --max-time 2 "
                    f"{shlex.quote(fake_base_url + '/models')}",
                ]
            )
            return probe.stdout.strip() == "200"

        assert _wait_until(_fake_models_up, timeout=20.0, poll=1.0), (
            f"fake agent server on port {_FAKE_SERVER_PORT} never became reachable; log:\n"
            + driver.run_command(["bash", "-lc", f"cat {_FAKE_SERVER_LOG} 2>/dev/null"]).stdout
        )

        proc = driver.run_command(
            [
                "bash",
                "-lc",
                f"cd {shlex.quote(_FAKE_PROJECT_DIR)} && "
                f"amplifier-opencode --no-bootstrap --base-url {shlex.quote(fake_base_url)} "
                f"--api-key e2e-fake-key prepare --project-dir {shlex.quote(_FAKE_PROJECT_DIR)} "
                "2>&1",
            ]
        )
        assert proc.returncode == 0, (
            f"prepare must succeed even though /v1/skills and /v1/modes 404 on the fake "
            f"server; output:\n{proc.stdout}"
        )
        assert "skills bridge failed" not in proc.stdout, (
            f"a fetch failure (404) must be silent, not a write/reconcile-failure warning; "
            f"output:\n{proc.stdout}"
        )
        assert "modes bridge failed" not in proc.stdout, (
            f"a fetch failure (404) must be silent, not a write/reconcile-failure warning; "
            f"output:\n{proc.stdout}"
        )
    finally:
        driver.run_command(
            ["bash", "-lc", f"tmux kill-session -t {_FAKE_SERVER_TMUX} 2>/dev/null || true"]
        )
        driver.run_command(["bash", "-lc", f"rm -rf {shlex.quote(_FAKE_PROJECT_DIR)}"])
