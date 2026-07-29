"""Prerequisite lifecycle management for the amplifier-opencode stack.

The user-facing promise is: *install one thing, run one thing, and the whole
stack works.* That means amplifier-opencode owns the install/update lifecycle
of the three components it needs:

  1. **amplifier-agent**  -- the OpenAI-compatible backend server (a uv tool).
  2. **opencode**         -- the TUI (a native binary; many install methods).
  3. **amplifier-app-opencode** (self) -- this adapter (a uv tool).

Every place the tool would historically have said *"go install X yourself"*
is replaced here with an idempotent **ensure** operation:

  - present and healthy  -> report and skip
  - missing              -> install
  - present but stale     -> update, and if the component's own updater
                            fails or the version is below the hard floor,
                            **force-reinstall** rather than punt to the user

Design boundary: this module contains ALL lifecycle logic. ``cli.py`` stays a
thin wiring layer. ``core.py``-style discovery/config-write logic (still in
``cli.py`` today) is a separate concern and untouched.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, distribution
from typing import Any

import click

from . import platform_utils as plat

# ---------------------------------------------------------------------------
# Constants -- package names, repos, installers, version floors
# ---------------------------------------------------------------------------

PACKAGE_NAME = "amplifier-app-opencode"
REPO_URL = "https://github.com/microsoft/amplifier-app-opencode.git"

AGENT_BIN = "amplifier-agent"
AGENT_PACKAGE = "amplifier-agent"
AGENT_REPO = "https://github.com/microsoft/amplifier-agent"
AGENT_INSTALL_SH = "https://raw.githubusercontent.com/microsoft/amplifier-agent/main/install.sh"

OPENCODE_BIN = "opencode"
OPENCODE_INSTALL_SH = "https://opencode.ai/install"
OPENCODE_NPM_PACKAGE = "opencode-ai"

# Minimum amplifier-agent version amplifier-opencode requires. >= 0.11.0 is the
# first version that namespaces reseller model ids (`github-copilot/<model>`).
# Below it, Copilot serves `claude-sonnet-5` and `claude-opus-5` under ids
# byte-identical to the native anthropic provider's, and whichever provider is
# enumerated last silently captures the other's traffic. Since we document the
# Copilot setup, the floor has to be the version where that is safe.
# (0.10.0 remains the floor for `GET /v1/skills` and `GET /v1/modes`, which the
# skills and modes bridges read; 0.9.3 for `auth set --stdin`, which onboarding
# uses to hand the provider key to the agent off-argv. 0.11.0 subsumes both.)
MIN_AGENT_VERSION = "0.11.0"
# The silent, launch-time auto-install/self-heal targets this exact known-good
# git tag rather than a moving branch, so a reliability tool never drags users
# onto un-vetted ``main``. Kept in lockstep with MIN_AGENT_VERSION: to adopt a
# newer agent, bump both in one deliberate PR (after the tag is cut upstream).
AGENT_PINNED_REF = f"v{MIN_AGENT_VERSION}"
# Below this floor the agent's own ``update`` subcommand is unreliable/absent,
# so we skip it and go straight to a forced reinstall. Kept equal to the
# required minimum: anything under the minimum is "too old to trust its self
# update", so we heal it forcefully.
AGENT_HARD_FLOOR = MIN_AGENT_VERSION


# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------


def extract_semver(text: str) -> tuple[int, int, int] | None:
    """Pull the first ``X.Y.Z`` out of an arbitrary version string.

    ``amplifier-agent, version 0.9.1`` -> ``(0, 9, 1)``;
    ``1.17.20`` -> ``(1, 17, 20)``.
    """
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def version_ge(have: str, want: str) -> bool:
    """True when semver ``have`` >= ``want``. Unparseable -> False (fail closed)."""
    h = extract_semver(have)
    w = extract_semver(want)
    if h is None or w is None:
        return False
    return h >= w


def binary_version(binary: str, version_flag: str = "--version") -> str:
    """Best-effort first-line version string for a binary. ``"?"`` on failure."""
    try:
        r = subprocess.run(
            [binary, version_flag],
            capture_output=True,
            text=True,
            timeout=10.0,
        )
        return (r.stdout or r.stderr).strip().splitlines()[0]
    except (OSError, subprocess.TimeoutExpired, IndexError):
        return "?"


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


@dataclass
class ToolStatus:
    """Snapshot of one managed component."""

    name: str
    present: bool
    path: str | None
    version: str | None
    meets_min: bool
    install_method: str | None = None

    @property
    def summary(self) -> str:
        if not self.present:
            return f"{self.name}: not installed"
        via = f", via {self.install_method}" if self.install_method else ""
        floor = "" if self.meets_min else "  (BELOW required minimum)"
        return f"{self.name} {self.version} at {self.path}{via}{floor}"


def detect_agent() -> ToolStatus:
    """Detect amplifier-agent presence + version + min-version compliance."""
    path = shutil.which(AGENT_BIN)
    if not path:
        return ToolStatus(AGENT_BIN, present=False, path=None, version=None, meets_min=False)
    version = binary_version(AGENT_BIN)
    return ToolStatus(
        AGENT_BIN,
        present=True,
        path=path,
        version=version,
        meets_min=version_ge(version, MIN_AGENT_VERSION),
        install_method="uv",
    )


def detect_opencode() -> ToolStatus:
    """Detect opencode presence + version + inferred install method.

    opencode has no hard minimum for this adapter (any version reads a static
    provider config), so ``meets_min`` is simply ``present``.
    """
    path = shutil.which(OPENCODE_BIN)
    if not path:
        return ToolStatus(OPENCODE_BIN, present=False, path=None, version=None, meets_min=False)
    version = binary_version(OPENCODE_BIN)
    return ToolStatus(
        OPENCODE_BIN,
        present=True,
        path=path,
        version=version,
        meets_min=True,
        install_method=plat.opencode_install_method(path),
    )


# ---------------------------------------------------------------------------
# Provider credential report (single source of truth = the agent itself)
# ---------------------------------------------------------------------------


def fetch_provider_report(binary: str | None = None) -> dict[str, Any] | None:
    """Return ``amplifier-agent providers list --json`` output, or ``None``.

    The agent is the only thing that truthfully knows which providers it will
    auto-enable at ``serve`` startup (env var, then credentials.json), so we
    ask it rather than re-implement resolution.
    """
    binary = binary or shutil.which(AGENT_BIN)
    if not binary:
        return None
    try:
        r = subprocess.run(
            [binary, "providers", "list", "--json"],
            capture_output=True,
            text=True,
            timeout=15.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    try:
        payload = json.loads(r.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("providers"), list):
        return None
    # Drop any non-dict rows so every downstream ``row.get(...)`` is safe.
    # A malformed row degrades to "one fewer provider", never an AttributeError.
    payload["providers"] = [row for row in payload["providers"] if isinstance(row, dict)]
    return payload


def has_resolvable_provider(binary: str | None = None) -> bool | None:
    """True/False if any provider is resolvable, or ``None`` if unknowable."""
    report = fetch_provider_report(binary)
    if report is None:
        return None
    return any(row.get("resolvable") for row in report.get("providers", []))


# ---------------------------------------------------------------------------
# Self (amplifier-app-opencode) install-source introspection (PEP 610)
# ---------------------------------------------------------------------------


def get_self_install_info() -> dict[str, Any]:
    """Detect how amplifier-app-opencode was installed (git / editable / pypi)."""
    info: dict[str, Any] = {"source": "unknown", "version": "?", "commit": None, "url": None}
    try:
        dist = distribution(PACKAGE_NAME)
        info["version"] = dist.metadata["Version"]
        du_text = dist.read_text("direct_url.json")
        if du_text:
            du = json.loads(du_text)
            if "vcs_info" in du:
                info["source"] = "git"
                info["commit"] = du["vcs_info"].get("commit_id", "") or None
                info["url"] = du.get("url", "") or None
            elif (du.get("dir_info") or {}).get("editable"):
                info["source"] = "editable"
            else:
                info["source"] = "unknown"
        else:
            info["source"] = "pypi"
    except PackageNotFoundError:
        pass
    return info


# ---------------------------------------------------------------------------
# Low-level runners
# ---------------------------------------------------------------------------


def _require_uv() -> str:
    uv = shutil.which("uv")
    if not uv:
        raise click.ClickException(
            "uv is required but not on PATH. Install it from https://docs.astral.sh/uv/ and re-run."
        )
    return uv


def _run(cmd: list[str], *, what: str) -> bool:
    """Run a command streaming output; return True on exit 0, False otherwise."""
    click.secho(f"    $ {' '.join(cmd)}", fg="bright_black")
    try:
        result = subprocess.run(cmd)
    except OSError as exc:
        click.secho(f"    ! {what} could not run: {exc}", fg="yellow")
        return False
    if result.returncode != 0:
        click.secho(f"    ! {what} exited {result.returncode}", fg="yellow")
        return False
    return True


def _run_bash_pipe(url: str, *, what: str) -> bool:
    """Run ``curl -fsSL <url> | bash``. Only valid where bash installers work."""
    if not plat.can_run_bash_installer():
        click.secho(
            f"    ! {what}: bash installers are not supported on native Windows", fg="yellow"
        )
        return False
    curl = shutil.which("curl")
    bash = shutil.which("bash")
    if not curl or not bash:
        click.secho(f"    ! {what}: need both curl and bash on PATH", fg="yellow")
        return False
    click.secho(f"    $ curl -fsSL {url} | bash", fg="bright_black")
    try:
        curl_proc = subprocess.Popen([curl, "-fsSL", url], stdout=subprocess.PIPE)
        bash_proc = subprocess.Popen([bash], stdin=curl_proc.stdout)
        if curl_proc.stdout:
            curl_proc.stdout.close()
        rc = bash_proc.wait()
        # Must also check curl: with `-fsSL` a failed fetch (404, TLS, network)
        # exits non-zero with empty stdout, so bash reads nothing and exits 0.
        # Checking only bash would report success on a silently-failed download.
        curl_rc = curl_proc.wait()
    except OSError as exc:
        click.secho(f"    ! {what} could not run: {exc}", fg="yellow")
        return False
    if curl_rc != 0:
        click.secho(f"    ! {what}: download failed (curl exited {curl_rc})", fg="yellow")
        return False
    return rc == 0


# ---------------------------------------------------------------------------
# amplifier-agent install / update / force-heal
# ---------------------------------------------------------------------------


def install_agent() -> bool:
    """Install amplifier-agent fresh. Prefers uv (cross-platform incl. Windows)."""
    click.secho("  Installing amplifier-agent ...", fg="cyan")
    uv = _require_uv()
    ok = _run(
        [uv, "tool", "install", "--from", f"git+{AGENT_REPO}@{AGENT_PINNED_REF}", AGENT_PACKAGE],
        what="uv tool install amplifier-agent",
    )
    if ok:
        return True
    # Secondary path: the official bash installer (mac/linux/wsl only).
    if plat.can_run_bash_installer():
        click.secho("  Retrying via the official install.sh ...", fg="cyan")
        return _run_bash_pipe(AGENT_INSTALL_SH, what="amplifier-agent install.sh")
    return False


def force_reinstall_agent() -> bool:
    """Force a clean reinstall of amplifier-agent to the pinned known-good tag."""
    click.secho(f"  Force-reinstalling amplifier-agent to {AGENT_PINNED_REF} ...", fg="cyan")
    uv = _require_uv()
    return _run(
        [
            uv,
            "tool",
            "install",
            "--force",
            "--from",
            f"git+{AGENT_REPO}@{AGENT_PINNED_REF}",
            AGENT_PACKAGE,
        ],
        what="uv tool install --force amplifier-agent",
    )


def ensure_agent(*, assume_yes: bool, allow_install: bool) -> bool:
    """Ensure amplifier-agent is present and >= MIN_AGENT_VERSION.

    Returns True when the agent ends up healthy. Never dead-ends with a
    "do it yourself" message: missing -> install; stale -> update then
    force-reinstall if needed.
    """
    status = detect_agent()

    if status.present and status.meets_min:
        click.secho(f"  \u2713 {status.summary}", fg="green")
        return True

    if not status.present:
        if not allow_install:
            click.secho(
                f"  \u2717 amplifier-agent not installed (bootstrap disabled). "
                f"Install: uv tool install --from git+{AGENT_REPO} amplifier-agent",
                fg="red",
            )
            return False
        if not _confirm_install("amplifier-agent", assume_yes):
            return False
        if not install_agent():
            return False
        status = detect_agent()
        if status.present and status.meets_min:
            click.secho(f"  \u2713 installed {status.summary}", fg="green")
            return True
        # Freshly installed but somehow below floor -> force to latest.

    # Present but below the required minimum -> heal it. Anything below the
    # minimum is too old to trust its own `update` subcommand, so heal with a
    # forced reinstall rather than a self-update.
    #
    # Healing does not prompt (assume_yes is irrelevant here): a stale agent
    # breaks the adapter, so bringing it to the floor is the whole point of the
    # check. But when bootstrap is disabled (allow_install=False) we must not
    # reinstall behind the caller's back -- honor the same opt-out as the
    # not-present branch above and report instead.
    if not allow_install:
        click.secho(
            f"  \u2717 amplifier-agent {status.version} is below required "
            f"{MIN_AGENT_VERSION} (bootstrap disabled). "
            f"Update: uv tool install --force --from git+{AGENT_REPO} amplifier-agent",
            fg="red",
        )
        return False

    click.secho(
        f"  amplifier-agent {status.version} is below required {MIN_AGENT_VERSION}; healing ...",
        fg="yellow",
    )
    force_reinstall_agent()

    final = detect_agent()
    if final.present and final.meets_min:
        click.secho(
            f"  \u2713 amplifier-agent now {final.version} (>= {MIN_AGENT_VERSION})", fg="green"
        )
        return True
    click.secho(
        f"  \u2717 amplifier-agent is {final.version} after healing, still below "
        f"{MIN_AGENT_VERSION}. See output above.",
        fg="red",
    )
    return False


def update_agent_to_latest() -> bool:
    """Update amplifier-agent to the newest version (for the ``update`` command)."""
    status = detect_agent()
    if not status.present:
        click.secho("  amplifier-agent not installed; installing latest ...", fg="cyan")
        return install_agent()
    click.secho(f"  Updating amplifier-agent ({status.version}) ...", fg="cyan")
    # Below the hard floor the self-updater is untrusted -> force reinstall.
    if not version_ge(status.version or "0.0.0", AGENT_HARD_FLOOR):
        return force_reinstall_agent()
    if _run([status.path or AGENT_BIN, "update"], what="amplifier-agent update"):
        return True
    click.secho("  self-update failed; force-reinstalling ...", fg="yellow")
    return force_reinstall_agent()


# ---------------------------------------------------------------------------
# opencode install / update / force-heal
# ---------------------------------------------------------------------------


def install_opencode() -> bool:
    """Install opencode fresh using the best method available on this host."""
    click.secho("  Installing opencode ...", fg="cyan")
    method = plat.preferred_opencode_install_method()

    if method == "curl":
        return _run_bash_pipe(OPENCODE_INSTALL_SH, what="opencode install")
    if method == "brew":
        brew = shutil.which("brew")
        return bool(brew) and _run([brew, "install", "opencode"], what="brew install opencode")
    if method == "npm":
        npm = shutil.which("npm")
        return bool(npm) and _run(
            [npm, "install", "-g", OPENCODE_NPM_PACKAGE], what="npm install -g opencode-ai"
        )
    if method == "scoop":
        scoop = shutil.which("scoop")
        return bool(scoop) and _run([scoop, "install", "opencode"], what="scoop install opencode")
    if method == "choco":
        choco = shutil.which("choco")
        return bool(choco) and _run(
            [choco, "install", "-y", "opencode"], what="choco install opencode"
        )

    # Nothing usable -> honest guidance (native Windows without npm/scoop/choco).
    _print_opencode_manual_guidance()
    return False


def update_opencode() -> bool:
    """Update opencode via its self-detecting ``opencode upgrade``, with fallbacks."""
    status = detect_opencode()
    if not status.present:
        return install_opencode()

    click.secho(f"  Updating opencode ({status.version}) ...", fg="cyan")
    ocbin = status.path or OPENCODE_BIN

    # Primary: opencode upgrade auto-detects its own install method.
    if _run([ocbin, "upgrade"], what="opencode upgrade"):
        return True

    # Fallback 1: force the detected method (native-Windows auto-detect is
    # a known-broken case where --method is required).
    method = status.install_method
    if method and method != "unknown":
        click.secho(f"  Retrying with explicit --method {method} ...", fg="yellow")
        if _run([ocbin, "upgrade", "--method", method], what=f"opencode upgrade --method {method}"):
            return True

    # Fallback 2: re-run the installer for the detected/available method.
    click.secho("  Falling back to re-running the opencode installer ...", fg="yellow")
    return install_opencode()


def ensure_opencode(*, assume_yes: bool, allow_install: bool) -> bool:
    """Ensure opencode is present (any version is acceptable for this adapter)."""
    status = detect_opencode()
    if status.present:
        click.secho(f"  \u2713 {status.summary}", fg="green")
        return True

    if not allow_install:
        click.secho(
            "  \u2717 opencode not installed (bootstrap disabled). "
            "Install: curl -fsSL https://opencode.ai/install | bash",
            fg="red",
        )
        return False

    # Native Windows: opencode's own docs recommend WSL; be explicit.
    if plat.is_windows() and plat.preferred_opencode_install_method() is None:
        _print_windows_wsl_guidance()
        return False

    if not _confirm_install("opencode", assume_yes):
        return False
    if not install_opencode():
        return False

    final = detect_opencode()
    if final.present:
        click.secho(f"  \u2713 installed {final.summary}", fg="green")
        return True
    click.secho(
        "  \u2717 opencode still not on PATH after install. You may need to open a "
        "new terminal (the installer edits your shell rc), then re-run.",
        fg="red",
    )
    return False


# ---------------------------------------------------------------------------
# Guidance helpers
# ---------------------------------------------------------------------------


def _print_opencode_manual_guidance() -> None:
    click.secho(
        "  Could not find a usable opencode installer on this system.",
        fg="red",
    )
    click.echo("    Install one of:")
    click.echo("      - macOS/Linux/WSL:  curl -fsSL https://opencode.ai/install | bash")
    click.echo("      - Homebrew:         brew install opencode")
    click.echo("      - npm:              npm install -g opencode-ai")


def _print_windows_wsl_guidance() -> None:
    click.secho(
        "  Native Windows detected without npm/scoop/choco available.",
        fg="yellow",
    )
    click.echo(
        "    opencode's own documentation recommends running inside WSL for the "
        "best experience. Either:"
    )
    click.echo("      1. Install WSL (`wsl --install`) and run amplifier-opencode there, or")
    click.echo("      2. Install Node.js, then: npm install -g opencode-ai")


def _confirm_install(name: str, assume_yes: bool) -> bool:
    """Prompt before installing, unless --yes or non-interactive-with-yes."""
    if assume_yes:
        return True
    if not plat.is_interactive():
        click.secho(
            f"  {name} is missing and this is a non-interactive shell. "
            "Re-run with --yes to auto-install, or install it manually.",
            fg="yellow",
        )
        return False
    return click.confirm(f"  {name} is not installed. Install it now?", default=True)


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------


def ensure_prerequisites(*, assume_yes: bool, allow_install: bool) -> bool:
    """Ensure amplifier-agent and opencode are both present and healthy.

    Returns True only when BOTH are ready. Used as the launch preflight and
    as the body of the ``setup`` command.
    """
    click.secho("Checking prerequisites ...", fg="cyan", bold=True)
    agent_ok = ensure_agent(assume_yes=assume_yes, allow_install=allow_install)
    opencode_ok = ensure_opencode(assume_yes=assume_yes, allow_install=allow_install)
    return agent_ok and opencode_ok
