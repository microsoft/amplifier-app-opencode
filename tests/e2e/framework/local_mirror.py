"""Deliver the developer's LOCAL working trees to the DTU via a Gitea mirror.

By default the DTU install script installs ``amplifier-agent`` and
``amplifier-app-opencode`` from their upstream GitHub URLs. This module lets a run
install BOTH from the developer's LOCAL (possibly uncommitted) working trees
instead, so an E2E run reflects work that has not yet landed upstream.

Mechanism:

1. Stand up (or reuse) a long-lived Gitea container via the ``amplifier-gitea``
   CLI. This harness uses a DEDICATED name/port (``oc-e2e`` / ``10130``) so it
   never collides with the E2E agent harness (``aa-e2e``/``10110``) or the eval
   harness (``aa-eval``/``10120``).
2. For each ``repo_name -> local_path``: ensure the repo exists in Gitea, then
   snapshot the local working tree (committed + staged + unstaged + untracked,
   minus gitignored, plus tracked deletions) into a throwaway clone and
   force-push it to the mirror's ``main`` branch, WITHOUT ever mutating the
   source repo.
3. Return ``{"GITEA_URL": ..., "GITEA_TOKEN": ...}``. The profile references
   ``${GITEA_URL}`` in its ``url_rewrites`` rules and declares
   ``token_var: GITEA_TOKEN``; passing these as DTU launch ``--var`` values is
   what activates the rewrite so the GitHub install URLs resolve to the mirror.

Self-contained on purpose: stdlib + subprocess only. On ANY failure this raises
``LocalMirrorError`` -- it NEVER silently falls back to upstream, because a silent
fallback would make an E2E run lie about which code it ran.

Submodule note: both source repos are git SUBMODULES (their ``.git`` is a FILE
pointing into the superproject's ``.git/modules/...``). ``git clone --local
--no-hardlinks <submodule-worktree>`` follows that ``.git`` file correctly and
produces a usable clone -- validated empirically -- so no special handling is
needed beyond what ``_snapshot_push`` already does.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .progress import log

# Dedicated Gitea coordinates for the opencode E2E harness. Kept DISTINCT from the
# agent E2E harness (aa-e2e/10110) and the eval harness (aa-eval/10120) so all three
# can run side by side without colliding.
GITEA_NAME = "oc-e2e"
GITEA_PORT = 10130


class LocalMirrorError(RuntimeError):
    """Raised when a gitea/git subprocess fails or returns unexpected output."""


def _run(
    argv: list[str], *, cwd: str | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run a command, capturing text output. Raises LocalMirrorError on failure."""
    proc = subprocess.run(argv, capture_output=True, text=True, cwd=cwd)
    if check and proc.returncode != 0:
        raise LocalMirrorError(
            f"command failed ({proc.returncode}): {' '.join(argv)}\nstderr:\n{proc.stderr}"
        )
    return proc


def _run_json(argv: list[str], *, cwd: str | None = None) -> Any:
    """Run a command and parse its stdout as JSON."""
    proc = _run(argv, cwd=cwd)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise LocalMirrorError(f"expected JSON from {' '.join(argv)}, got:\n{proc.stdout}") from exc


def _q(value: str) -> str:
    """Minimal shell quoting for paths embedded in a bash -c pipeline."""
    return "'" + value.replace("'", "'\\''") + "'"


def _ensure_gitea(name: str = GITEA_NAME, port: int = GITEA_PORT) -> dict[str, Any]:
    """Ensure a running Gitea container named ``name`` exists; return coordinates.

    Reuses an existing running container with a matching name, else creates one.
    Always mints a fresh token (Gitea does not store token values) and reads the
    current mapped port from ``status``.

    Returns a dict with keys ``id``, ``port``, ``token``.
    """
    entries = _run_json(["amplifier-gitea", "list"])
    found: dict[str, Any] | None = None
    if isinstance(entries, list):
        for entry in entries:
            if entry.get("name") == name and entry.get("container_running"):
                found = entry
                break

    if found is not None:
        log(f"local-mirror: reusing running gitea '{name}' (id={found['id']})")
        match: dict[str, Any] = found
    else:
        log(f"local-mirror: creating gitea '{name}' on port {port} (pulls image on first run)...")
        match = _run_json(["amplifier-gitea", "create", "--port", str(port), "--name", name])
        log(f"local-mirror: gitea created (id={match['id']})")

    gitea_id = match["id"]

    # Read authoritative mapped port from status.
    status = _run_json(["amplifier-gitea", "status", gitea_id])
    resolved_port = status.get("port", match.get("port", port))

    token_info = _run_json(["amplifier-gitea", "token", gitea_id])
    token = token_info["token"]

    return {"id": gitea_id, "port": resolved_port, "token": token}


def _ensure_repo(gitea_port: int, token: str, repo: str) -> None:
    """Create the Gitea repo if it does not already exist (ignore 409 conflicts)."""
    url = f"http://localhost:{gitea_port}/api/v1/user/repos"
    payload = json.dumps(
        {"name": repo, "private": False, "auto_init": False, "default_branch": "main"}
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"token {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        urllib.request.urlopen(request, timeout=30)
    except urllib.error.HTTPError as exc:
        if exc.code == 409:
            return  # already exists
        raise LocalMirrorError(
            f"gitea repo create failed ({exc.code}) for {repo}: "
            f"{exc.read().decode(errors='replace')}"
        ) from exc
    except urllib.error.URLError as exc:
        raise LocalMirrorError(f"gitea repo create request failed for {repo}: {exc}") from exc


def _snapshot_push(local_repo_path: str, gitea_port: int, token: str, repo: str) -> None:
    """Force-push a snapshot of the local working tree to the Gitea repo.

    Implements the gitea-skill snapshot pattern WITHOUT mutating the source repo:

    1. ``git clone --local`` the source into a temp dir (follows a submodule
       ``.git`` file correctly; validated empirically).
    2. Overlay the working set (cached + modified + untracked, minus gitignored)
       into the clone via rsync.
    3. Delete files that are tracked-but-deleted in the source.
    4. Commit (allow-empty) and force-push HEAD to refs/heads/main.

    Raises LocalMirrorError on ANY failure -- never falls back to the source tree.
    """
    log(f"local-mirror: pushing working-tree snapshot of {repo}...")
    src = str(Path(local_repo_path).expanduser().resolve())
    snap_dir = tempfile.mkdtemp(prefix=f"oc-e2e-snap-{repo}-")
    snap = str(Path(snap_dir) / "repo")

    try:
        _run(["git", "clone", "--local", "--no-hardlinks", src, snap])

        # Overlay the exact working set into the clone. ls-files -z gives NUL-delimited
        # paths; rsync --files-from=- --from0 reads that list. Run as one bash pipeline.
        overlay = (
            f"git -C {_q(src)} ls-files -z --cached --modified --others --exclude-standard "
            f"| rsync -a --files-from=- --from0 {_q(src)}/ {_q(snap)}/"
        )
        _run(["bash", "-c", overlay])

        # Remove files deleted in the working tree but still tracked.
        deleted = _run(["git", "-C", src, "ls-files", "-z", "--deleted"])
        for rel in filter(None, deleted.stdout.split("\0")):
            target = Path(snap) / rel
            target.unlink(missing_ok=True)

        # Commit the snapshot inside the clone (never the source).
        _run(
            [
                "git",
                "-C",
                snap,
                "-c",
                "user.email=snapshot@local",
                "-c",
                "user.name=Snapshot",
                "add",
                "-A",
            ]
        )
        _run(
            [
                "git",
                "-C",
                snap,
                "-c",
                "user.email=snapshot@local",
                "-c",
                "user.name=Snapshot",
                "commit",
                "--allow-empty",
                "-m",
                "working-tree snapshot",
            ]
        )
        push_url = f"http://admin:{token}@localhost:{gitea_port}/admin/{repo}.git"
        _run(
            [
                "git",
                "-C",
                snap,
                "-c",
                "credential.helper=",
                "push",
                "--force",
                push_url,
                "HEAD:refs/heads/main",
            ]
        )
    finally:
        shutil.rmtree(snap_dir, ignore_errors=True)


def ensure_local_mirror(repos: dict[str, str]) -> dict[str, str]:
    """Mirror LOCAL working trees of ``repos`` into Gitea; return launch vars.

    Stands up (or reuses) the dedicated ``oc-e2e`` Gitea and, for each
    ``repo_name -> local_path`` entry, ensures the Gitea repo exists and
    force-pushes a snapshot of that repo's working tree (committed + staged +
    unstaged + untracked, minus gitignored, plus tracked deletions) without
    mutating the source.

    Args:
        repos: Mapping of Gitea repo name -> local repo path. The repo name must
            match the last path segment of the GitHub URL the profile rewrites
            (e.g. ``amplifier-agent`` -> ``admin/amplifier-agent``).

    Returns:
        ``{"GITEA_URL": "http://localhost:<port>", "GITEA_TOKEN": "<token>"}``.
        These names matter: the profile references ``${GITEA_URL}`` and declares
        ``token_var: GITEA_TOKEN``, and passing them as DTU launch ``--var`` values
        activates the profile's ``url_rewrites`` so both tools install from the
        mirror instead of upstream GitHub.

    Raises:
        LocalMirrorError: on any gitea/git failure, or if a repo path is missing
            or not a git repo. Never silently falls back to upstream.
    """
    if not repos:
        raise LocalMirrorError("ensure_local_mirror called with no repos")

    # Validate all paths up front (fail fast before touching Gitea).
    resolved: dict[str, Path] = {}
    for name, path in repos.items():
        src = Path(path).expanduser().resolve()
        if not src.exists():
            raise LocalMirrorError(f"repo path does not exist for '{name}': {src}")
        if not (src / ".git").exists():
            raise LocalMirrorError(f"not a git repo (no .git) for '{name}': {src}")
        resolved[name] = src

    gitea = _ensure_gitea()
    port = int(gitea["port"])
    token = str(gitea["token"])

    for name, src in resolved.items():
        _ensure_repo(port, token, name)
        _snapshot_push(str(src), port, token, name)
        log(f"local-mirror: mirrored {name} <- {src}")

    gitea_url = f"http://localhost:{port}"
    log(f"local-mirror: {len(resolved)} repo(s) mirrored -> {gitea_url}")
    return {"GITEA_URL": gitea_url, "GITEA_TOKEN": token}


def teardown_local_mirror(name: str = GITEA_NAME) -> None:
    """Destroy every Gitea env named ``name`` (best-effort; never raises).

    Called at teardown to clean up the dedicated ``oc-e2e`` Gitea this harness
    stood up. Matches ONLY by the ``oc-e2e`` name so it never touches other Gitea
    envs (aa-e2e, aa-eval, ...). Teardown must NEVER fail a run: any error while
    listing or destroying is swallowed with a concise warning line.
    """
    try:
        entries = _run_json(["amplifier-gitea", "list"])
    except Exception as exc:
        log(f"local-mirror: warning: could not list gitea envs to tear down: {exc}")
        return

    matches = (
        [entry for entry in entries if isinstance(entry, dict) and entry.get("name") == name]
        if isinstance(entries, list)
        else []
    )

    if not matches:
        log(f"local-mirror: no gitea env '{name}' to tear down")
        return

    for entry in matches:
        env_id = entry.get("id")
        try:
            _run(["amplifier-gitea", "destroy", str(env_id)])
            log(f"local-mirror: torn down gitea env '{name}' ({env_id})")
        except Exception as exc:
            log(f"local-mirror: warning: failed to destroy gitea env '{name}' ({env_id}): {exc}")


__all__ = [
    "GITEA_NAME",
    "GITEA_PORT",
    "LocalMirrorError",
    "ensure_local_mirror",
    "teardown_local_mirror",
]
