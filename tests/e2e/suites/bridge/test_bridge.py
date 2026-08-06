"""DTU-backed tests for the skills/modes bridge contract (``docs/spec/skills-and-modes-bridge.md``).

Ground-truth assertions only -- no TUI, no AI judge. Everything the bridge does is a
FILESYSTEM side effect (generated command/agent files, ownership manifests), so every
assertion reads files inside the DTU with ``driver.run_command`` (same spirit as
``suites/shadowing`` and ``suites/traversal``).
"""

from __future__ import annotations

import json
import shlex

import pytest
from framework.driver import TmuxTuiDriver
from suites.bridge.conftest import (
    AGENT_DIR,
    AGENTS_MANIFEST,
    BRIDGE_NAME,
    COMMAND_DIR,
    COMMANDS_MANIFEST,
    GENERATED_AGENT,
    GENERATED_COMMAND,
    USER_OWNED_COMMAND,
    USER_OWNED_CONTENT,
    USER_OWNED_NAME,
    PreparedRun,
)

# ``dtu`` only -- deliberately NOT ``fresh_dtu``. amplifier-agent fixes skill/mode
# discovery at server STARTUP, but this suite needs no launch-time env overrides, so the
# fixtures kill the running server and let ``prepare`` start a fresh one. That gets the
# same guarantee against the WARM DTU without a full reprovision per run (same trade as
# suites/shadowing and suites/traversal).
pytestmark = pytest.mark.dtu


def _read_text(driver: TmuxTuiDriver, path: str) -> str:
    """Return the contents of ``path`` inside the DTU, or ``""`` if unreadable."""
    proc = driver.run_command(["bash", "-lc", f"cat {shlex.quote(path)} 2>/dev/null"])
    return proc.stdout


def _path_exists(driver: TmuxTuiDriver, path: str) -> bool:
    """True if ``path`` exists inside the DTU."""
    proc = driver.run_command(["bash", "-lc", f"test -e {shlex.quote(path)}"])
    return proc.returncode == 0


def _split_frontmatter(content: str) -> tuple[list[str], list[str]]:
    """Split a generated file into (frontmatter lines, body lines).

    Assumes the exact two-``---``-delimiter shape the bridge always writes (see
    docs/spec/skills-and-modes-bridge.md's worked examples for both faces).
    """
    lines = content.splitlines()
    assert lines and lines[0].strip() == "---", (
        f"expected the file to open with a '---' frontmatter delimiter, got:\n{content}"
    )
    closing = lines[1:].index("---") + 1
    return lines[1:closing], lines[closing + 1 :]


def test_skill_becomes_a_command_file(bridge_prepare: PreparedRun) -> None:
    """A server-supplied skill becomes exactly one ``<name>.md`` command file.

    Per the spec's skill command template, the file carries a JSON-string
    ``description`` leading with the ``"(Amplifier) "`` marker, and a body that is
    exactly ``!amplifier:skill <name> $ARGUMENTS`` -- the whole point being that
    invoking the generated opencode command re-dispatches to the server-side skill.
    """
    driver = bridge_prepare.driver
    content = _read_text(driver, GENERATED_COMMAND)
    assert content.strip(), (
        f"expected the bridge to generate {GENERATED_COMMAND} for skill {BRIDGE_NAME!r}; "
        f"prepare output:\n{bridge_prepare.stdout}"
    )

    front, body = _split_frontmatter(content)
    desc_lines = [line for line in front if line.strip().startswith("description:")]
    assert len(desc_lines) == 1, (
        f"expected exactly one 'description:' frontmatter line, got {front!r}\n"
        f"full file:\n{content}"
    )
    raw_value = desc_lines[0].split(":", 1)[1].strip()
    value = json.loads(raw_value)
    assert value.startswith("(Amplifier) "), (
        f"expected the description to lead with the '(Amplifier) ' marker, got {value!r}\n"
        f"full file:\n{content}"
    )

    body_text = "\n".join(line for line in body if line.strip())
    assert body_text == f"!amplifier:skill {BRIDGE_NAME} $ARGUMENTS", (
        f"expected the exact skill-invocation body line, got {body_text!r}\nfull file:\n{content}"
    )


def test_mode_becomes_an_agent_file(bridge_prepare: PreparedRun) -> None:
    """A server-supplied mode becomes exactly one ``amplifier-<name>.md`` agent file.

    Per the spec's mode agent template: ``mode: primary``, a ``name`` field carrying
    the user-visible ``" (Amplifier)"`` suffix, the ``[amplifier-agent:mode=<name>]``
    directive in the body, and CRITICALLY no ``model:`` frontmatter key -- the spec
    states this is deliberately absent so opencode never rejects the agent and the
    session's current model is inherited instead.
    """
    driver = bridge_prepare.driver
    content = _read_text(driver, GENERATED_AGENT)
    assert content.strip(), (
        f"expected the bridge to generate {GENERATED_AGENT} for mode {BRIDGE_NAME!r}; "
        f"prepare output:\n{bridge_prepare.stdout}"
    )

    front, body = _split_frontmatter(content)
    assert any(line.strip() == "mode: primary" for line in front), (
        f"expected 'mode: primary' in the agent frontmatter, got {front!r}\nfull file:\n{content}"
    )

    name_lines = [line for line in front if line.strip().startswith("name:")]
    assert len(name_lines) == 1, (
        f"expected exactly one 'name:' frontmatter line, got {front!r}\nfull file:\n{content}"
    )
    assert name_lines[0].rstrip().endswith(' (Amplifier)"'), (
        f"expected the name field to end in ' (Amplifier)\"', got {name_lines[0]!r}\n"
        f"full file:\n{content}"
    )

    assert not any(line.strip().startswith("model:") for line in front), (
        "generated mode agent file must never carry a 'model:' frontmatter key -- the "
        f"spec says this is deliberate so opencode never rejects the agent; got "
        f"frontmatter:\n{front!r}"
    )

    body_text = "\n".join(body)
    assert f"[amplifier-agent:mode={BRIDGE_NAME}]" in body_text, (
        f"expected the mode directive in the body, got:\n{body_text}"
    )


def test_manifests_record_exactly_the_generated_files(bridge_prepare: PreparedRun) -> None:
    """Each face's ownership manifest is a single-key object naming real, on-disk files.

    Per the spec: the commands manifest is ``{"commands": [...]}``, the agents manifest
    is ``{"agents": [...]}``, and the two are independent -- each face reconciles only
    against its own manifest.
    """
    driver = bridge_prepare.driver
    commands_raw = _read_text(driver, COMMANDS_MANIFEST)
    agents_raw = _read_text(driver, AGENTS_MANIFEST)
    assert commands_raw.strip(), (
        f"expected a commands manifest at {COMMANDS_MANIFEST}; prepare output:\n"
        f"{bridge_prepare.stdout}"
    )
    assert agents_raw.strip(), (
        f"expected an agents manifest at {AGENTS_MANIFEST}; prepare output:\n"
        f"{bridge_prepare.stdout}"
    )

    commands_doc = json.loads(commands_raw)
    agents_doc = json.loads(agents_raw)
    assert set(commands_doc.keys()) == {"commands"}, (
        f"expected the commands manifest to have exactly the 'commands' key, got {commands_doc!r}"
    )
    assert set(agents_doc.keys()) == {"agents"}, (
        f"expected the agents manifest to have exactly the 'agents' key, got {agents_doc!r}"
    )

    assert f"{BRIDGE_NAME}.md" in commands_doc["commands"], (
        f"expected {BRIDGE_NAME}.md recorded in the commands manifest, got "
        f"{commands_doc['commands']!r}"
    )
    assert f"amplifier-{BRIDGE_NAME}.md" in agents_doc["agents"], (
        f"expected amplifier-{BRIDGE_NAME}.md recorded in the agents manifest, got "
        f"{agents_doc['agents']!r}"
    )

    for filename in commands_doc["commands"]:
        exists = driver.run_command(
            ["bash", "-lc", f"test -f {shlex.quote(f'{COMMAND_DIR}/{filename}')}"]
        )
        assert exists.returncode == 0, (
            f"commands manifest records {filename!r} but it does not exist on disk under "
            f"{COMMAND_DIR}"
        )
    for filename in agents_doc["agents"]:
        exists = driver.run_command(
            ["bash", "-lc", f"test -f {shlex.quote(f'{AGENT_DIR}/{filename}')}"]
        )
        assert exists.returncode == 0, (
            f"agents manifest records {filename!r} but it does not exist on disk under {AGENT_DIR}"
        )


def test_removing_a_source_skill_prunes_its_command_file(pruned_prepare: PreparedRun) -> None:
    """Reconciliation prunes a generated file once its source skill disappears.

    Per the spec's reconciliation contract: "A file recorded in the OLD manifest whose
    source (skill or mode) no longer exists is deleted." The ``pruned_prepare`` fixture
    deletes the seeded skill's directory and reruns ``prepare``; this test asserts both
    halves of the prune -- the file is gone AND the manifest no longer names it.
    """
    assert pruned_prepare.returncode == 0, (
        f"prepare failed after removing the skill source:\n{pruned_prepare.stdout}"
    )
    driver = pruned_prepare.driver
    gone = driver.run_command(["bash", "-lc", f"test -e {shlex.quote(GENERATED_COMMAND)}"])
    assert gone.returncode != 0, (
        f"expected {GENERATED_COMMAND} to be pruned once its source skill was removed; "
        f"prepare output:\n{pruned_prepare.stdout}"
    )

    raw = _read_text(driver, COMMANDS_MANIFEST)
    commands = json.loads(raw).get("commands", []) if raw.strip() else []
    assert f"{BRIDGE_NAME}.md" not in commands, (
        f"expected {BRIDGE_NAME}.md removed from the commands manifest after pruning, got "
        f"{commands!r}"
    )


def test_a_user_authored_file_is_never_overwritten(user_owned_prepare: PreparedRun) -> None:
    """A file the bridge did not generate is left alone, byte-for-byte, forever.

    Per the spec's reconciliation contract: "A target that already exists on disk but
    was NOT recorded in the old manifest is left alone and skipped -- it is treated as
    the user's own file, never overwritten." The fixture drops a foreign file at
    ``zz-user-owned.md`` (a name no seeded skill uses) and reruns ``prepare``; this test
    asserts the file is untouched and was never claimed by the manifest.
    """
    assert user_owned_prepare.returncode == 0, (
        f"prepare failed with a foreign file present in the command dir:\n"
        f"{user_owned_prepare.stdout}"
    )
    driver = user_owned_prepare.driver
    assert _path_exists(driver, USER_OWNED_COMMAND), (
        f"expected the user-owned file {USER_OWNED_COMMAND} to still exist after prepare; "
        f"output:\n{user_owned_prepare.stdout}"
    )
    content = _read_text(driver, USER_OWNED_COMMAND)
    assert content == USER_OWNED_CONTENT, (
        f"user-owned file {USER_OWNED_COMMAND} was modified by prepare.\n"
        f"before:\n{USER_OWNED_CONTENT!r}\nafter:\n{content!r}"
    )

    raw = _read_text(driver, COMMANDS_MANIFEST)
    commands = json.loads(raw).get("commands", []) if raw.strip() else []
    assert USER_OWNED_NAME not in commands, (
        f"user-owned file must never be recorded in the ownership manifest, got {commands!r}"
    )
