"""Unit tests for the credential onboarding wizard."""

from __future__ import annotations

import pytest

from amplifier_app_opencode import onboarding


def test_needs_onboarding_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(onboarding.prereqs, "has_resolvable_provider", lambda: False)
    assert onboarding.needs_onboarding() is True


def test_needs_onboarding_false_when_resolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(onboarding.prereqs, "has_resolvable_provider", lambda: True)
    assert onboarding.needs_onboarding() is False


def test_needs_onboarding_false_when_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    # Unknown (None) must NOT trigger onboarding -- we only act on a confident zero.
    monkeypatch.setattr(onboarding.prereqs, "has_resolvable_provider", lambda: None)
    assert onboarding.needs_onboarding() is False


def test_run_onboarding_non_interactive_prints_guidance(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(onboarding, "_agent_bin", lambda: "/fake/amplifier-agent")
    monkeypatch.setattr(onboarding.plat, "is_interactive", lambda: False)
    result = onboarding.run_onboarding(assume_yes=True)
    assert result is False
    out = capsys.readouterr().out
    assert "auth set" in out


def test_run_onboarding_no_agent_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(onboarding, "_agent_bin", lambda: None)
    assert onboarding.run_onboarding(assume_yes=True) is False


def test_run_onboarding_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Interactive: pick provider #1, enter a key, agent stores it, becomes resolvable."""
    monkeypatch.setattr(onboarding, "_agent_bin", lambda: "/fake/amplifier-agent")
    monkeypatch.setattr(onboarding.plat, "is_interactive", lambda: True)

    prompts = iter([1, "sk-ant-test"])  # provider choice, then key
    monkeypatch.setattr(onboarding.click, "prompt", lambda *a, **kw: next(prompts))

    stored: dict[str, object] = {}

    def _auth_set(agent_bin: str, provider: onboarding.Provider, value: str) -> bool:
        stored["provider"] = provider.provider_id
        stored["value"] = value
        return True

    monkeypatch.setattr(onboarding, "_auth_set", _auth_set)
    monkeypatch.setattr(onboarding.prereqs, "has_resolvable_provider", lambda: True)

    result = onboarding.run_onboarding(assume_yes=True)
    assert result is True
    assert stored == {"provider": "anthropic", "value": "sk-ant-test"}
