"""Tests for the ``--version`` flag on the amplifier-opencode CLI.

``--version`` reports two lines and exits before any bootstrap/self-heal:
  1. amplifier-opencode's own version,
  2. the running amplifier-agent version (or "not installed"), with the
     minimum amplifier-agent version this build requires folded onto the
     same row so it's unambiguously about the agent.
"""

from __future__ import annotations

from click.testing import CliRunner

from amplifier_app_opencode import __version__, prereqs
from amplifier_app_opencode.cli import main

MIN = prereqs.MIN_AGENT_VERSION


def _status(*, present: bool, version: str | None, meets_min: bool) -> prereqs.ToolStatus:
    return prereqs.ToolStatus(
        name="amplifier-agent",
        present=present,
        path="/x/amplifier-agent" if present else None,
        version=version,
        meets_min=meets_min,
    )


def test_version_reports_app_agent_and_minimum(monkeypatch):
    monkeypatch.setattr(
        prereqs,
        "detect_agent",
        lambda: _status(present=True, version="amplifier-agent, version 0.9.5", meets_min=True),
    )
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert f"amplifier-opencode {__version__}" in result.output
    # Minimum is folded onto the agent row.
    assert f"0.9.5 (installed; minimum required {MIN})" in result.output
    # --version must short-circuit before any bootstrap/prepare runs.
    assert "not installed" not in result.output


def test_version_reports_missing_agent(monkeypatch):
    monkeypatch.setattr(
        prereqs,
        "detect_agent",
        lambda: _status(present=False, version=None, meets_min=False),
    )
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert f"amplifier-agent    not installed (minimum required {MIN})" in result.output


def test_version_flags_below_minimum(monkeypatch):
    monkeypatch.setattr(
        prereqs,
        "detect_agent",
        lambda: _status(present=True, version="0.9.0", meets_min=False),
    )
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert f"0.9.0 (installed, below minimum; minimum required {MIN})" in result.output
