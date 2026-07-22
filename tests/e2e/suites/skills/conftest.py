"""Fixtures that SEED probe skills into amplifier-agent discovery dirs in the DTU.

The bridge under test exposes amplifier-agent USER-INVOKED skills (the ones GET
/v1/skills returns -- i.e. ``disable-model-invocation: true``) to opencode as native
slash COMMANDS (``~/.config/opencode/command/<name>.md`` with a
``!amplifier:skill <name> $ARGUMENTS`` body). Model-invocable skills are deliberately
NOT bridged, so they never clutter opencode. This suite seeds:

* one USER-INVOKED probe per amplifier discovery dir (project / user / env / hostcfg),
  each ``disable-model-invocation: true`` so it surfaces in GET /v1/skills and should
  become a ``/<name>`` command, and
* one MODEL-INVOCABLE probe (no ``disable-model-invocation``) that must NOT be bridged --
  the negative case that proves the visibility filter.

Each probe is a self-contained inline ``SKILL.md`` (rendered from
``fixtures/probe_skill.md.tmpl``) whose directory name equals its skill name. Seeding
happens BEFORE the opencode TUI/server launches (see ``skills_session``), so the server
discovers them at startup and the launcher can bridge them.

The probe emits a DETERMINISTIC sentinel (``SKILL-PROBE-OK::<name>::ARGS=[...]``) and
echoes ``$ARGUMENTS`` verbatim, so invocation is verifiable via the AI judge alone.

Seeded sources (name -> dir -> kind):
    e2e-amp-proj    -> {PROJECT_DIR}/.amplifier/skills/   (user-invoked)
    e2e-amp-user    -> /root/.amplifier/skills/           (user-invoked)
    e2e-amp-env     -> /root/e2e-amp-env-skills/          (user-invoked, env dir)
    e2e-amp-hostcfg -> /root/e2e-amp-hostcfg-skills/      (user-invoked, hostcfg)
    e2e-amp-model   -> /root/.amplifier/skills/           (model-invocable; NEGATIVE)
"""

from __future__ import annotations

import shlex
import tempfile
from pathlib import Path

import pytest

# PROJECT_DIR is the in-DTU project dir the TUI launches from (``/root/oc-e2e``). It is
# defined in the e2e root conftest, which sits on sys.path (inserted there by that same
# conftest), so it imports cleanly here.
from conftest import PROJECT_DIR
from framework import dtu

# The DTU installs the stack under the root user; ``$HOME`` is ``/root`` there.
DTU_HOME = "/root"

_TEMPLATE_PATH = Path(__file__).parent / "fixtures" / "probe_skill.md.tmpl"

# --------------------------------------------------------------------------- #
# code-review reviewable-change seeding
#
# Mirrors amplifier-agent's own code-review eval task, which seeds a Python file with an
# obvious backdoor-credential defect for the built-in code-review skill to catch (see
# amplifier-agent: .amplifier/evaluation/tasks/amplifier-agent-capabilities/
# skill-invoke-and-behave/workspace/app.py + grader.yaml). The shipped code-review skill
# starts with ``git diff`` (SKILL.md Phase 1), so unlike the amplifier-agent eval -- which
# runs the skill against a bare workspace file -- we commit a CLEAN baseline first, then
# leave the defect as an UNCOMMITTED change. That way ``git diff`` surfaces exactly the
# seeded backdoor line as the change under review.
# --------------------------------------------------------------------------- #
_REVIEW_FILE = f"{PROJECT_DIR}/app.py"

# Committed baseline: a plausible login helper with NO backdoor.
_REVIEW_BASELINE = '''\
"""Tiny login helper (clean baseline; the reviewable change is seeded on top)."""


def check_login(username, password):
    return username == "root" and password == "letmein"


if __name__ == "__main__":
    print(check_login("alice", "letmein"))
'''

# Uncommitted change: adds an obvious hardcoded backdoor credential -- the seeded defect
# the code-review skill should flag. Diffs cleanly against the baseline above.
_REVIEW_DEFECT = '''\
"""Tiny login helper (clean baseline; the reviewable change is seeded on top)."""


def check_login(username, password):
    # SEEDED DEFECT: hardcoded backdoor credential added in this uncommitted change.
    if password == "admin123":
        return True
    return username == "root" and password == "letmein"


if __name__ == "__main__":
    print(check_login("alice", "letmein"))
'''

# source_key -> (skill_name, dest SKILL.md path, user_invoked). Directory name == name.
# ``user_invoked`` renders ``disable-model-invocation: true`` into the frontmatter, which is
# what makes GET /v1/skills return the skill (and thus what the launcher bridges to a
# command). The single ``user_invoked=False`` entry is the negative case.
_SEED_MAP: dict[str, tuple[str, str, bool]] = {
    "amplifier_project": (
        "e2e-amp-proj",
        f"{PROJECT_DIR}/.amplifier/skills/e2e-amp-proj/SKILL.md",
        True,
    ),
    "amplifier_user": (
        "e2e-amp-user",
        f"{DTU_HOME}/.amplifier/skills/e2e-amp-user/SKILL.md",
        True,
    ),
    "amplifier_env": (
        "e2e-amp-env",
        f"{DTU_HOME}/e2e-amp-env-skills/e2e-amp-env/SKILL.md",
        True,
    ),
    "amplifier_hostcfg": (
        "e2e-amp-hostcfg",
        f"{DTU_HOME}/e2e-amp-hostcfg-skills/e2e-amp-hostcfg/SKILL.md",
        True,
    ),
    # NEGATIVE: model-invocable (no disable-model-invocation). Seeded in the user dir so the
    # server DISCOVERS it -- proving it is the GET /v1/skills filter, not a discovery gap,
    # that keeps it out of opencode's command menu.
    "amplifier_model_only": (
        "e2e-amp-model",
        f"{DTU_HOME}/.amplifier/skills/e2e-amp-model/SKILL.md",
        False,
    ),
}


def _render(name: str, *, user_invoked: bool) -> str:
    """Render the probe template for ``name``.

    Substitutes ``{EXTRA_FRONTMATTER}`` (the ``disable-model-invocation`` line for
    user-invoked probes, empty otherwise) and then ``{NAME}``.
    """
    extra = "disable-model-invocation: true\n" if user_invoked else ""
    return (
        _TEMPLATE_PATH.read_text(encoding="utf-8")
        .replace("{EXTRA_FRONTMATTER}", extra)
        .replace("{NAME}", name)
    )


def _seed_skill(dtu_id: str, name: str, dest: str, *, user_invoked: bool) -> None:
    """Write a rendered probe ``SKILL.md`` at ``dest`` inside the DTU.

    Creates the parent dir first (belt-and-suspenders next to ``file_push``'s own
    parent creation) then pushes the rendered file across the DTU boundary.
    """
    parent = dest.rsplit("/", 1)[0]
    dtu.exec_json(dtu_id, ["bash", "-lc", f"mkdir -p {shlex.quote(parent)}"])
    with tempfile.NamedTemporaryFile(
        "w", suffix=".md", prefix="probe-skill-", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(_render(name, user_invoked=user_invoked))
        local_path = handle.name
    dtu.file_push(dtu_id, local_path, dest)


@pytest.fixture
def seeded_skill_dirs(dtu_id: str) -> dict[str, str]:
    """Seed each probe skill; return ``{source_key: skill_name}``.

    Runs before the TUI launches (ordered ahead of ``opencode_session`` in
    ``skills_session``) so the server discovers the freshly-seeded skills and the launcher
    can bridge the user-invoked ones into opencode commands.
    """
    seeded: dict[str, str] = {}
    for source_key, (name, dest, user_invoked) in _SEED_MAP.items():
        _seed_skill(dtu_id, name, dest, user_invoked=user_invoked)
        seeded[source_key] = name

    # LIMITATION (amplifier_env): the probe dir is seeded, but AMPLIFIER_SKILLS_DIR must
    # point the amplifier-agent SERVER at /root/e2e-amp-env-skills for this skill to be
    # discovered (and thus returned by GET /v1/skills and bridged to a command). The server
    # is launched by the root ``opencode_session`` fixture, which we must NOT modify -- so
    # the env var is not exported into it here. The launcher implementation must wire
    # AMPLIFIER_SKILLS_DIR=/root/e2e-amp-env-skills into the launched server's environment.
    # Until then, ``discover-amplifier-env`` and its invocation stay red.

    # LIMITATION (amplifier_hostcfg): the probe dir is seeded, but the amplifier-agent
    # host-config's ``skills.skills`` list must include /root/e2e-amp-hostcfg-skills for the
    # server to discover it. We write a host-config JSON artifact next to the dir as a
    # convenience for the implementer, but we do NOT know (and must not guess) the exact
    # host-config path the launched server reads, and we cannot point the un-modifiable
    # ``opencode_session`` fixture at it. The launcher must place/point the launched server's
    # host-config so ``skills.skills`` contains /root/e2e-amp-hostcfg-skills.
    hostcfg_artifact = f"{DTU_HOME}/e2e-amp-hostcfg-skills/host-config.json"
    hostcfg_json = '{"skills": {"skills": ["/root/e2e-amp-hostcfg-skills"]}}'
    dtu.exec_json(
        dtu_id,
        ["bash", "-lc", f"printf %s {shlex.quote(hostcfg_json)} > {shlex.quote(hostcfg_artifact)}"],
    )

    return seeded


def _seed_file(dtu_id: str, content: str, dest: str) -> None:
    """Write ``content`` to ``dest`` inside the DTU via a pushed temp file."""
    with tempfile.NamedTemporaryFile(
        "w", suffix=".py", prefix="review-src-", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(content)
        local_path = handle.name
    dtu.file_push(dtu_id, local_path, dest)


@pytest.fixture
def seeded_review_workspace(dtu_id: str) -> str:
    """Make PROJECT_DIR a git repo with an UNCOMMITTED reviewable change; return its path.

    Gives the shipped ``code-review`` skill (Group B's ``invoke-command-code-review-fork``)
    something real to review: it starts with ``git diff`` (see the skill's SKILL.md Phase
    1). We commit a clean baseline ``app.py``, then overwrite it with a version containing
    an obvious hardcoded-backdoor defect and leave that change UNCOMMITTED, so ``git diff``
    surfaces exactly the seeded defect as the change under review. Mirrors amplifier-agent's
    own code-review eval (skill-invoke-and-behave) which seeds the same backdoor defect.

    Harmless for the other cases -- they only need the seeded skill dirs and a launched
    TUI -- so it runs for every case in the suite. Ordered ahead of ``opencode_session``
    in ``skills_session`` so the repo exists before the TUI launches.
    """
    # 1. Commit the clean baseline in a fresh repo (dummy identity; no signing).
    #    ``project_dir`` copies PROJECT_DIR through an f-string so its type is a plain str
    #    for shlex.quote and the return (the dynamic ``from conftest import`` confuses the
    #    static type checker, which is what forces this local rebind).
    project_dir = f"{PROJECT_DIR}"
    _seed_file(dtu_id, _REVIEW_BASELINE, _REVIEW_FILE)
    setup = (
        f"cd {shlex.quote(project_dir)} && "
        "git init -q && "
        "git config user.email e2e@example.com && "
        "git config user.name 'E2E Bot' && "
        "git add app.py && "
        "git -c commit.gpgsign=false commit -q -m 'baseline: clean login helper'"
    )
    dtu.exec_json(dtu_id, ["bash", "-lc", setup])
    # 2. Introduce the defect as an uncommitted change so `git diff` shows it.
    _seed_file(dtu_id, _REVIEW_DEFECT, _REVIEW_FILE)
    return project_dir


@pytest.fixture
def skills_session(seeded_skill_dirs, seeded_review_workspace, opencode_session):
    """The live TUI driver, with all probe skills + the review workspace seeded pre-launch.

    Fixture params are ordered deliberately: pytest sets up same-scope independent
    fixtures in listed order, so ``seeded_skill_dirs`` (writing SKILL.md files) and
    ``seeded_review_workspace`` (git repo + uncommitted reviewable change) both run BEFORE
    ``opencode_session`` spawns the opencode TUI/server. That ordering guarantees the
    skills exist for discovery on startup and the code-review skill has a diff to review.
    """
    return opencode_session
