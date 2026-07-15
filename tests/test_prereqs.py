"""Unit tests for the prereqs lifecycle module (no real subprocess/network)."""

from __future__ import annotations

import pytest

from amplifier_app_opencode import prereqs

# ---------------------------------------------------------------------------
# version helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("amplifier-agent, version 0.9.1", (0, 9, 1)),
        ("1.17.20", (1, 17, 20)),
        ("v2.0.0-rc1", (2, 0, 0)),
        ("no version here", None),
    ],
)
def test_extract_semver(text: str, expected: tuple[int, int, int] | None) -> None:
    assert prereqs.extract_semver(text) == expected


@pytest.mark.parametrize(
    ("have", "want", "expected"),
    [
        ("0.9.1", "0.9.1", True),
        ("0.9.2", "0.9.1", True),
        ("0.9.0", "0.9.1", False),
        ("garbage", "0.9.1", False),  # unparseable fails closed
    ],
)
def test_version_ge(have: str, want: str, expected: bool) -> None:
    assert prereqs.version_ge(have, want) is expected


# ---------------------------------------------------------------------------
# provider report resolution
# ---------------------------------------------------------------------------


def test_has_resolvable_provider_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        prereqs,
        "fetch_provider_report",
        lambda binary=None: {"providers": [{"id": "openai", "resolvable": True}]},
    )
    assert prereqs.has_resolvable_provider() is True


def test_has_resolvable_provider_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        prereqs,
        "fetch_provider_report",
        lambda binary=None: {"providers": [{"id": "openai", "resolvable": False}]},
    )
    assert prereqs.has_resolvable_provider() is False


def test_has_resolvable_provider_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prereqs, "fetch_provider_report", lambda binary=None: None)
    assert prereqs.has_resolvable_provider() is None


# ---------------------------------------------------------------------------
# ensure_agent: healing logic
# ---------------------------------------------------------------------------


def _status(present: bool, version: str | None, meets_min: bool) -> prereqs.ToolStatus:
    return prereqs.ToolStatus(
        name="amplifier-agent",
        present=present,
        path="/fake/amplifier-agent" if present else None,
        version=version,
        meets_min=meets_min,
    )


def test_ensure_agent_healthy_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prereqs, "detect_agent", lambda: _status(True, "0.9.5", True))
    # Should not attempt any install/heal.
    monkeypatch.setattr(prereqs, "install_agent", lambda: pytest.fail("should not install"))
    assert prereqs.ensure_agent(assume_yes=True, allow_install=True) is True


def test_ensure_agent_missing_no_install_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prereqs, "detect_agent", lambda: _status(False, None, False))
    assert prereqs.ensure_agent(assume_yes=True, allow_install=False) is False


def test_ensure_agent_stale_force_heals(monkeypatch: pytest.MonkeyPatch) -> None:
    """Below the hard floor -> skip self-update, force reinstall, succeed."""
    calls: dict[str, int] = {"force": 0}
    states = iter([_status(True, "0.8.0", False), _status(True, "0.9.1", True)])
    monkeypatch.setattr(prereqs, "detect_agent", lambda: next(states))

    def _force() -> bool:
        calls["force"] += 1
        return True

    monkeypatch.setattr(prereqs, "force_reinstall_agent", _force)
    # self-update path must be skipped because version < AGENT_HARD_FLOOR
    monkeypatch.setattr(prereqs, "_run", lambda *a, **kw: pytest.fail("should force, not update"))

    assert prereqs.ensure_agent(assume_yes=True, allow_install=True) is True
    assert calls["force"] == 1


# ---------------------------------------------------------------------------
# opencode update fallbacks
# ---------------------------------------------------------------------------


def test_update_opencode_primary_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        prereqs,
        "detect_opencode",
        lambda: prereqs.ToolStatus("opencode", True, "/fake/opencode", "1.0.0", True, "curl"),
    )
    monkeypatch.setattr(prereqs, "_run", lambda cmd, what: cmd[1] == "upgrade")
    assert prereqs.update_opencode() is True


def test_update_opencode_missing_installs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        prereqs,
        "detect_opencode",
        lambda: prereqs.ToolStatus("opencode", False, None, None, False),
    )
    monkeypatch.setattr(prereqs, "install_opencode", lambda: True)
    assert prereqs.update_opencode() is True


# ---------------------------------------------------------------------------
# ensure_prerequisites aggregate
# ---------------------------------------------------------------------------


def test_ensure_prerequisites_requires_both(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prereqs, "ensure_agent", lambda **kw: True)
    monkeypatch.setattr(prereqs, "ensure_opencode", lambda **kw: False)
    assert prereqs.ensure_prerequisites(assume_yes=True, allow_install=True) is False

    monkeypatch.setattr(prereqs, "ensure_opencode", lambda **kw: True)
    assert prereqs.ensure_prerequisites(assume_yes=True, allow_install=True) is True
