"""Tests for the skills bridge: /v1/skills -> opencode /command files.

Covers:
  - fetch_skills() parses the ``data`` list on 200 and returns [] on any
    error (network error, non-200, malformed JSON, wrong shape).
  - resolve_command_dir() returns the project-scope command dir when a
    project dir is given and the global command dir otherwise.
  - write_command_files() writes correct frontmatter + body per skill, with
    sidecar-manifest reconciliation:
      * stale generated files are pruned on a later run
      * a user's own (unowned) command file is never overwritten
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from amplifier_app_opencode.cli import (
    GLOBAL_OPENCODE_DIR,
    fetch_skills,
    resolve_command_dir,
    write_command_files,
)

# ---------------------------------------------------------------------------
# fetch_skills()
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, json_data=None, raise_json=False):
        self.status_code = status_code
        self._json_data = json_data
        self._raise_json = raise_json

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error",
                request=httpx.Request("GET", "http://x/v1/skills"),
                response=self,  # type: ignore[arg-type]
            )

    def json(self):
        if self._raise_json:
            raise ValueError("bad json")
        return self._json_data


def test_fetch_skills_parses_data_on_200(monkeypatch: pytest.MonkeyPatch) -> None:
    skills = [
        {"name": "code-review", "description": "Review changed code."},
        {"name": "council", "description": "Convene the panel."},
    ]
    monkeypatch.setattr(
        "amplifier_app_opencode.cli.httpx.get",
        lambda *a, **kw: _FakeResponse(json_data={"object": "list", "data": skills}),
    )
    assert fetch_skills("http://127.0.0.1:9099/v1", "key") == skills


def test_fetch_skills_returns_empty_on_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*a, **kw):
        raise httpx.ConnectError("no server")

    monkeypatch.setattr("amplifier_app_opencode.cli.httpx.get", _boom)
    assert fetch_skills("http://127.0.0.1:9099/v1", "key") == []


def test_fetch_skills_returns_empty_on_non_200(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "amplifier_app_opencode.cli.httpx.get",
        lambda *a, **kw: _FakeResponse(status_code=500, json_data={"data": []}),
    )
    assert fetch_skills("http://127.0.0.1:9099/v1", "key") == []


def test_fetch_skills_returns_empty_on_bad_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "amplifier_app_opencode.cli.httpx.get",
        lambda *a, **kw: _FakeResponse(raise_json=True),
    )
    assert fetch_skills("http://127.0.0.1:9099/v1", "key") == []


def test_fetch_skills_returns_empty_on_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # Body is a dict but has no ``data`` list.
    monkeypatch.setattr(
        "amplifier_app_opencode.cli.httpx.get",
        lambda *a, **kw: _FakeResponse(json_data={"object": "list"}),
    )
    assert fetch_skills("http://127.0.0.1:9099/v1", "key") == []


# ---------------------------------------------------------------------------
# resolve_command_dir()
# ---------------------------------------------------------------------------


def test_resolve_command_dir_project_scope(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    assert resolve_command_dir(project) == project / ".opencode" / "command"


def test_resolve_command_dir_global_scope() -> None:
    assert resolve_command_dir(None) == GLOBAL_OPENCODE_DIR / "command"


# ---------------------------------------------------------------------------
# write_command_files()
# ---------------------------------------------------------------------------


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _frontmatter_description(content: str) -> str:
    """Extract and JSON-decode the description value from the frontmatter."""
    lines = content.splitlines()
    assert lines[0] == "---"
    desc_line = next(line for line in lines if line.startswith("description:"))
    value = desc_line[len("description:") :].strip()
    # The value is serialized as a JSON string, which is also valid YAML.
    return json.loads(value)


def test_write_command_files_writes_frontmatter_and_body(tmp_path: Path) -> None:
    command_dir = tmp_path / ".opencode" / "command"
    skills = [
        {"name": "code-review", "description": 'Review changed code: colons "quotes" & more'},
        {"name": "council", "description": "Convene the panel."},
    ]
    write_command_files(skills, command_dir)

    cr = command_dir / "code-review.md"
    co = command_dir / "council.md"
    assert cr.exists()
    assert co.exists()

    cr_content = _read(cr)
    # Exact body line.
    assert cr_content.splitlines()[-1] == "!amplifier:skill code-review $ARGUMENTS"
    # Description present and round-trips (valid YAML/JSON, special chars safe).
    assert _frontmatter_description(cr_content) == 'Review changed code: colons "quotes" & more'

    co_content = _read(co)
    assert co_content.splitlines()[-1] == "!amplifier:skill council $ARGUMENTS"
    assert _frontmatter_description(co_content) == "Convene the panel."

    # Manifest records both generated files.
    manifest = command_dir.parent / ".amplifier-generated-commands.json"
    assert manifest.exists()
    data = json.loads(_read(manifest))
    assert set(data["commands"]) == {"code-review.md", "council.md"}


def test_write_command_files_prunes_stale_on_second_run(tmp_path: Path) -> None:
    command_dir = tmp_path / ".opencode" / "command"
    first = [
        {"name": "code-review", "description": "Review."},
        {"name": "council", "description": "Panel."},
    ]
    write_command_files(first, command_dir)
    assert (command_dir / "council.md").exists()

    # Second run: council is gone.
    second = [{"name": "code-review", "description": "Review."}]
    write_command_files(second, command_dir)

    assert (command_dir / "code-review.md").exists()
    assert not (command_dir / "council.md").exists()

    manifest = command_dir.parent / ".amplifier-generated-commands.json"
    data = json.loads(_read(manifest))
    assert data["commands"] == ["code-review.md"]


def test_write_command_files_leaves_unowned_file_untouched(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    command_dir = tmp_path / ".opencode" / "command"
    command_dir.mkdir(parents=True, exist_ok=True)

    # User's own command file that we did NOT generate (no manifest yet).
    user_file = command_dir / "code-review.md"
    original = "---\ndescription: my own command\n---\necho hello\n"
    user_file.write_text(original, encoding="utf-8")

    write_command_files([{"name": "code-review", "description": "Amplifier version"}], command_dir)

    # Untouched.
    assert _read(user_file) == original
    # Warning emitted.
    out = capsys.readouterr().out
    assert "code-review.md" in out
    assert "skipping" in out.lower() or "warning" in out.lower()

    # Not recorded as owned.
    manifest = command_dir.parent / ".amplifier-generated-commands.json"
    data = json.loads(_read(manifest))
    assert "code-review.md" not in data["commands"]
