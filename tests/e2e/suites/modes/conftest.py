"""Fixtures that SEED uniquely-named probe modes into amplifier mode dirs in the DTU.

Each probe is a single flat ``<name>.md`` file with a top-level ``mode:`` frontmatter block
-- the exact on-disk layout amplifier-agent's mode discovery expects (it globs ``*.md`` and
takes the file stem as the mode name; see amplifier-agent ``resources.list_modes`` +
hooks-mode ``ModeDiscovery``). Seeding happens BEFORE the opencode TUI/server launches (see
``modes_session``), so the modes exist when the launcher fetches ``GET /v1/modes`` at startup.

The probe forces a DETERMINISTIC sentinel (``MODE-PROBE-OK::<name>``) when active, so both
discovery (the ``amplifier-<name>`` agent in the "Select agent" dialog) and activation (the
sentinel in the reply) are verifiable via the AI judge alone -- no log or filesystem
inspection.

Seeded sources (bare mode name -> path in DTU; the launcher adds the ``amplifier-`` prefix
for the opencode agent identity, so ``e2e-proj`` -> agent ``amplifier-e2e-proj``):
    e2e-proj  -> {PROJECT_DIR}/.amplifier/modes/e2e-proj.md   (amplifier project mode)
    e2e-user  -> /root/.amplifier/modes/e2e-user.md           (amplifier user mode)
"""

from __future__ import annotations

import shlex
import tempfile
from pathlib import Path

import pytest

# PROJECT_DIR is the in-DTU project dir the TUI launches from (``/root/oc-e2e``). It is
# defined in the e2e root conftest, which sits on sys.path, so it imports cleanly here.
from conftest import PROJECT_DIR
from framework import dtu

# The DTU installs the stack under the root user; ``$HOME`` is ``/root`` there.
DTU_HOME = "/root"

_TEMPLATE_PATH = Path(__file__).parent / "fixtures" / "probe_mode.md.tmpl"

# bare mode name -> dest ``<name>.md`` path inside the DTU (single flat file per mode).
_SEED_MAP: dict[str, tuple[str, str]] = {
    "amplifier_project": ("e2e-proj", f"{PROJECT_DIR}/.amplifier/modes/e2e-proj.md"),
    "amplifier_user": ("e2e-user", f"{DTU_HOME}/.amplifier/modes/e2e-user.md"),
}


def _render(name: str) -> str:
    """Render the probe template for ``name`` (substitute the ``{NAME}`` placeholder)."""
    return _TEMPLATE_PATH.read_text(encoding="utf-8").replace("{NAME}", name)


def _seed_mode(dtu_id: str, name: str, dest: str) -> None:
    """Write a rendered probe ``<name>.md`` at ``dest`` inside the DTU.

    Creates the parent dir first (belt-and-suspenders next to ``file_push``'s own parent
    creation) then pushes the rendered file across the DTU boundary.
    """
    parent = dest.rsplit("/", 1)[0]
    dtu.exec_json(dtu_id, ["bash", "-lc", f"mkdir -p {shlex.quote(parent)}"])
    with tempfile.NamedTemporaryFile(
        "w", suffix=".md", prefix="probe-mode-", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(_render(name))
        local_path = handle.name
    dtu.file_push(dtu_id, local_path, dest)


@pytest.fixture
def seeded_mode_dirs(dtu_id: str) -> dict[str, str]:
    """Seed a probe mode into each amplifier mode dir; return ``{source_key: mode_name}``.

    Runs before the TUI launches (ordered ahead of ``opencode_session`` in ``modes_session``)
    so the launcher's ``GET /v1/modes`` fetch sees the freshly-seeded modes.

    LIMITATION (amplifier_project): the project mode is discovered relative to the
    amplifier-agent server's working directory. It is only found if the launched server runs
    with cwd == PROJECT_DIR (the launcher passes ``--project-dir {PROJECT_DIR}``). The user
    mode (/root/.amplifier/modes/) is an absolute path and is always discovered.
    """
    seeded: dict[str, str] = {}
    for source_key, (name, dest) in _SEED_MAP.items():
        _seed_mode(dtu_id, name, dest)
        seeded[source_key] = name
    return seeded


@pytest.fixture
def modes_session(seeded_mode_dirs, opencode_session):
    """The live TUI driver, with probe modes seeded pre-launch.

    Fixture params are ordered deliberately: pytest sets up same-scope independent fixtures in
    listed order, so ``seeded_mode_dirs`` (writing the mode ``.md`` files) runs BEFORE
    ``opencode_session`` spawns the opencode TUI/server. That ordering guarantees the modes
    exist for discovery on startup.
    """
    return opencode_session
