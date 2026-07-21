"""Unit tests for platform_utils -- OS classification and install-method logic."""

from __future__ import annotations

import pytest

from amplifier_app_opencode import platform_utils as plat


def test_os_label_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plat.sys, "platform", "darwin")
    assert plat.os_label() == "darwin"
    assert plat.is_darwin() is True
    assert plat.is_windows() is False
    assert plat.can_run_bash_installer() is True


def test_os_label_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plat.sys, "platform", "win32")
    assert plat.os_label() == "windows"
    assert plat.is_windows() is True
    # Native Windows cannot run the bash one-line installers.
    assert plat.can_run_bash_installer() is False


def test_os_label_wsl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plat.sys, "platform", "linux")
    monkeypatch.setattr(plat, "is_wsl", lambda: True)
    assert plat.os_label() == "wsl"
    # WSL *can* run bash installers (it is Linux underneath).
    assert plat.can_run_bash_installer() is True


def test_temp_paths_use_tempdir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plat.tempfile, "gettempdir", lambda: "/somewhere/tmp")
    assert str(plat.server_log_path()) == "/somewhere/tmp/amplifier-agent.log"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/Users/x/.opencode/bin/opencode", "curl"),
        ("/opt/homebrew/Cellar/opencode/1.0/bin/opencode", "brew"),
        ("/usr/lib/node_modules/opencode-ai/bin/opencode", "npm"),
        ("C:\\Users\\x\\scoop\\shims\\opencode.exe", "scoop"),
        ("C:\\ProgramData\\chocolatey\\bin\\opencode.exe", "choco"),
        ("/random/place/opencode", "unknown"),
        (None, "unknown"),
    ],
)
def test_opencode_install_method(path: str | None, expected: str) -> None:
    assert plat.opencode_install_method(path) == expected


def test_preferred_method_prefers_curl_on_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plat, "can_run_bash_installer", lambda: True)
    monkeypatch.setattr(plat, "has", lambda b: b == "curl")
    assert plat.preferred_opencode_install_method() == "curl"


def test_preferred_method_windows_npm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plat, "can_run_bash_installer", lambda: False)
    monkeypatch.setattr(plat, "has", lambda b: b == "npm")
    assert plat.preferred_opencode_install_method() == "npm"


def test_preferred_method_windows_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plat, "can_run_bash_installer", lambda: False)
    monkeypatch.setattr(plat, "has", lambda b: False)
    assert plat.preferred_opencode_install_method() is None
