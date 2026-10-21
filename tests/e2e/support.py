"""Driving opencode in the twin and reading what Amplifier kept."""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import time

from digital_twin_universe import lib
from digital_twin_universe.schemas import ExecResult
import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
PROFILE = REPOSITORY / ".agents" / "digital-twin-universe" / "opencode-amplifier"
HOME = "/home/user"
LAUNCH_TIMEOUT = 900
TURN_TIMEOUT = 300


def current_branch() -> str:
    ref = (
        os.environ.get("E2E_REF")
        or subprocess.run(
            ["git", "-C", str(REPOSITORY), "branch", "--show-current"], capture_output=True, text=True, check=True
        ).stdout.strip()
    )
    if not ref:
        raise pytest.UsageError("The checkout is not on a branch. Check out a branch or set E2E_REF.")
    return ref


def session_header(session_id: str) -> str:
    """The Amplifier session id the plugin derives from an opencode session id."""
    return f"oc-{hashlib.sha256(session_id.encode()).hexdigest()[:40]}"


def project_slug(directory: str) -> str:
    return directory.replace("/", "-")


@dataclass(frozen=True)
class Reply:
    session_id: str
    text: str
    result: ExecResult


@dataclass(frozen=True)
class Twin:
    id: str

    def run(self, command: str, workdir: str | None = None, timeout: int = TURN_TIMEOUT) -> ExecResult:
        return lib.execute(self.id, command, workdir=workdir, timeout_seconds=timeout)

    def check(self, command: str, workdir: str | None = None, timeout: int = TURN_TIMEOUT) -> str:
        result = self.run(command, workdir, timeout)
        assert result.exit_code == 0, f"{command} exited {result.exit_code}\n{result.stdout}\n{result.stderr}"
        return result.stdout

    def read(self, path: str) -> str:
        return self.check(f"cat {shlex.quote(path)}")

    def exists(self, path: str) -> bool:
        return self.run(f"test -e {shlex.quote(path)}").exit_code == 0

    def project(self, name: str) -> str:
        """A new git repository under the user's home, as a person would start a project."""
        directory = f"{HOME}/{name}"
        self.check(f"rm -rf {directory} && mkdir -p {directory} && git init -q {directory}")
        return directory

    def opencode_run(self, directory: str, prompt: str, *options: str) -> Reply:
        """One `opencode run` turn. Each word is its own argument, which opencode joins without quoting."""
        words = " ".join(shlex.quote(word) for word in prompt.split())
        flags = " ".join(shlex.quote(option) for option in options)
        result = self.run(f"opencode run --format json {flags} {words}", workdir=directory)
        assert result.exit_code == 0, f"opencode run exited {result.exit_code}\n{result.stdout}\n{result.stderr}"
        events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
        sessions = {event["sessionID"] for event in events if "sessionID" in event}
        assert len(sessions) == 1, f"expected one session in the output:\n{result.stdout}\n{result.stderr}"
        text = "".join(event["part"]["text"] for event in events if event.get("type") == "text")
        return Reply(sessions.pop(), text, result)

    def export(self, session_id: str) -> dict:
        return json.loads(self.check(f"opencode session export {shlex.quote(session_id)}"))

    def pull_sessions(self, directory: str, destination: Path) -> Path:
        """A copy of the Amplifier sessions the face keeps for `directory`, under `destination`."""
        source = f"{HOME}/.amplifier-agent/projects/{project_slug(directory)}/sessions"
        target = destination / "sessions"
        lib.pull_files(self.id, source, target)
        return target


def durable_sessions(sessions: Path) -> dict[str, dict]:
    """metadata.json of each durable Amplifier session in a pulled sessions directory, by session id."""
    found = {
        path.parent.name: json.loads(path.read_text(encoding="utf-8")) for path in sessions.glob("*/metadata.json")
    }
    return {name: metadata for name, metadata in found.items() if metadata.get("persistence") == "durable"}


def turns(sessions: Path, session: str) -> list[dict]:
    """Turn records of a pulled Amplifier session, as `{input, result}` mappings."""
    lines = (sessions / session / "turns.jsonl").read_text(encoding="utf-8").splitlines()
    records = [json.loads(line)["turn"]["fields"]["mapping"] for line in lines if line.strip()]
    return [
        {
            "input": record["input"]["fields"]["mapping"],
            "result": record["result"]["fields"]["mapping"] if record.get("result") else None,
        }
        for record in records
    ]


def word(text: str) -> str:
    return text.strip().strip(".!\"'`*").lower()


def wait_for(predicate, timeout: float, interval: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()
