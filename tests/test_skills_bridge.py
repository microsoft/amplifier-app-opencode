"""Tests for the skills bridge: /v1/skills -> opencode /command files.

Covers:
  - fetch_skills() parses the ``data`` list on 200 and returns [] on any
    error (network error, non-200, malformed JSON, wrong shape).
  - fetch_skills() normalizes the provenance fields (``source``/``shadowed``)
    the agent reports, defensively -- the agent is a separate process on its
    own release cadence, so a missing or malformed field must degrade, never
    raise.
  - render_bridge_conflicts() reports every shadowed file, and stays silent
    when nothing collides.
  - resolve_command_dir() returns the project-scope command dir when a
    project dir is given and the global command dir otherwise.
  - write_command_files() writes correct frontmatter + body per skill, with
    sidecar-manifest reconciliation:
      * stale generated files are pruned on a later run
      * a user's own (unowned) command file is never overwritten
      * two skills mapping to the same filename in ONE run: first wins
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from amplifier_app_opencode.cli import (
    GLOBAL_OPENCODE_DIR,
    SKILL_COMMAND_DESCRIPTION_PREFIX,
    fetch_skills,
    render_bridge_conflicts,
    resolve_command_dir,
    skill_command_description,
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


def _fetch_with_data(monkeypatch: pytest.MonkeyPatch, data: Any) -> list[dict[str, Any]]:
    """Run fetch_skills() against a stubbed 200 response carrying ``data``."""
    monkeypatch.setattr(
        "amplifier_app_opencode.cli.httpx.get",
        lambda *a, **kw: _FakeResponse(json_data={"object": "list", "data": data}),
    )
    return fetch_skills("http://127.0.0.1:9099/v1", "key")


def test_fetch_skills_parses_data_on_200(monkeypatch: pytest.MonkeyPatch) -> None:
    skills = [
        {"name": "code-review", "description": "Review changed code."},
        {"name": "council", "description": "Convene the panel."},
    ]
    # ``source``/``shadowed`` are always materialised (see the normalization tests
    # below); name and description pass through untouched.
    assert _fetch_with_data(monkeypatch, skills) == [
        {
            "name": "code-review",
            "description": "Review changed code.",
            "source": "",
            "shadowed": [],
        },
        {"name": "council", "description": "Convene the panel.", "source": "", "shadowed": []},
    ]


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
# fetch_skills(): provenance normalization (source / shadowed)
# ---------------------------------------------------------------------------


def test_fetch_skills_passes_wellformed_shadowed_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """A well-formed conflict report survives the fetch layer intact."""
    rows = _fetch_with_data(
        monkeypatch,
        [
            {
                "name": "code-review",
                "description": "Review changed code.",
                "source": "/opt/bundle/skills/code-review/SKILL.md",
                "shadowed": [
                    {"source": "/root/.amplifier/skills/code-review/SKILL.md"},
                    {"source": "/root/other/code-review/SKILL.md"},
                ],
            }
        ],
    )
    assert rows[0]["source"] == "/opt/bundle/skills/code-review/SKILL.md"
    assert rows[0]["shadowed"] == [
        {"source": "/root/.amplifier/skills/code-review/SKILL.md"},
        {"source": "/root/other/code-review/SKILL.md"},
    ]


@pytest.mark.parametrize(
    "row",
    [
        pytest.param({"name": "s"}, id="absent"),
        pytest.param({"name": "s", "shadowed": None}, id="null"),
        pytest.param({"name": "s", "shadowed": "a-string"}, id="string"),
        pytest.param({"name": "s", "shadowed": ["a", "b"]}, id="list-of-strings"),
        pytest.param({"name": "s", "shadowed": [{"path": "/x"}]}, id="dict-missing-source"),
        pytest.param({"name": "s", "shadowed": [{"source": ""}]}, id="dict-empty-source"),
        pytest.param({"name": "s", "shadowed": [{"source": 7}]}, id="dict-nonstring-source"),
        pytest.param({"name": "s", "shadowed": {"source": "/x"}}, id="bare-dict"),
    ],
)
def test_fetch_skills_normalizes_malformed_shadowed(
    row: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any malformed ``shadowed`` degrades to ``[]`` -- never an exception.

    The agent is a separate process on its own release cadence, so an older or
    drifted server must cost the user a missing conflict report, not a failed launch.
    """
    rows = _fetch_with_data(monkeypatch, [row])
    assert rows[0]["shadowed"] == []


def test_fetch_skills_normalizes_partially_malformed_shadowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bad elements are dropped; the good ones are kept."""
    rows = _fetch_with_data(
        monkeypatch,
        [
            {
                "name": "s",
                "shadowed": ["junk", {"source": "/good/one"}, {"nope": 1}, {"source": "/good/two"}],
            }
        ],
    )
    assert rows[0]["shadowed"] == [{"source": "/good/one"}, {"source": "/good/two"}]


@pytest.mark.parametrize(
    "row",
    [
        pytest.param({"name": "s"}, id="absent"),
        pytest.param({"name": "s", "source": None}, id="null"),
        pytest.param({"name": "s", "source": 42}, id="non-string"),
    ],
)
def test_fetch_skills_normalizes_missing_source(
    row: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing/malformed entry ``source`` becomes ``""``."""
    assert _fetch_with_data(monkeypatch, [row])[0]["source"] == ""


# ---------------------------------------------------------------------------
# render_bridge_conflicts()
# ---------------------------------------------------------------------------


def test_render_bridge_conflicts_names_winner_and_every_loser(
    capsys: pytest.CaptureFixture[str],
) -> None:
    render_bridge_conflicts(
        "skills",
        [
            {
                "name": "code-review",
                "source": "/opt/bundle/skills/code-review/SKILL.md",
                "shadowed": [
                    {"source": "/root/.amplifier/skills/code-review/SKILL.md"},
                    {"source": "/root/extra/code-review/SKILL.md"},
                ],
            },
            # No collision -- must not appear in the report at all.
            {"name": "council", "source": "/opt/bundle/skills/council/SKILL.md", "shadowed": []},
        ],
    )
    out = capsys.readouterr().out
    assert "skills: 1 name conflict" in out
    assert "code-review" in out
    # The winner is named, and labelled as the one that runs.
    assert "/opt/bundle/skills/code-review/SKILL.md" in out
    assert "runs:" in out
    # Every loser is named.
    assert "/root/.amplifier/skills/code-review/SKILL.md" in out
    assert "/root/extra/code-review/SKILL.md" in out
    assert out.count("shadowed:") == 2
    # The clean entry is not reported.
    assert "council" not in out


def test_render_bridge_conflicts_silent_when_nothing_collides(
    capsys: pytest.CaptureFixture[str],
) -> None:
    render_bridge_conflicts(
        "skills",
        [{"name": "council", "source": "/opt/bundle/skills/council/SKILL.md", "shadowed": []}],
    )
    assert capsys.readouterr().out == ""


def test_render_bridge_conflicts_silent_on_empty_list(capsys: pytest.CaptureFixture[str]) -> None:
    render_bridge_conflicts("skills", [])
    assert capsys.readouterr().out == ""


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


def test_skill_command_description_marks_provenance() -> None:
    """Bridged skills lead with "(Amplifier)" so the "/" menu shows where they came from.

    The marker LEADS rather than trails (unlike the modes suffix) because opencode's
    command autocomplete truncates the description to the popup width with no ellipsis --
    a trailing marker would be cut off and never seen.
    """
    assert skill_command_description("Convene the panel.") == "(Amplifier) Convene the panel."
    assert SKILL_COMMAND_DESCRIPTION_PREFIX == "(Amplifier) "

    # Idempotent: re-running the launcher over an already-marked description must not
    # stack prefixes.
    once = skill_command_description("Convene the panel.")
    assert skill_command_description(once) == once

    # An empty description still gets marked (the command is still ours).
    assert skill_command_description("") == "(Amplifier) "


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
    # Description present and round-trips (valid YAML/JSON, special chars safe), carrying
    # the "(Amplifier)" provenance marker at the head.
    assert (
        _frontmatter_description(cr_content)
        == '(Amplifier) Review changed code: colons "quotes" & more'
    )

    co_content = _read(co)
    assert co_content.splitlines()[-1] == "!amplifier:skill council $ARGUMENTS"
    assert _frontmatter_description(co_content) == "(Amplifier) Convene the panel."

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


def test_write_command_files_skips_intra_run_duplicate_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two skills mapping to the same filename in ONE run: the first one wins.

    Without the guard the second write silently clobbers the first and the manifest
    records the filename twice.
    """
    command_dir = tmp_path / ".opencode" / "command"
    write_command_files(
        [
            {"name": "code-review", "description": "FIRST"},
            {"name": "code-review", "description": "SECOND"},
        ],
        command_dir,
    )

    # First wins: the file on disk is the first entry's content.
    content = _read(command_dir / "code-review.md")
    assert _frontmatter_description(content) == "(Amplifier) FIRST"

    # Warning names the skill and the file.
    out = capsys.readouterr().out
    assert "code-review.md" in out
    assert "code-review" in out
    assert "duplicate" in out.lower()

    # Recorded exactly once.
    manifest = command_dir.parent / ".amplifier-generated-commands.json"
    assert json.loads(_read(manifest))["commands"] == ["code-review.md"]


def test_write_command_files_leaves_unowned_file_untouched(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """REGRESSION: the ownership-skip path is unchanged by the duplicate guard.

    The duplicate check is an ADDITIONAL, separate check; a pre-existing file that
    this launcher did not generate must still be skipped and left byte-identical.
    """
    command_dir = tmp_path / ".opencode" / "command"
    command_dir.mkdir(parents=True, exist_ok=True)

    # User's own command file that we did NOT generate (no manifest yet).
    user_file = command_dir / "code-review.md"
    original = "---\ndescription: my own command\n---\necho hello\n"
    user_file.write_text(original, encoding="utf-8")
    original_bytes = user_file.read_bytes()

    write_command_files([{"name": "code-review", "description": "Amplifier version"}], command_dir)

    # Untouched, byte for byte.
    assert _read(user_file) == original
    assert user_file.read_bytes() == original_bytes
    # Warning emitted.
    out = capsys.readouterr().out
    assert "code-review.md" in out
    assert "skipping" in out.lower() or "warning" in out.lower()

    # Not recorded as owned.
    manifest = command_dir.parent / ".amplifier-generated-commands.json"
    data = json.loads(_read(manifest))
    assert "code-review.md" not in data["commands"]
