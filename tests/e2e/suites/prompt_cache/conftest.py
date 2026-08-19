"""Plumbing for the prompt-cache suite: server control, a raw HTTP poster, and an in-DTU
reader for the outbound Anthropic payload.

Mirrors ``suites/agent_integration/conftest.py`` -- same constants, same helpers, same
module-scoped ``driver``. Nothing is seeded on disk: this suite's input is an HTTP request
body, not a skill or a mode file.

Two additions on top of that baseline. ``post_chat_completion`` writes the JSON body to a
file inside the DTU and ``curl``s it, keeping a large nested payload away from two layers
of shell quoting. ``cache_control_verdict`` reports what the provider actually put ON THE
WIRE, which the HTTP response can only reveal indirectly -- and only when Anthropic
rejects it. That needs raw payload capture, which needs a ``host_config.json``, which is
why ``agent_server`` restarts the agent instead of reusing the warm one.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import time
import warnings
from collections.abc import Callable, Iterator
from typing import Any, NamedTuple

import pytest

# The in-DTU project dir and tmux session name, from the e2e root conftest. Rebound
# through an f-string so the static type is a plain str: the dynamic import otherwise
# confuses the type checker (same workaround the other DTU suites use).
from conftest import PROJECT_DIR as _PROJECT_DIR
from conftest import TMUX_SESSION as _TMUX_SESSION
from framework.driver import TmuxTuiDriver

PROJECT_DIR = f"{_PROJECT_DIR}"
TMUX_SESSION = f"{_TMUX_SESSION}"

# The address this tool defaults to. Driven rather than overridden, so "the agent" means
# the same process throughout.
BASE_URL = "http://127.0.0.1:9099/v1"
API_KEY = "local-dev-secret"

MODELS_URL = f"{BASE_URL}/models"
CHAT_COMPLETIONS_URL = f"{BASE_URL}/chat/completions"

# Fixed in-DTU scratch paths, not tempfiles: the suite makes one request, and a stable
# path is what a human debugging a failure wants to be able to ``cat`` afterwards.
REQUEST_BODY_PATH = "/tmp/e2e-prompt-cache-request.json"
RESPONSE_BODY_PATH = "/tmp/e2e-prompt-cache-response.json"

# ``provider`` is deliberately omitted: amplifier-agent auto-enables providers from
# resolvable credentials when no provider registry is declared, so this server serves
# exactly what the warm one does. Naming one here is the only thing that could change
# that. ``rawLlmPayloads`` must be a real JSON boolean -- the loader rejects the string
# "true" rather than coercing it.
HOST_CONFIG_PATH = "/tmp/e2e-prompt-cache-host-config.json"
HOST_CONFIG: dict[str, Any] = {"debug": {"rawLlmPayloads": True}}

# Where the capture lands. One directory per session, and this suite sends no
# ``X-Session-Id``, so the newest file by mtime is this test's. Expanded on the DTU side.
EVENTS_GLOB = (
    "~/.amplifier-agent/state/workspaces/opencode/sessions/*/context-intelligence/events.jsonl"
)

# Prefix the extractor stamps on its one line of output, so the verdict survives any
# banner noise the login shell prepends.
VERDICT_MARKER = "PROMPT_CACHE_VERDICT:"

# The extractor. A plain (non-f) string so its braces need no escaping; ``MARKER`` and
# ``EVENTS_GLOB`` are prepended as literals at call time. The walk is RECURSIVE because a
# marker can legitimately sit on a nested block, and one missed by the walk would read as
# an anti-vacuity failure rather than as the bug it is.
_VERDICT_SCRIPT = '''
import glob
import json
import os


def blocks_with_cache_control(node, trail):
    """Yield (trail, block) for every dict under ``node`` carrying cache_control."""
    if isinstance(node, dict):
        if "cache_control" in node:
            yield trail, node
        for key, value in node.items():
            yield from blocks_with_cache_control(value, trail + [key])
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from blocks_with_cache_control(value, trail + [index])


verdict = {
    "events_file": "",
    "requests_with_raw": 0,
    "cache_control_count": 0,
    "empty_stamped": [],
}
files = glob.glob(os.path.expanduser(EVENTS_GLOB))
if files:
    path = max(files, key=os.path.getmtime)
    verdict["events_file"] = path
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict) or event.get("event") != "llm:request":
                continue
            data = event.get("data")
            raw = data.get("raw") if isinstance(data, dict) else None
            messages = raw.get("messages") if isinstance(raw, dict) else None
            if not isinstance(messages, list):
                continue
            verdict["requests_with_raw"] += 1
            for trail, block in blocks_with_cache_control(messages, []):
                verdict["cache_control_count"] += 1
                if block.get("type") != "text":
                    continue
                text = block.get("text")
                text = text if isinstance(text, str) else ""
                if not text.strip():
                    verdict["empty_stamped"].append({"path": trail, "text": text[:40]})
print(MARKER + json.dumps(verdict))
'''

# A full agentic turn against a real model. Generous, because a timeout here would be an
# environment problem and must not be mistaken for the bug.
REQUEST_TIMEOUT_SECONDS = 240


class HttpResult(NamedTuple):
    """One ``curl`` round trip: transport outcome, HTTP status, and the raw body."""

    curl_returncode: int
    """Non-zero means the request never completed -- an environment fault, not a
    server response."""

    status: str
    """The HTTP status as ``curl -w '%{http_code}'`` reported it; ``"000"`` if none."""

    body: str
    """The response body verbatim, never pre-parsed: a failure message that quotes
    exactly what came back is the whole point."""


def _stop_agent_server(driver: TmuxTuiDriver, *, timeout: float = 30.0) -> None:
    """Kill any running amplifier-agent and WAIT for the process to go away.

    Waiting closes the race where a liveness probe still succeeds against a process on its
    way down. The ``[ ]`` bracket regex prevents ``pkill`` from matching its own command.
    """
    driver.run_command(["bash", "-lc", 'pkill -f "amplifier-agent[ ]serve" || true'])
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        proc = driver.run_command(["bash", "-lc", 'pgrep -f "amplifier-agent[ ]serve" || true'])
        if not proc.stdout.strip():
            return
        time.sleep(1.0)
    raise RuntimeError(f"amplifier-agent still running after {timeout}s; refusing to continue")


def _run_prepare(
    driver: TmuxTuiDriver, project_dir: str, *, extra_args: str = ""
) -> subprocess.CompletedProcess[str]:
    """Run ``amplifier-opencode --yes prepare`` (plus any ``extra_args``) and return it.

    ``--yes`` keeps the preflight non-interactive (there is no tty here); ``2>&1`` folds
    stderr in so a failure is visible in the same captured text.
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


def _wait_until(predicate: Callable[[], bool], *, timeout: float, poll: float = 1.0) -> bool:
    """Poll ``predicate()`` until it returns truthy or ``timeout`` elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(poll)
    return predicate()


def served_model_ids(driver: TmuxTuiDriver) -> list[str]:
    """Return the model ids advertised by ``GET /v1/models`` (empty on any failure)."""
    proc = driver.run_command(
        [
            "bash",
            "-lc",
            f"curl -s --max-time 10 -H {shlex.quote('Authorization: Bearer ' + API_KEY)} "
            f"{shlex.quote(MODELS_URL)}",
        ]
    )
    try:
        data = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, dict):
        return []
    entries = data.get("data")
    if not isinstance(entries, list):
        return []
    return [str(e["id"]) for e in entries if isinstance(e, dict) and e.get("id")]


def post_chat_completion(driver: TmuxTuiDriver, payload: dict[str, Any]) -> HttpResult:
    """POST ``payload`` to ``/v1/chat/completions`` inside the DTU and return the result.

    The body goes through a quoted heredoc and ``--data-binary @<path>``, so no part of the
    JSON is ever subject to shell quoting. Status and body are captured separately because
    the bug can surface as an error status OR as a 200 carrying the rejection inline.

    NOTE: no ``X-Session-Id`` header is sent, deliberately. That header puts the request on
    the session-resume path, which runs transcript repair over the incoming messages and
    persists them -- making the outcome depend on state left by a previous run. Without it
    the crafted history is seeded verbatim.
    """
    body = json.dumps(payload)
    write = driver.run_command(
        ["bash", "-lc", f"cat > {REQUEST_BODY_PATH} <<'JSONEOF'\n{body}\nJSONEOF"]
    )
    if write.returncode != 0:
        raise RuntimeError(f"failed to write the request body inside the DTU:\n{write.stdout}")

    proc = driver.run_command(
        [
            "bash",
            "-lc",
            f"curl -sS --max-time {REQUEST_TIMEOUT_SECONDS} -X POST "
            f"-H {shlex.quote('Authorization: Bearer ' + API_KEY)} "
            f"-H {shlex.quote('Content-Type: application/json')} "
            f"--data-binary @{REQUEST_BODY_PATH} "
            f"-o {RESPONSE_BODY_PATH} -w '%{{http_code}}' "
            f"{shlex.quote(CHAT_COMPLETIONS_URL)}",
        ]
    )
    read = driver.run_command(["bash", "-lc", f"cat {RESPONSE_BODY_PATH} 2>/dev/null"])
    return HttpResult(
        curl_returncode=proc.returncode,
        status=proc.stdout.strip() or "000",
        body=read.stdout,
    )


def cache_control_verdict(driver: TmuxTuiDriver) -> dict[str, Any]:
    """Report what ``cache_control`` markers the last outbound request carried.

    Returns the parsed verdict: ``events_file``, ``requests_with_raw``,
    ``cache_control_count``, and ``empty_stamped`` (one entry per marker on an
    empty/whitespace-only text block).

    The captured event lines are LARGE -- full message list, system prompt, tool schemas --
    so they never leave the twin. The extractor runs inside the DTU and only its one-line
    verdict crosses back.
    """
    script = f"MARKER = {VERDICT_MARKER!r}\nEVENTS_GLOB = {EVENTS_GLOB!r}\n{_VERDICT_SCRIPT}"
    proc = driver.run_command(["bash", "-lc", f"python3 - <<'PYEOF'\n{script}\nPYEOF"])
    for line in proc.stdout.splitlines():
        if line.startswith(VERDICT_MARKER):
            parsed = json.loads(line[len(VERDICT_MARKER) :])
            if not isinstance(parsed, dict):
                raise RuntimeError(f"in-DTU payload extractor emitted a non-object verdict: {line}")
            return parsed
    raise RuntimeError(
        "the in-DTU payload extractor produced no verdict line; without it the test "
        "cannot tell a real pass from a vacuous one.\n"
        f"exit={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


@pytest.fixture(scope="module")
def driver(dtu_id: str) -> TmuxTuiDriver:
    """A driver bound to the DTU, with the shared project dir present.

    Module-scoped: this suite only ever runs plain commands through it, never a
    tmux-hosted TUI, so one instance is safely reused.
    """
    tmux_session = f"{TMUX_SESSION}"
    project_dir = f"{PROJECT_DIR}"
    exec_prefix = ["amplifier-digital-twin", "exec", dtu_id, "--"]
    d = TmuxTuiDriver(tmux_session, exec_prefix=exec_prefix)
    d.run_command(["bash", "-lc", f"mkdir -p {shlex.quote(project_dir)}"])
    return d


@pytest.fixture(scope="module")
def agent_server(driver: TmuxTuiDriver) -> Iterator[TmuxTuiDriver]:
    """Run this suite against an amplifier-agent started with raw payload capture on.

    A warm server cannot be reused: the payload only reaches disk when the server was
    STARTED with ``debug.rawLlmPayloads``, and ``--host-config`` is only read by the
    process that starts it. So this stops whatever is running and starts its own.

    Teardown restores a default server, and that is what lets the suite stay ``dtu``
    rather than ``fresh_dtu``. The debug flag writes every prompt and tool result to disk
    unredacted, and other suites share this twin. Restoration is best effort on purpose:
    raising from teardown would replace whatever the test found with a fixture error.
    """
    _stop_agent_server(driver)

    body = json.dumps(HOST_CONFIG)
    write = driver.run_command(
        ["bash", "-lc", f"cat > {HOST_CONFIG_PATH} <<'JSONEOF'\n{body}\nJSONEOF"]
    )
    assert write.returncode == 0, (
        f"could not write the host_config.json in the DTU:\n{write.stdout}"
    )

    proc = _run_prepare(
        driver, PROJECT_DIR, extra_args=f"--host-config {shlex.quote(HOST_CONFIG_PATH)}"
    )
    assert proc.returncode == 0, (
        f"could not start amplifier-agent with raw payload capture enabled:\n{proc.stdout}"
    )
    assert _wait_until(lambda: bool(served_model_ids(driver)), timeout=60.0), (
        "amplifier-agent never advertised any model at GET /v1/models; the suite cannot "
        "distinguish the regression under test from a server that is not serving"
    )

    try:
        yield driver
    finally:
        try:
            _stop_agent_server(driver)
            restored = _run_prepare(driver, PROJECT_DIR)
            if restored.returncode != 0:
                warnings.warn(
                    "prompt_cache teardown: could not restart a default amplifier-agent; "
                    f"this DTU is left with no server:\n{restored.stdout}",
                    stacklevel=1,
                )
            elif not _wait_until(lambda: bool(served_model_ids(driver)), timeout=60.0):
                warnings.warn(
                    "prompt_cache teardown: the restored amplifier-agent never advertised "
                    "a model; later suites sharing this DTU may fail their preconditions.",
                    stacklevel=1,
                )
        except Exception as exc:  # deliberately broad -- see the fixture docstring
            warnings.warn(
                f"prompt_cache teardown: failed to restore the default agent: {exc!r}",
                stacklevel=1,
            )
