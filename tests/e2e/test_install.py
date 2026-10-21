import json
from pathlib import Path
import shlex

from digital_twin_universe import lib
from digital_twin_universe.schemas import ExecResult
from support import CONFIG, HOME, REPOSITORY, STATE, Twin

PLUGIN = f"{CONFIG}/plugins/amplifier.js"
BUNDLE = REPOSITORY / "src" / "amplifier_app_opencode" / "plugin_bundle" / "amplifier.js"

PROVIDER_BLOCK = {
    "npm": "@ai-sdk/openai-compatible",
    "name": "Amplifier",
    "options": {"baseURL": "http://127.0.0.1:8765/v1", "apiKey": "amplifier"},
    "models": {"anthropic/claude-sonnet-4-5": {"name": "anthropic/claude-sonnet-4-5"}},
}
USER_CONFIG = {"$schema": "https://opencode.ai/config.json", "share": "disabled"}
GENERATED = {
    ".amplifier-generated-commands.json": json.dumps({"commands": ["brainstorm.md", "debug.md"]}),
    "command/brainstorm.md": (
        '---\ndescription: "[amplifier] Brainstorm"\n---\n!amplifier:skill brainstorm $ARGUMENTS\n'
    ),
    "command/debug.md": '---\ndescription: "[amplifier] Debug"\n---\n!amplifier:skill debug $ARGUMENTS\n',
    ".amplifier-generated-agents.json": json.dumps({"agents": ["amplifier-plan.md"]}),
    "agent/amplifier-plan.md": (
        '---\nmode: primary\ndescription: "Plan"\n---\nAmplifier mode "plan". Behaviour is applied server-side by '
        "amplifier-agent.\n\n[amplifier-agent:mode=plan]\n"
    ),
}
USER_FILES = {
    "command/release.md": "---\ndescription: Cut a release\n---\nTag and push a release.\n",
    "agent/reviewer.md": "---\nmode: subagent\ndescription: Reviews diffs\n---\nReview the diff.\n",
}


def test_install_from_the_readme_installs_the_plugin_that_was_built(
    opencode: Twin, installed: ExecResult, tmp_path: Path
) -> None:
    assert f"Installed the plugin: {PLUGIN}" in installed.stdout
    assert "Warning" not in installed.stdout

    lib.pull_files(opencode.id, PLUGIN, tmp_path / "amplifier.js")
    assert (tmp_path / "amplifier.js").read_bytes() == BUNDLE.read_bytes()

    options = json.loads(opencode.read(f"{CONFIG}/amplifier.json"))
    assert options["setDefaultAgent"] is True
    assert options["environment"] == {}
    assert "command" not in options
    assert opencode.check("uv tool list").startswith("amplifier-app-opencode v")


def test_doctor_finds_no_problems(opencode: Twin) -> None:
    result = opencode.run("amplifier-opencode doctor", workdir=HOME)

    assert result.exit_code == 0, f"{result.stdout}\n{result.stderr}"
    assert "opencode: 2.1.2" in result.stdout
    assert f"plugin: {PLUGIN}" in result.stdout
    assert "providers connected in opencode: openai\n" in result.stdout
    assert "providers with credentials in the environment: none\n" in result.stdout
    assert result.stdout.rstrip().endswith("No problems found.")


def test_openai_connected_in_opencode_reaches_the_running_face(opencode: Twin) -> None:
    log = opencode.read(f"{STATE}/plugin.log")

    assert opencode.run('test -z "${OPENAI_API_KEY:-}"').exit_code == 0
    assert f"opencode connections changed; restarting the face for {HOME}" in log
    assert f"face environment for {HOME}: OPENAI_API_KEY from opencode openai" in log


def test_the_amplifier_agent_runs_the_default_model(opencode: Twin) -> None:
    agents = json.loads(opencode.check("opencode debug agents", workdir=HOME))
    amplifier = next(agent for agent in agents if agent["id"] == "amplifier")

    assert amplifier["model"] == {"providerID": "amplifier", "id": "default"}


def test_install_removes_what_0_4_wrote_and_keeps_the_users_files(opencode: Twin) -> None:
    config = {**USER_CONFIG, "provider": {"amplifier": PROVIDER_BLOCK}}
    files = {"opencode.json": json.dumps(config, indent=2), **GENERATED, **USER_FILES}
    for name, content in files.items():
        path = shlex.quote(f"{CONFIG}/{name}")
        opencode.check(f"mkdir -p $(dirname {path}) && printf %s {shlex.quote(content)} > {path}")

    try:
        kept = opencode.run("amplifier-opencode install < /dev/null", workdir=HOME)
        assert kept.exit_code == 0, kept.stderr
        assert "Kept them. Run `amplifier-opencode install --yes` to remove them." in kept.stdout
        assert all(opencode.exists(f"{CONFIG}/{name}") for name in GENERATED)

        doctor = opencode.run("amplifier-opencode doctor", workdir=HOME)
        assert doctor.exit_code == 1
        assert "Files from amplifier-app-opencode 0.4.x remain" in doctor.stdout

        removed = opencode.run("amplifier-opencode install --yes", workdir=HOME)
        assert removed.exit_code == 0, removed.stderr
        assert "Removed them." in removed.stdout

        assert [name for name in GENERATED if opencode.exists(f"{CONFIG}/{name}")] == []
        assert {name: opencode.read(f"{CONFIG}/{name}") for name in USER_FILES} == USER_FILES
        assert json.loads(opencode.read(f"{CONFIG}/opencode.json")) == USER_CONFIG
        assert opencode.run("amplifier-opencode doctor", workdir=HOME).exit_code == 0
    finally:
        paths = " ".join(shlex.quote(f"{CONFIG}/{name}") for name in files)
        opencode.check(f"rm -f {paths}")
