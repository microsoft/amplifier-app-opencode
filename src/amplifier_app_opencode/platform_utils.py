"""Platform detection and OS-specific conventions.

The lifecycle machinery (``prereqs.py``) needs to know three things that
differ across operating systems:

  1. **What OS are we on?**  darwin / linux / wsl / windows. WSL is treated
     as its own category because although it *is* Linux, its gotchas (the
     opencode installer only edits interactive-shell rc files; config lives
     in the WSL filesystem, not the Windows host) matter for messaging.

  2. **Can we run the bash one-line installers?**  amplifier-agent's
     ``install.sh`` and opencode's ``opencode.ai/install`` are both bash
     scripts. They run on macOS, Linux, and WSL, but NOT in a native-Windows
     shell (cmd.exe / PowerShell). On native Windows we fall back to
     package managers (npm / scoop / choco) or route the user to WSL.

  3. **Where do transient files go?**  The original code hardcoded
     ``/tmp/...`` for the server log and PID file, which does not exist on
     native Windows. We resolve those through :func:`temp_dir` instead.

This module has no dependency on the rest of the package so it can be
imported from anywhere without cycles.
"""

from __future__ import annotations

import platform
import shutil
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# OS classification
# ---------------------------------------------------------------------------


def is_windows() -> bool:
    """True on native Windows (cmd.exe / PowerShell), NOT WSL."""
    return sys.platform.startswith("win")


def is_darwin() -> bool:
    """True on macOS."""
    return sys.platform == "darwin"


def is_wsl() -> bool:
    """True when running inside Windows Subsystem for Linux.

    Detected via the ``microsoft`` / ``wsl`` marker the WSL kernel writes
    into its release string (``platform.uname().release`` /
    ``/proc/version``). WSL reports ``sys.platform == "linux"`` so we must
    sniff the kernel string to tell it apart from bare-metal Linux.
    """
    if not sys.platform.startswith("linux"):
        return False
    release = platform.uname().release.lower()
    if "microsoft" in release or "wsl" in release:
        return True
    # Fallback: some WSL2 kernels omit the marker from uname; check /proc.
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


def os_label() -> str:
    """One of ``"darwin"``, ``"wsl"``, ``"linux"``, ``"windows"``."""
    if is_darwin():
        return "darwin"
    if is_windows():
        return "windows"
    if is_wsl():
        return "wsl"
    return "linux"


def can_run_bash_installer() -> bool:
    """True when the bash ``curl | bash`` installers can run here.

    macOS, Linux, and WSL qualify. Native Windows does not (no POSIX shell
    by default; the amplifier-agent/opencode install scripts are bash).
    """
    return not is_windows()


# ---------------------------------------------------------------------------
# Transient file locations (Windows-safe replacements for hardcoded /tmp)
# ---------------------------------------------------------------------------


def temp_dir() -> Path:
    """Return the platform temp directory (``/tmp`` on POSIX, ``%TEMP%`` on Windows)."""
    return Path(tempfile.gettempdir())


def server_log_path() -> Path:
    """Where amplifier-agent's spawned-server stdout/stderr is appended."""
    return temp_dir() / "amplifier-agent.log"


# ---------------------------------------------------------------------------
# Package-manager / install-method detection
# ---------------------------------------------------------------------------


def opencode_install_method(binary_path: str | None) -> str:
    """Infer how an existing opencode binary was installed from its path.

    ``opencode upgrade`` auto-detects its own install method, so we rarely
    *need* this -- but native-Windows auto-detection is known to fail
    (returns "unknown"), and choosing an *install* method for a missing
    binary requires knowing what's available. Returns one of:
    ``"curl"``, ``"brew"``, ``"npm"``, ``"scoop"``, ``"choco"``,
    ``"unknown"``.
    """
    if not binary_path:
        return "unknown"
    p = binary_path.replace("\\", "/").lower()
    if "/.opencode/bin" in p:
        return "curl"
    if "cellar" in p or "/homebrew/" in p or "linuxbrew" in p:
        return "brew"
    # ``p`` has already had every backslash rewritten to ``/`` above, so a
    # ``\npm\`` probe here could never match -- match the normalized form only.
    if "node_modules" in p or "/npm/" in p:
        return "npm"
    if "scoop" in p:
        return "scoop"
    if "chocolatey" in p:
        return "choco"
    return "unknown"


def has(binary: str) -> bool:
    """True if ``binary`` is resolvable on PATH."""
    return shutil.which(binary) is not None


def preferred_opencode_install_method() -> str | None:
    """Pick the best available method to INSTALL opencode fresh on this host.

    - macOS/Linux/WSL: the official bash installer (``curl``) is the
      canonical path and needs nothing but curl.
    - native Windows: prefer npm if node is present, else scoop, else choco.
      Returns ``None`` when nothing usable is found (caller routes to WSL
      guidance).
    """
    if can_run_bash_installer():
        if has("curl"):
            return "curl"
        if has("brew"):
            return "brew"
        if has("npm"):
            return "npm"
        return None
    # native Windows
    if has("npm"):
        return "npm"
    if has("scoop"):
        return "scoop"
    if has("choco"):
        return "choco"
    return None


def is_interactive() -> bool:
    """True when we have a real TTY on stdin AND stdout (safe to prompt)."""
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (ValueError, OSError):
        return False
