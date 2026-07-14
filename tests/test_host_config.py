"""Tests for CLI host-config pass-through, provider-block clamping, and doctor.

Covers:
  - launch/prepare pass ``--host-config`` through verbatim (or omit it
    entirely) rather than auto-generating one from env vars -- amplifier-agent
    auto-enables every provider whose credentials resolve when no explicit
    ``--config`` providers block is given.
  - build_provider_block() context-clamp unit tests
  - doctor command provider section (delegates to
    ``amplifier-agent providers list --json``)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from amplifier_app_opencode.cli import build_provider_block, main

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
    _patch_launch_deps(monkeypatch, tmp_path)

    monkeypatch.setattr(
        "amplifier_app_opencode.cli.fetch_models",
        lambda *a, **kw: [_model("claude-sonnet-4-6", 1_000_000)],
    )
    captured: dict = {}

    def _capture_write(config_path, provider, **kwargs):
        captured["provider"] = provider
        return config_path

    monkeypatch.setattr("amplifier_app_opencode.cli.write_opencode_config", _capture_write)

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
    # Server never running -> triggers the start branch.
    monkeypatch.setattr("amplifier_app_opencode.cli.server_is_running", lambda *a, **kw: False)
    monkeypatch.setattr("amplifier_app_opencode.cli.wait_for_server_ready", lambda *a, **kw: True)
    # Models discovery returns empty list -- ok for these tests.
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


def test_launch_passes_no_config_when_no_flag_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When no --host-config is passed, launch passes host_config=None through to
    start_amplifier_agent unchanged -- no auto-generation. amplifier-agent itself
    auto-enables whichever providers have resolvable credentials."""
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
    assert captured == [None]
    # No state dir / host_config.json must be auto-generated anywhere.
    assert not (tmp_path / ".amplifier-opencode").exists()


def test_launch_uses_user_config_when_flag_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When --host-config PATH is given, that path is passed verbatim."""
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
    assert captured == [user_cfg]


# ---------------------------------------------------------------------------
# doctor tests
# ---------------------------------------------------------------------------


def _stub_doctor_binaries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Stub binary checks and server checks so doctor runs without real tools."""
    monkeypatch.setattr(
        "amplifier_app_opencode.cli._check_amplifier_agent_binary",
        lambda: ("[ OK ]", "amplifier-agent found at /fake/amplifier-agent (0.9.0)"),
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


def _provider_report(rows: list[dict]) -> dict:
    return {"schema_version": 1, "providers": rows}


def test_doctor_reports_each_provider_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With 2 of 4 providers resolvable per the agent's report, doctor shows
    check for 2 and cross for 2."""
    _stub_doctor_binaries(monkeypatch, tmp_path)
    report = _provider_report(
        [
            {
                "name": "anthropic",
                "module": "provider-anthropic",
                "resolvable": True,
                "source": "env",
                "env_var": "ANTHROPIC_API_KEY",
            },
            {
                "name": "openai",
                "module": "provider-openai",
                "resolvable": True,
                "source": "file",
                "env_var": "OPENAI_API_KEY",
            },
            {
                "name": "azure-openai",
                "module": "provider-azure-openai",
                "resolvable": False,
                "source": "none",
                "env_var": "AZURE_OPENAI_API_KEY",
            },
            {
                "name": "ollama",
                "module": "provider-ollama",
                "resolvable": False,
                "source": "default",
                "env_var": "OLLAMA_HOST",
            },
        ]
    )
    monkeypatch.setattr(
        "amplifier_app_opencode.cli._fetch_provider_report", lambda *a, **kw: report
    )

    runner = CliRunner()
    result = runner.invoke(main, ["doctor"])

    assert result.output.count("\u2713") == 2
    assert result.output.count("\u2717") == 2
    assert result.exit_code == 0, result.output


def test_doctor_fails_when_provider_report_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When `amplifier-agent providers list --json` can't be run, doctor fails
    loudly instead of silently reporting nothing."""
    _stub_doctor_binaries(monkeypatch, tmp_path)
    monkeypatch.setattr("amplifier_app_opencode.cli._fetch_provider_report", lambda *a, **kw: None)

    runner = CliRunner()
    result = runner.invoke(main, ["doctor"])

    assert result.exit_code == 1
    assert "Could not run `amplifier-agent providers list --json`" in result.output


def test_doctor_fails_when_no_providers_resolvable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a report where zero providers are resolvable, doctor exits 1."""
    _stub_doctor_binaries(monkeypatch, tmp_path)
    report = _provider_report(
        [
            {
                "name": "anthropic",
                "module": "provider-anthropic",
                "resolvable": False,
                "source": "none",
                "env_var": "ANTHROPIC_API_KEY",
            },
        ]
    )
    monkeypatch.setattr(
        "amplifier_app_opencode.cli._fetch_provider_report", lambda *a, **kw: report
    )

    runner = CliRunner()
    result = runner.invoke(main, ["doctor"])

    assert result.exit_code == 1
    assert "No provider credentials resolvable" in result.output


def test_doctor_passes_when_at_least_one_provider_resolvable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With at least one resolvable provider in the agent's report, doctor exits 0."""
    _stub_doctor_binaries(monkeypatch, tmp_path)
    report = _provider_report(
        [
            {
                "name": "ollama",
                "module": "provider-ollama",
                "resolvable": True,
                "source": "env",
                "env_var": "OLLAMA_HOST",
            },
        ]
    )
    monkeypatch.setattr(
        "amplifier_app_opencode.cli._fetch_provider_report", lambda *a, **kw: report
    )

    runner = CliRunner()
    result = runner.invoke(main, ["doctor"])

    assert result.exit_code == 0
    assert "ollama" in result.output
    assert "\u2713" in result.output
