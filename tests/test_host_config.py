"""Tests for auto-generated host_config.json and related CLI changes.

Covers:
  - build_host_config() unit tests
  - launch command integration (config resolution, --host-config flag)
  - doctor command provider section
"""

from __future__ import annotations

import json
import stat
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from amplifier_app_opencode.cli import (
    KNOWN_PROVIDER_ENV_VARS,
    build_host_config,
    build_provider_block,
    main,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ALL_ENV_VARS = {v for vs in KNOWN_PROVIDER_ENV_VARS.values() for v in vs}


def _clear_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every provider env var from the environment."""
    for var in _ALL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# build_host_config() unit tests
# ---------------------------------------------------------------------------


def test_build_host_config_writes_anthropic_when_env_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    state_dir = tmp_path / "state"
    result = build_host_config(state_dir)

    assert result == state_dir / "host_config.json"
    data = json.loads(result.read_text(encoding="utf-8"))
    assert list(data["providers"].keys()) == ["anthropic"]


def test_build_host_config_writes_all_four_when_all_env_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "az-test")
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")

    state_dir = tmp_path / "state"
    result = build_host_config(state_dir)

    data = json.loads(result.read_text(encoding="utf-8"))
    assert set(data["providers"].keys()) == {"anthropic", "openai", "azure-openai", "ollama"}


def test_build_host_config_raises_when_no_creds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_provider_env(monkeypatch)

    import click

    state_dir = tmp_path / "state"
    with pytest.raises(click.ClickException) as exc_info:
        build_host_config(state_dir)

    msg = str(exc_info.value.format_message())
    assert "Set at least one of" in msg
    # Every env var name must appear in the error message.
    for var in _ALL_ENV_VARS:
        assert var in msg


def test_build_host_config_creates_state_dir_with_0700(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    state_dir = tmp_path / "new" / "nested" / "state"
    result = build_host_config(state_dir)

    # Directory must exist with mode 0700.
    dir_mode = stat.S_IMODE(state_dir.stat().st_mode)
    assert dir_mode == 0o700, f"state_dir mode is {oct(dir_mode)}, expected 0700"

    # File must exist with mode 0600.
    file_mode = stat.S_IMODE(result.stat().st_mode)
    assert file_mode == 0o600, f"host_config.json mode is {oct(file_mode)}, expected 0600"


def test_build_host_config_overwrites_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    existing = state_dir / "host_config.json"
    existing.write_text(json.dumps({"providers": {"openai": {}, "ollama": {}}}), encoding="utf-8")

    result = build_host_config(state_dir)

    data = json.loads(result.read_text(encoding="utf-8"))
    # Should reflect current env state, not old file content.
    assert list(data["providers"].keys()) == ["anthropic"]


def test_build_host_config_handles_azure_alternate_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AZURE_OPENAI_KEY (alternate) should also trigger the azure-openai provider."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("AZURE_OPENAI_KEY", "az-alt-test")  # not AZURE_OPENAI_API_KEY

    state_dir = tmp_path / "state"
    result = build_host_config(state_dir)

    data = json.loads(result.read_text(encoding="utf-8"))
    assert "azure-openai" in data["providers"]


# ---------------------------------------------------------------------------
# build_provider_block() context-clamp unit tests
# ---------------------------------------------------------------------------


def _model(model_id: str, context: int, output: int = 64000) -> dict:
    return {"id": model_id, "limit": {"context": context, "output": output}}


def test_provider_block_forwards_context_verbatim_when_no_clamp() -> None:
    """Default (max_context=None) forwards the backend context window unchanged."""
    block = build_provider_block(
        base_url="http://x/v1",
        api_key="k",
        models=[_model("claude-sonnet-4-6", 1_000_000)],
    )
    assert block["models"]["claude-sonnet-4-6"]["limit"]["context"] == 1_000_000


def test_provider_block_clamps_context_over_ceiling() -> None:
    """A context window above max_context is capped to the ceiling."""
    block = build_provider_block(
        base_url="http://x/v1",
        api_key="k",
        models=[_model("claude-sonnet-4-6", 1_000_000)],
        max_context=200_000,
    )
    limit = block["models"]["claude-sonnet-4-6"]["limit"]
    assert limit["context"] == 200_000
    # output is never touched by the context clamp
    assert limit["output"] == 64000


def test_provider_block_leaves_context_below_ceiling_untouched() -> None:
    """A context window at or below max_context is left as-is."""
    block = build_provider_block(
        base_url="http://x/v1",
        api_key="k",
        models=[_model("claude-haiku-4-5", 200_000)],
        max_context=200_000,
    )
    assert block["models"]["claude-haiku-4-5"]["limit"]["context"] == 200_000


def test_provider_block_clamp_applies_per_model() -> None:
    """Clamp is applied independently to each model; small windows are preserved."""
    block = build_provider_block(
        base_url="http://x/v1",
        api_key="k",
        models=[
            _model("claude-opus-4-8", 1_000_000),
            _model("gpt-5-mini", 128_000),
        ],
        max_context=200_000,
    )
    assert block["models"]["claude-opus-4-8"]["limit"]["context"] == 200_000
    assert block["models"]["gpt-5-mini"]["limit"]["context"] == 128_000


def test_max_context_cli_option_clamps_written_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`prepare --max-context` clamps the context window in the written config."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    _patch_launch_deps(monkeypatch, tmp_path)

    monkeypatch.setattr(
        "amplifier_app_opencode.cli.fetch_models",
        lambda *a, **kw: [_model("claude-sonnet-4-6", 1_000_000)],
    )
    captured: dict = {}

    def _capture_write(config_path, provider, **kwargs):
        captured["provider"] = provider
        return config_path

    monkeypatch.setattr(
        "amplifier_app_opencode.cli.write_opencode_config", _capture_write
    )

    runner = CliRunner()
    result = runner.invoke(main, ["prepare", "--max-context", "200000"])

    assert result.exit_code == 0, result.output
    ctx = captured["provider"]["models"]["claude-sonnet-4-6"]["limit"]["context"]
    assert ctx == 200_000


# ---------------------------------------------------------------------------
# launch integration tests
# ---------------------------------------------------------------------------


def _make_mock_proc() -> MagicMock:
    proc = MagicMock()
    proc.pid = 99999
    return proc


def _patch_launch_deps(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Patch all I/O-heavy functions so launch tests run without real processes."""
    # Server never running → triggers the start branch.
    monkeypatch.setattr("amplifier_app_opencode.cli.server_is_running", lambda *a, **kw: False)
    monkeypatch.setattr("amplifier_app_opencode.cli.wait_for_server_ready", lambda *a, **kw: True)
    # Models discovery returns empty list — ok for these tests.
    monkeypatch.setattr("amplifier_app_opencode.cli.fetch_models", lambda *a, **kw: [])
    # Config writing returns a dummy path.
    opencode_cfg = tmp_path / "opencode.jsonc"
    monkeypatch.setattr(
        "amplifier_app_opencode.cli.write_opencode_config", lambda *a, **kw: opencode_cfg
    )
    # Don't actually exec opencode.
    monkeypatch.setattr("amplifier_app_opencode.cli.exec_opencode", lambda *a, **kw: None)
    # Avoid touching /tmp/amplifier-agent.log and pid file.
    monkeypatch.setattr("amplifier_app_opencode.cli.SERVER_LOG_PATH", tmp_path / "agent.log")
    monkeypatch.setattr("amplifier_app_opencode.cli.PID_FILE", tmp_path / "agent.pid")


def test_launch_uses_generated_config_when_no_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When no --host-config is passed, launch auto-generates one from env and passes
    it to start_amplifier_agent."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("HOME", str(tmp_path))
    _patch_launch_deps(monkeypatch, tmp_path)

    captured: list[Path | None] = []

    def mock_start(*, port, workspace, host_config, api_key, binary):
        captured.append(host_config)
        return _make_mock_proc()

    monkeypatch.setattr("amplifier_app_opencode.cli.start_amplifier_agent", mock_start)

    runner = CliRunner()
    result = runner.invoke(main, ["launch", "--no-launch"])

    assert result.exit_code == 0, result.output
    assert len(captured) == 1
    assert captured[0] is not None
    # The generated file must be inside our tmp HOME.
    expected = tmp_path / ".amplifier-opencode" / "state" / "host_config.json"
    assert captured[0] == expected
    # And it must actually contain the provider.
    data = json.loads(expected.read_text(encoding="utf-8"))
    assert "anthropic" in data["providers"]


def test_launch_uses_user_config_when_flag_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When --host-config PATH is given, that path is passed verbatim; no auto-generation."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    _patch_launch_deps(monkeypatch, tmp_path)

    # Create a real file so click's exists=True check passes.
    user_cfg = tmp_path / "my_config.json"
    user_cfg.write_text(json.dumps({"providers": {"openai": {}}}), encoding="utf-8")

    captured: list[Path | None] = []

    def mock_start(*, port, workspace, host_config, api_key, binary):
        captured.append(host_config)
        return _make_mock_proc()

    monkeypatch.setattr("amplifier_app_opencode.cli.start_amplifier_agent", mock_start)

    runner = CliRunner()
    result = runner.invoke(main, ["launch", "--host-config", str(user_cfg), "--no-launch"])

    assert result.exit_code == 0, result.output
    assert len(captured) == 1
    assert captured[0] == user_cfg
    # Auto-generated path must NOT exist.
    auto_path = tmp_path / ".amplifier-opencode" / "state" / "host_config.json"
    assert not auto_path.exists()


def test_launch_fails_when_no_creds_and_no_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no env vars and no --host-config, launch aborts with a clear message."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    _patch_launch_deps(monkeypatch, tmp_path)

    started: list[bool] = []

    def mock_start(**kwargs):  # type: ignore[override]
        started.append(True)
        return _make_mock_proc()

    monkeypatch.setattr("amplifier_app_opencode.cli.start_amplifier_agent", mock_start)

    runner = CliRunner()
    result = runner.invoke(main, ["launch", "--no-launch"])

    assert result.exit_code != 0
    assert "Set at least one of" in result.output
    assert not started, "amplifier-agent must never be spawned when no creds"


# ---------------------------------------------------------------------------
# doctor tests
# ---------------------------------------------------------------------------


def _stub_doctor_binaries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Stub binary checks and server checks so doctor runs without real tools."""
    monkeypatch.setattr(
        "amplifier_app_opencode.cli._check_amplifier_agent_binary",
        lambda: ("[ OK ]", "amplifier-agent found at /fake/amplifier-agent (0.8.0)"),
    )
    monkeypatch.setattr(
        "amplifier_app_opencode.cli._check_opencode_binary",
        lambda: ("[ OK ]", "opencode found at /fake/opencode (1.17.0)"),
    )
    monkeypatch.setattr(
        "amplifier_app_opencode.cli._check_amplifier_agent_server",
        lambda base_url, api_key: ("[INFO]", "server not running"),
    )
    monkeypatch.setattr(
        "amplifier_app_opencode.cli._check_opencode_config",
        lambda: ("[INFO]", "config not yet generated"),
    )
    monkeypatch.setattr(
        "amplifier_app_opencode.cli._check_live_models",
        lambda base_url, api_key: ("[INFO]", "Skipped (server not running)"),
    )


def test_doctor_reports_each_provider_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With 2 of 4 providers set, doctor shows ✓ for 2 and ✗ for 2."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    _stub_doctor_binaries(monkeypatch, tmp_path)

    runner = CliRunner()
    result = runner.invoke(main, ["doctor"])

    assert result.output.count("✓") == 2
    assert result.output.count("✗") == 2


def test_doctor_fails_when_no_providers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With no provider env vars, doctor exits 1 and prints the set-at-least-one message."""
    _clear_provider_env(monkeypatch)
    _stub_doctor_binaries(monkeypatch, tmp_path)

    runner = CliRunner()
    result = runner.invoke(main, ["doctor"])

    assert result.exit_code == 1
    assert "Set at least one of" in result.output


def test_doctor_passes_when_at_least_one_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With at least one provider env var set, doctor exits 0."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")
    _stub_doctor_binaries(monkeypatch, tmp_path)

    runner = CliRunner()
    result = runner.invoke(main, ["doctor"])

    assert result.exit_code == 0
    assert "ollama" in result.output
    assert "✓" in result.output
