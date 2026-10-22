import json
from pathlib import Path

from amplifier_app_opencode import cleanup

PROVIDER_BLOCK = {
    "npm": "@ai-sdk/openai-compatible",
    "name": "Amplifier",
    "options": {"baseURL": "http://127.0.0.1:8765/v1", "apiKey": "unused"},
    "models": {"anthropic/claude-sonnet-5-5": {"name": "Claude Sonnet 5.5"}},
}


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _bridge(scope: Path) -> None:
    """Command and agent files with their manifests, as the 0.4.x skills and modes bridges wrote them."""
    _write_json(scope / ".amplifier-generated-commands.json", {"commands": ["brainstorm.md", "debug.md"]})
    (scope / "command").mkdir(parents=True)
    (scope / "command" / "brainstorm.md").write_text("!amplifier:skill brainstorm $ARGUMENTS\n")
    (scope / "command" / "debug.md").write_text("!amplifier:skill debug $ARGUMENTS\n")
    (scope / "command" / "mine.md").write_text("a user command\n")
    _write_json(scope / ".amplifier-generated-agents.json", {"agents": ["amplifier-mode-plan.md"]})
    (scope / "agent").mkdir(parents=True)
    (scope / "agent" / "amplifier-mode-plan.md").write_text("---\nmode: primary\n---\n")
    (scope / "agent" / "reviewer.md").write_text("a user agent\n")


def test_global_and_project_leftovers_are_removed_and_other_content_kept(tmp_path: Path) -> None:
    global_dir = tmp_path / "config" / "opencode"
    project = tmp_path / "project"
    global_dir.mkdir(parents=True)
    (global_dir / "opencode.jsonc").write_text(
        "{\n"
        "  // my settings\n"
        '  "$schema": "https://opencode.ai/config.json",\n'
        '  "model": "anthropic/claude-sonnet-5-5", /* keep */\n'
        '  "provider": {\n'
        f'    "amplifier": {json.dumps(PROVIDER_BLOCK)},\n'
        '    "openai": {"options": {"baseURL": "https://example.com/v1"}},\n'
        "  },\n"
        "}\n",
        encoding="utf-8",
    )
    _write_json(
        project / "opencode.json",
        {"$schema": "https://opencode.ai/config.json", "provider": {"amplifier": PROVIDER_BLOCK}},
    )
    _bridge(global_dir)
    _bridge(project / ".opencode")

    found = cleanup.find(global_dir, project)

    assert [(block.path, block.has_comments) for block in found.provider_blocks] == [
        (global_dir / "opencode.jsonc", True),
        (project / "opencode.json", False),
    ]
    expected_files = []
    for scope in (global_dir, project / ".opencode"):
        expected_files += [
            scope / "command" / "brainstorm.md",
            scope / "command" / "debug.md",
            scope / ".amplifier-generated-commands.json",
            scope / "agent" / "amplifier-mode-plan.md",
            scope / ".amplifier-generated-agents.json",
        ]
    assert found.files == expected_files

    cleanup.remove(found)

    assert json.loads((global_dir / "opencode.jsonc").read_text()) == {
        "$schema": "https://opencode.ai/config.json",
        "model": "anthropic/claude-sonnet-5-5",
        "provider": {"openai": {"options": {"baseURL": "https://example.com/v1"}}},
    }
    assert json.loads((project / "opencode.json").read_text()) == {"$schema": "https://opencode.ai/config.json"}
    for path in expected_files:
        assert not path.exists()
    for scope in (global_dir, project / ".opencode"):
        assert (scope / "command" / "mine.md").exists()
        assert (scope / "agent" / "reviewer.md").exists()
    assert not cleanup.find(global_dir, project)


def test_nothing_to_clean(tmp_path: Path) -> None:
    global_dir = tmp_path / "opencode"
    _write_json(global_dir / "opencode.json", {"provider": {"anthropic": {}}})
    (global_dir / "config.json").write_text("not json")

    assert not cleanup.find(global_dir, tmp_path / "missing-project")


def test_manifest_entries_never_leave_their_directory(tmp_path: Path) -> None:
    _write_json(tmp_path / ".amplifier-generated-commands.json", {"commands": ["../opencode.json", "ok.md", "..", 3]})
    (tmp_path / "command").mkdir()
    (tmp_path / "command" / "ok.md").write_text("x")
    (tmp_path / "opencode.json").write_text("{}")

    found = cleanup.find(tmp_path)

    assert found.files == [tmp_path / "command" / "ok.md", tmp_path / ".amplifier-generated-commands.json"]


def test_strip_jsonc_keeps_comment_markers_inside_strings() -> None:
    text = '{"url": "https://example.com/a,b", "glob": "/* not a comment */", "list": [1, 2,],}'

    assert json.loads(cleanup.strip_jsonc(text)) == {
        "url": "https://example.com/a,b",
        "glob": "/* not a comment */",
        "list": [1, 2],
    }
