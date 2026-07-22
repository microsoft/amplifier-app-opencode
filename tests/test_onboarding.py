"""Unit tests for the credential onboarding wizard."""

from __future__ import annotations

import subprocess

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
    # The provider menu is enumerated from the agent's own report, so drive it
    # through that real path rather than the hardcoded fallback.
    monkeypatch.setattr(
        onboarding.prereqs,
        "fetch_provider_report",
        lambda: {"providers": [{"name": "anthropic"}, {"name": "openai"}]},
    )

    prompts = iter([1, "sk-ant-test"])  # provider choice, then key
    monkeypatch.setattr(onboarding.click, "prompt", lambda *a, **kw: next(prompts))

    stored: dict[str, object] = {}

    def _auth_set(
        agent_bin: str,
        provider: onboarding.Provider,
        value: str,
        *,
        endpoint: str | None = None,
    ) -> bool:
        stored["provider"] = provider.provider_id
        stored["value"] = value
        return True

    monkeypatch.setattr(onboarding, "_auth_set", _auth_set)
    monkeypatch.setattr(onboarding.prereqs, "has_resolvable_provider", lambda: True)

    result = onboarding.run_onboarding(assume_yes=True)
    assert result is True
    assert stored == {"provider": "anthropic", "value": "sk-ant-test"}


def test_available_providers_enumerates_from_agent_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The menu follows the agent's report -- including ids our overlay never heard of."""
    monkeypatch.setattr(
        onboarding.prereqs,
        "fetch_provider_report",
        lambda: {"providers": [{"name": "openai"}, {"name": "some-new-provider"}]},
    )
    ids = [p.provider_id for p in onboarding._available_providers()]
    # Order and membership come from the agent, and an id absent from
    # _PRESENTATION still appears (proves we don't filter to a hardcoded set).
    assert ids == ["openai", "some-new-provider"]


def test_available_providers_falls_back_when_report_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Agent offline / report unreadable -> fall back to the presentation overlay."""
    monkeypatch.setattr(onboarding.prereqs, "fetch_provider_report", lambda: None)
    ids = [p.provider_id for p in onboarding._available_providers()]
    assert ids == list(onboarding._PRESENTATION)


def test_auth_set_timeout_does_not_leak_secret(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A timeout must never surface the argv, which contains the plaintext key."""
    secret = "sk-ant-SUPERSECRET"

    def _raise_timeout(*a: object, **kw: object) -> None:
        raise subprocess.TimeoutExpired(
            cmd=["amplifier-agent", "auth", "set", "anthropic", secret], timeout=30.0
        )

    monkeypatch.setattr(onboarding.subprocess, "run", _raise_timeout)
    prov = onboarding._PRESENTATION["anthropic"]
    ok = onboarding._auth_set("/fake/amplifier-agent", prov, secret)
    assert ok is False
    assert secret not in capsys.readouterr().out


def test_auth_set_failure_redacts_secret_from_agent_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """If the agent echoes the argv (with the key) in its error, we scrub it."""
    secret = "sk-ant-SUPERSECRET"

    class _Result:
        returncode = 1
        stderr = f"invalid command: auth set anthropic {secret}"
        stdout = ""

    monkeypatch.setattr(onboarding.subprocess, "run", lambda *a, **kw: _Result())
    prov = onboarding._PRESENTATION["anthropic"]
    ok = onboarding._auth_set("/fake/amplifier-agent", prov, secret)
    assert ok is False
    assert secret not in capsys.readouterr().out
