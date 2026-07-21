"""Thin subprocess wrappers over the ``amplifier-digital-twin`` CLI.

Deliberately has NO dependency on the amplifier-tester bundle. Every shell-out uses
``subprocess.run(capture_output=True, text=True)``; the ``exec_json`` path parses the
DTU exec JSON envelope (``{id, command, exit_code, stdout, stderr}``) from stdout.

Responsibilities: launch / poll-readiness / exec / file-push / destroy a Digital Twin
instance. The TUI driver (``driver.py``) shells ``amplifier-digital-twin exec ... -- tmux``
directly; this module covers the lifecycle verbs the harness needs.
"""

from __future__ import annotations

import json
import subprocess
import time
from typing import Any

from .progress import log


class DTUError(RuntimeError):
    """Raised when an amplifier-digital-twin subprocess fails or returns bad output."""


def _run(argv: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a command, capturing text output. Raises DTUError on non-zero when check."""
    proc = subprocess.run(argv, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise DTUError(
            f"command failed ({proc.returncode}): {' '.join(argv)}\nstderr:\n{proc.stderr}"
        )
    return proc


def _run_json(argv: list[str]) -> Any:
    """Run a command and parse its stdout as JSON."""
    proc = _run(argv)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise DTUError(f"expected JSON from {' '.join(argv)}, got:\n{proc.stdout}") from exc


def launch(profile_path: str, varmap: dict[str, str], name: str | None = None) -> str:
    """Launch a DTU instance from a profile; return the instance id.

    Args:
        profile_path: Path to the DTU profile YAML.
        varmap: ``--var KEY=VALUE`` pairs supplied at launch.
        name: Optional instance name.

    Returns:
        The launched instance id (the profile launch dict's top-level ``id``).
    """
    log(f"dtu: launching '{name or profile_path}' (creates container, installs stack; ~1-2 min)...")
    argv = ["amplifier-digital-twin", "launch", profile_path]
    if name:
        argv += ["--name", name]
    for key, value in varmap.items():
        argv += ["--var", f"{key}={value}"]
    result = _run_json(argv)
    dtu_id = result.get("id")
    if not isinstance(dtu_id, str):
        raise DTUError(f"launch returned no id: {result!r}")
    log(f"dtu: launched (id={dtu_id})")
    return dtu_id


def update(dtu_id: str, varmap: dict[str, str]) -> None:
    """Re-run the profile's ``update`` section in place, re-applying ``--var`` values.

    Routes through the DTU engine's ``update`` verb (NOT a raw ``exec`` of the install
    script) so the engine re-applies the profile's ``url_rewrites`` during the update
    commands. This is what makes an in-place refresh install from the local Gitea mirror
    rather than from GitHub. ``--skip-readiness`` is not passed; readiness is re-checked.
    """
    log(f"dtu: updating '{dtu_id}' in place (re-applies url_rewrites)...")
    argv = ["amplifier-digital-twin", "update", dtu_id]
    for key, value in varmap.items():
        argv += ["--var", f"{key}={value}"]
    _run(argv)


def check_ready(dtu_id: str) -> bool:
    """Return True if the instance is ready. Readiness is the EXIT CODE (0 ready)."""
    proc = _run(["amplifier-digital-twin", "check-readiness", dtu_id], check=False)
    return proc.returncode == 0


def wait_ready(dtu_id: str, timeout: int = 600, interval: int = 10) -> None:
    """Poll ``check_ready`` until ready or timeout. Never a single long blocking call."""
    log(f"dtu: waiting for '{dtu_id}' readiness (polling every {interval}s, timeout {timeout}s)...")
    start = time.monotonic()
    deadline = start + timeout
    while time.monotonic() < deadline:
        if check_ready(dtu_id):
            log(f"dtu: '{dtu_id}' ready after {time.monotonic() - start:.0f}s")
            return
        time.sleep(interval)
    raise TimeoutError(f"DTU {dtu_id} not ready within {timeout}s")


def exec(dtu_id: str, argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a command inside the DTU; return the raw host CompletedProcess.

    The stdout is the DTU exec JSON envelope. Callers wanting the parsed inner
    result should use ``exec_json``.
    """
    full = ["amplifier-digital-twin", "exec", dtu_id, "--", *argv]
    return _run(full, check=False)


def exec_json(dtu_id: str, argv: list[str]) -> dict[str, Any]:
    """Run a command inside the DTU; return ``{id, command, exit_code, stdout, stderr}``.

    For chained shell use ``argv=["bash", "-lc", "..."]``.
    """
    full = ["amplifier-digital-twin", "exec", dtu_id, "--", *argv]
    result = _run_json(full)
    if not isinstance(result, dict):
        raise DTUError(f"exec_json expected an object, got: {result!r}")
    return result


def file_push(dtu_id: str, src: str, dest: str, *, recursive: bool = False) -> None:
    """Push a local file or directory into the DTU at ``dest`` (parents auto-created)."""
    log(f"dtu: pushing {src} -> {dest}")
    argv = ["amplifier-digital-twin", "file-push", dtu_id]
    if recursive:
        argv.append("--recursive")
    argv += [src, dest]
    _run(argv)


def destroy(dtu_id: str) -> None:
    """Destroy the given DTU instance (best-effort)."""
    log(f"dtu: destroying '{dtu_id}'...")
    _run(["amplifier-digital-twin", "destroy", dtu_id], check=False)


def list_instances() -> list[dict[str, Any]]:
    """Return the list of DTU instances."""
    result = _run_json(["amplifier-digital-twin", "list"])
    return result if isinstance(result, list) else []
