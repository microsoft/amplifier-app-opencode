"""Tests for the ``--version`` flag on the amplifier-opencode CLI.

``--version`` reports three things and exits before any bootstrap/self-heal:
  1. amplifier-opencode's own version,
  2. the running amplifier-agent version (or "not installed"),
  3. the minimum amplifier-agent version this build requires.
"""

from __future__ import annotations

from click.testing import CliRunner

from amplifier_app_opencode import __version__, prereqs
from amplifier_app_opencode.cli import main


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
    assert "0.9.5 (installed)" in result.output
    assert f"minimum required amplifier-agent: {prereqs.MIN_AGENT_VERSION}" in result.output
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
    assert "amplifier-agent    not installed" in result.output
    assert f"minimum required amplifier-agent: {prereqs.MIN_AGENT_VERSION}" in result.output


def test_version_flags_below_minimum(monkeypatch):
    monkeypatch.setattr(
        prereqs,
        "detect_agent",
        lambda: _status(present=True, version="0.9.0", meets_min=False),
    )
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "below required minimum" in result.output
