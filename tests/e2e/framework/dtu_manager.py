"""High-level orchestration of the warm DTU (used by cli.py and conftest.py).

Ties the profile launch and the state file into the four lifecycle verbs the harness
exposes: provision / is_warm / refresh / teardown.

By DEFAULT, provisioning installs BOTH ``amplifier-agent`` and ``amplifier-app-opencode``
from the developer's LOCAL working trees, delivered via a dedicated ``oc-e2e`` Gitea
mirror (see ``local_mirror.py``). The profile's ``url_rewrites`` block redirects the
GitHub install URLs to that mirror when ``GITEA_URL`` / ``GITEA_TOKEN`` are passed as
launch ``--var`` values. Set the ``OC_E2E_PUBLISHED`` env var (truthy) to opt OUT and
install the published upstream versions instead.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from . import dtu, local_mirror, state
from .progress import log

# tests/e2e/framework/dtu_manager.py -> framework -> e2e -> tests -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
PROVISIONING = Path(__file__).resolve().parent / "provisioning"
PROFILE_SRC = PROVISIONING / "profile.yaml"

DTU_NAME = "oc-e2e"
BASE_IMAGE = "ubuntu:24.04"

# Local working trees to mirror. The Gitea repo name (key) must match the last segment
# of the GitHub URL the profile rewrites. amplifier-app-opencode is this repo (REPO_ROOT);
# amplifier-agent is expected as a sibling checkout next to it.
AGENT_REPO = REPO_ROOT.parent / "amplifier-agent"


def _use_published() -> bool:
    """True if the caller opted out of the local mirror via OC_E2E_PUBLISHED."""
    return os.environ.get("OC_E2E_PUBLISHED", "").strip().lower() not in ("", "0", "false", "no")


def _local_repos() -> dict[str, str]:
    """Resolve the {gitea_repo_name -> local_path} map, erroring if a repo is missing."""
    if not AGENT_REPO.exists():
        raise RuntimeError(
            f"local amplifier-agent checkout not found at {AGENT_REPO}. "
            "The default E2E flow installs both repos from LOCAL code; place amplifier-agent "
            "as a sibling of amplifier-app-opencode, or set OC_E2E_PUBLISHED=1 to use published "
            "versions."
        )
    return {
        "amplifier-app-opencode": str(REPO_ROOT),
        "amplifier-agent": str(AGENT_REPO),
    }


def mirror_local_repos() -> dict[str, str]:
    """Mirror local working trees into the ``oc-e2e`` Gitea; return url_rewrite vars.

    DEFAULT behavior: stand up (or reuse) the dedicated Gitea, snapshot-push the LOCAL
    amplifier-agent + amplifier-app-opencode working trees, and return the
    ``GITEA_URL`` / ``GITEA_TOKEN`` ``--var`` values that activate the profile's
    ``url_rewrites`` so both tools install from the mirror.

    If ``OC_E2E_PUBLISHED`` is set (truthy), returns ``{}`` -- the DTU then installs the
    published upstream versions (no rewrite, no Gitea).
    """
    if _use_published():
        log("mirror: OC_E2E_PUBLISHED set -> installing PUBLISHED versions (no local mirror)")
        return {}
    log("mirror: mirroring LOCAL working trees (amplifier-app-opencode + amplifier-agent)")
    return local_mirror.ensure_local_mirror(_local_repos())


def _build_varmap() -> dict[str, str]:
    """Assemble the ``--var`` map for launch/update."""
    varmap = {"OC_E2E_BASE_IMAGE": BASE_IMAGE}
    varmap.update(mirror_local_repos())
    return varmap


def _stage_launch_dir() -> str:
    """Copy the profile + provisioning assets into a temp dir so ``./dtu/...`` resolves.

    Returns the path to the staged profile YAML.
    """
    tmp = tempfile.mkdtemp(prefix="oc-e2e-launch-")
    profile_dst = Path(tmp) / "profile.yaml"
    shutil.copyfile(PROFILE_SRC, profile_dst)

    assets_dst = Path(tmp) / "dtu"
    assets_dst.mkdir()
    for asset in PROVISIONING.iterdir():
        if asset.name == "profile.yaml":
            continue
        shutil.copyfile(asset, assets_dst / asset.name)
    return str(profile_dst)


def _find_instance(name: str) -> dict[str, Any] | None:
    """Return the DTU instance dict named ``name``, or None if it does not exist."""
    for inst in dtu.list_instances():
        if inst.get("id") == name:
            return inst
    return None


def _write_state(dtu_id: str) -> dict[str, Any]:
    new_state = {
        "dtu_id": dtu_id,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    state.write(new_state)
    return new_state


def provision() -> dict[str, Any]:
    """Provision a fresh warm DTU: destroy any existing ``oc-e2e``, then launch clean."""
    log("provision: starting fresh DTU provision")
    varmap = _build_varmap()

    existing = _find_instance(DTU_NAME)
    if existing:
        log(f"provision: existing '{DTU_NAME}' found; destroying for a clean rebuild")
        dtu.destroy(existing["id"])

    profile_path = _stage_launch_dir()
    dtu_id = dtu.launch(profile_path, varmap, name=DTU_NAME)
    dtu.wait_ready(dtu_id)
    result = _write_state(dtu_id)
    log("provision: done; DTU is warm and state written")
    return result


def is_warm() -> bool:
    """True if a state file exists and its DTU is currently ready."""
    current = state.read()
    if not current:
        return False
    try:
        return dtu.check_ready(current["dtu_id"])
    except Exception:
        return False


def refresh() -> None:
    """Re-run the profile's update step in place inside the warm DTU (no relaunch).

    Re-mirrors the LOCAL working trees first (so code changes reach the Gitea mirror),
    then routes the in-place reinstall through the DTU engine's ``update`` verb, passing
    the same GITEA_URL / GITEA_TOKEN vars as launch. This matters: the engine applies
    ``url_rewrites`` transiently during provision/update, NOT as persistent git config in
    the container. A raw ``exec`` of the install script would therefore reinstall from
    GitHub and silently miss local changes; going through ``update`` re-applies the
    rewrites so the reinstall pulls the freshly-pushed local snapshots from the mirror.
    """
    current = state.read()
    if not current:
        raise RuntimeError("no warm DTU to refresh; run `up` first")
    log("refresh: re-mirroring local trees, then updating DTU in place")
    varmap = _build_varmap()  # re-pushes local snapshots and returns the mirror vars
    dtu.update(current["dtu_id"], varmap)
    log("refresh: done")


def teardown() -> None:
    """Destroy the DTU instance and the dedicated ``oc-e2e`` Gitea; clear state.

    Only the ``oc-e2e`` DTU and the ``oc-e2e`` Gitea are touched -- other DTUs and Gitea
    envs (aa-e2e, aa-eval, ...) are never destroyed. Gitea teardown is best-effort.
    """
    current = state.read()
    if current:
        dtu.destroy(current["dtu_id"])
    local_mirror.teardown_local_mirror()
    state.clear()
    log("teardown: DTU + oc-e2e gitea torn down, state cleared")
