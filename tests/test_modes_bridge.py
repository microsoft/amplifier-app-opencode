"""Tests for the modes bridge: /v1/modes -> opencode primary-agent files.

Covers:
  - fetch_modes() parses the ``data`` list on 200 and returns [] on any
    error (network error, non-200, malformed JSON, wrong shape).
  - fetch_modes() normalizes the provenance fields (``source``/``shadowed``)
    the agent reports, defensively -- the agent is a separate process on its
    own release cadence, so a missing or malformed field must degrade, never
    raise.
  - render_bridge_conflicts() reports every shadowed file for the modes face.
  - resolve_agent_dir() returns the project-scope agent dir when a project
    dir is given and the global agent dir otherwise.
  - write_agent_files() writes correct frontmatter (mode: primary, description,
    amplifier/mode-<name> model alias) with the ``amplifier-`` filename prefix,
    plus sidecar-manifest reconciliation:
      * stale generated files are pruned on a later run
      * a user's own (unowned) agent file is never overwritten
      * two modes mapping to the same filename in ONE run: first wins
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from amplifier_app_opencode.cli import (
    GLOBAL_OPENCODE_DIR,
    fetch_modes,
    render_bridge_conflicts,
    resolve_agent_dir,
    write_agent_files,
)

# ---------------------------------------------------------------------------
# fetch_modes()
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
                request=httpx.Request("GET", "http://x/v1/modes"),
                response=self,  # type: ignore[arg-type]
            )

    def json(self):
        if self._raise_json:
            raise ValueError("bad json")
        return self._json_data


def _fetch_with_data(monkeypatch: pytest.MonkeyPatch, data: Any) -> list[dict[str, Any]]:
    """Run fetch_modes() against a stubbed 200 response carrying ``data``."""
    monkeypatch.setattr(
        "amplifier_app_opencode.cli.httpx.get",
        lambda *a, **kw: _FakeResponse(json_data={"object": "list", "data": data}),
    )
    return fetch_modes("http://127.0.0.1:9099/v1", "key")


def test_fetch_modes_parses_data_on_200(monkeypatch: pytest.MonkeyPatch) -> None:
    modes = [
        {"name": "plan", "description": "Read-only planning mode."},
        {"name": "brainstorm", "description": "Exploratory design mode."},
    ]
    # ``source``/``shadowed`` are always materialised (see the normalization tests
    # below); name and description pass through untouched.
    assert _fetch_with_data(monkeypatch, modes) == [
        {
            "name": "plan",
            "description": "Read-only planning mode.",
            "source": "",
            "shadowed": [],
        },
        {
            "name": "brainstorm",
            "description": "Exploratory design mode.",
            "source": "",
            "shadowed": [],
        },
    ]


def test_fetch_modes_returns_empty_on_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*a, **kw):
        raise httpx.ConnectError("no server")

    monkeypatch.setattr("amplifier_app_opencode.cli.httpx.get", _boom)
    assert fetch_modes("http://127.0.0.1:9099/v1", "key") == []


def test_fetch_modes_returns_empty_on_non_200(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "amplifier_app_opencode.cli.httpx.get",
        lambda *a, **kw: _FakeResponse(status_code=500, json_data={"data": []}),
    )
    assert fetch_modes("http://127.0.0.1:9099/v1", "key") == []


def test_fetch_modes_returns_empty_on_bad_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "amplifier_app_opencode.cli.httpx.get",
        lambda *a, **kw: _FakeResponse(raise_json=True),
    )
    assert fetch_modes("http://127.0.0.1:9099/v1", "key") == []


def test_fetch_modes_returns_empty_on_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # Body is a dict but has no ``data`` list.
    monkeypatch.setattr(
        "amplifier_app_opencode.cli.httpx.get",
        lambda *a, **kw: _FakeResponse(json_data={"object": "list"}),
    )
    assert fetch_modes("http://127.0.0.1:9099/v1", "key") == []


# ---------------------------------------------------------------------------
# fetch_modes(): provenance normalization (source / shadowed)
# ---------------------------------------------------------------------------


def test_fetch_modes_passes_wellformed_shadowed_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """A well-formed conflict report survives the fetch layer intact."""
    rows = _fetch_with_data(
        monkeypatch,
        [
            {
                "name": "plan",
                "description": "Read-only planning mode.",
                "source": "/root/oc-e2e/.amplifier/modes/plan.md",
                "shadowed": [
                    {"source": "/root/.amplifier/modes/plan.md"},
                    {"source": "/opt/bundle/modes/plan.md"},
                ],
            }
        ],
    )
    assert rows[0]["source"] == "/root/oc-e2e/.amplifier/modes/plan.md"
    assert rows[0]["shadowed"] == [
        {"source": "/root/.amplifier/modes/plan.md"},
        {"source": "/opt/bundle/modes/plan.md"},
    ]


@pytest.mark.parametrize(
    "row",
    [
        pytest.param({"name": "m"}, id="absent"),
        pytest.param({"name": "m", "shadowed": None}, id="null"),
        pytest.param({"name": "m", "shadowed": "a-string"}, id="string"),
        pytest.param({"name": "m", "shadowed": ["a", "b"]}, id="list-of-strings"),
        pytest.param({"name": "m", "shadowed": [{"path": "/x"}]}, id="dict-missing-source"),
        pytest.param({"name": "m", "shadowed": [{"source": ""}]}, id="dict-empty-source"),
        pytest.param({"name": "m", "shadowed": [{"source": 7}]}, id="dict-nonstring-source"),
        pytest.param({"name": "m", "shadowed": {"source": "/x"}}, id="bare-dict"),
    ],
)
def test_fetch_modes_normalizes_malformed_shadowed(
    row: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any malformed ``shadowed`` degrades to ``[]`` -- never an exception."""
    rows = _fetch_with_data(monkeypatch, [row])
    assert rows[0]["shadowed"] == []


def test_fetch_modes_normalizes_partially_malformed_shadowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bad elements are dropped; the good ones are kept."""
    rows = _fetch_with_data(
        monkeypatch,
        [
            {
                "name": "m",
                "shadowed": ["junk", {"source": "/good/one"}, {"nope": 1}, {"source": "/good/two"}],
            }
        ],
    )
    assert rows[0]["shadowed"] == [{"source": "/good/one"}, {"source": "/good/two"}]


@pytest.mark.parametrize(
    "row",
    [
        pytest.param({"name": "m"}, id="absent"),
        pytest.param({"name": "m", "source": None}, id="null"),
        pytest.param({"name": "m", "source": 42}, id="non-string"),
    ],
)
def test_fetch_modes_normalizes_missing_source(
    row: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing/malformed entry ``source`` becomes ``""``."""
    assert _fetch_with_data(monkeypatch, [row])[0]["source"] == ""


# ---------------------------------------------------------------------------
# render_bridge_conflicts() -- the modes face of the shared helper
# ---------------------------------------------------------------------------


def test_render_bridge_conflicts_reports_every_shadowed_mode(
    capsys: pytest.CaptureFixture[str],
) -> None:
    render_bridge_conflicts(
        "modes",
        [
            {
                "name": "plan",
                "source": "/root/oc-e2e/.amplifier/modes/plan.md",
                "shadowed": [
                    {"source": "/root/.amplifier/modes/plan.md"},
                    {"source": "/opt/bundle/modes/plan.md"},
                ],
            },
            {"name": "brainstorm", "source": "/opt/bundle/modes/brainstorm.md", "shadowed": []},
        ],
    )
    out = capsys.readouterr().out
    assert "modes: 1 name conflict" in out
    assert "plan" in out
    assert "/root/oc-e2e/.amplifier/modes/plan.md" in out
    assert "/root/.amplifier/modes/plan.md" in out
    assert "/opt/bundle/modes/plan.md" in out
    assert out.count("shadowed:") == 2
    assert "brainstorm" not in out


def test_render_bridge_conflicts_modes_silent_when_clean(
    capsys: pytest.CaptureFixture[str],
) -> None:
    render_bridge_conflicts(
        "modes",
        [{"name": "plan", "source": "/opt/bundle/modes/plan.md", "shadowed": []}],
    )
    assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# resolve_agent_dir()
# ---------------------------------------------------------------------------


def test_resolve_agent_dir_project_scope(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    assert resolve_agent_dir(project) == project / ".opencode" / "agent"


def test_resolve_agent_dir_global_scope() -> None:
    assert resolve_agent_dir(None) == GLOBAL_OPENCODE_DIR / "agent"


# ---------------------------------------------------------------------------
# write_agent_files()
# ---------------------------------------------------------------------------


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _frontmatter_value(content: str, field: str) -> str:
    """Return the raw value of a top-level frontmatter ``field``."""
    lines = content.splitlines()
    assert lines[0] == "---"
    line = next(line for line in lines if line.startswith(f"{field}:"))
    return line[len(f"{field}:") :].strip()


def test_write_agent_files_writes_frontmatter(tmp_path: Path) -> None:
    agent_dir = tmp_path / ".opencode" / "agent"
    modes = [
        {"name": "plan", "description": 'Plan mode: colons "quotes" & more'},
        {"name": "brainstorm", "description": "Exploratory design mode."},
    ]
    write_agent_files(modes, agent_dir)

    plan = agent_dir / "amplifier-plan.md"
    brainstorm = agent_dir / "amplifier-brainstorm.md"
    assert plan.exists()
    assert brainstorm.exists()

    plan_content = _read(plan)
    assert _frontmatter_value(plan_content, "mode") == "primary"
    # No model field: the mode agent inherits the session's current model, so
    # opencode neither rejects it as invalid nor lists a synthetic mode model.
    assert "\nmodel:" not in plan_content
    # The mode is signalled to amplifier-agent by the body directive instead.
    assert "[amplifier-agent:mode=plan]" in plan_content
    # Description round-trips (JSON-quoted so special chars are safe).
    assert json.loads(_frontmatter_value(plan_content, "description")) == (
        'Plan mode: colons "quotes" & more'
    )

    brainstorm_content = _read(brainstorm)
    assert "\nmodel:" not in brainstorm_content
    assert "[amplifier-agent:mode=brainstorm]" in brainstorm_content

    # Manifest records both generated files.
    manifest = agent_dir.parent / ".amplifier-generated-agents.json"
    assert manifest.exists()
    data = json.loads(_read(manifest))
    assert set(data["agents"]) == {"amplifier-plan.md", "amplifier-brainstorm.md"}


def test_write_agent_files_embeds_mode_directive(tmp_path: Path) -> None:
    """Each mode agent body carries the ``[amplifier-agent:mode=<name>]`` directive."""
    agent_dir = tmp_path / ".opencode" / "agent"
    write_agent_files([{"name": "e2e-proj", "description": "Probe."}], agent_dir)
    content = _read(agent_dir / "amplifier-e2e-proj.md")
    assert "[amplifier-agent:mode=e2e-proj]" in content
    assert "\nmodel:" not in content


def test_write_agent_files_prunes_stale_on_second_run(tmp_path: Path) -> None:
    agent_dir = tmp_path / ".opencode" / "agent"
    first = [
        {"name": "plan", "description": "Plan."},
        {"name": "brainstorm", "description": "Brainstorm."},
    ]
    write_agent_files(first, agent_dir)
    assert (agent_dir / "amplifier-brainstorm.md").exists()

    # Second run: brainstorm is gone.
    second = [{"name": "plan", "description": "Plan."}]
    write_agent_files(second, agent_dir)

    assert (agent_dir / "amplifier-plan.md").exists()
    assert not (agent_dir / "amplifier-brainstorm.md").exists()

    manifest = agent_dir.parent / ".amplifier-generated-agents.json"
    data = json.loads(_read(manifest))
    assert data["agents"] == ["amplifier-plan.md"]


def test_write_agent_files_skips_intra_run_duplicate_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two modes mapping to the same filename in ONE run: the first one wins.

    Without the guard the second write silently clobbers the first and the manifest
    records the filename twice.
    """
    agent_dir = tmp_path / ".opencode" / "agent"
    write_agent_files(
        [
            {"name": "plan", "description": "FIRST"},
            {"name": "plan", "description": "SECOND"},
        ],
        agent_dir,
    )

    # First wins: the file on disk is the first entry's content.
    content = _read(agent_dir / "amplifier-plan.md")
    assert json.loads(_frontmatter_value(content, "description")) == "FIRST"

    # Warning names the mode and the file.
    out = capsys.readouterr().out
    assert "amplifier-plan.md" in out
    assert "plan" in out
    assert "duplicate" in out.lower()

    # Recorded exactly once.
    manifest = agent_dir.parent / ".amplifier-generated-agents.json"
    assert json.loads(_read(manifest))["agents"] == ["amplifier-plan.md"]


def test_write_agent_files_leaves_unowned_file_untouched(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """REGRESSION: the ownership-skip path is unchanged by the duplicate guard.

    The duplicate check is an ADDITIONAL, separate check; a pre-existing file that
    this launcher did not generate must still be skipped and left byte-identical.
    """
    agent_dir = tmp_path / ".opencode" / "agent"
    agent_dir.mkdir(parents=True, exist_ok=True)

    # User's own agent file that we did NOT generate (no manifest yet).
    user_file = agent_dir / "amplifier-plan.md"
    original = "---\nmode: primary\ndescription: my own agent\n---\nHi.\n"
    user_file.write_text(original, encoding="utf-8")
    original_bytes = user_file.read_bytes()

    write_agent_files([{"name": "plan", "description": "Amplifier version"}], agent_dir)

    # Untouched, byte for byte.
    assert _read(user_file) == original
    assert user_file.read_bytes() == original_bytes
    # Warning emitted.
    out = capsys.readouterr().out
    assert "amplifier-plan.md" in out
    assert "skipping" in out.lower() or "warning" in out.lower()

    # Not recorded as owned.
    manifest = agent_dir.parent / ".amplifier-generated-agents.json"
    data = json.loads(_read(manifest))
    assert "amplifier-plan.md" not in data["agents"]
