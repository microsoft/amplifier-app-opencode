"""Case data for the skills suite: bridging amplifier-agent USER-INVOKED skills into
opencode as native slash COMMANDS.

The bridge (launcher feature under test): the launcher reads amplifier-agent's
user-invoked skills from ``GET /v1/skills`` (the ``disable-model-invocation: true`` set --
today ``code-review`` + ``council``, plus any the user drops in an amplifier dir) and
writes one opencode command file per skill:

    ~/.config/opencode/command/<name>.md   body: !amplifier:skill <name> $ARGUMENTS

So each user-invoked skill shows up as ``/<name>`` in opencode's "/" command menu, and
running it sends that sigil body as the turn message. amplifier-agent then routes it
SERVER-SIDE to its own ``load_skill`` tool (deterministic; runs the real fork for
``code-review`` / ``council``) rather than opencode injecting the body. Model-invocable
skills are deliberately NOT bridged -- they never reach opencode's command menu.

Two contracts are encoded here.

Group A (discovery): every user-invoked skill, from every amplifier discovery dir, is
advertised as a ``/<name>`` command. Plus a NEGATIVE case: a model-invocable skill must
NOT appear as a command (proves the ``GET /v1/skills`` filter is what gates visibility).

Group B (invocation): running the command actually RUNS the skill server-side. The probes
emit a deterministic sentinel (``SKILL-PROBE-OK::<name>::ARGS=...``) so a case passes only
if the skill genuinely ran; the shipped ``code-review`` case exercises the real fork on a
seeded uncommitted defect.

The env and hostcfg cases need the launched server pointed at those discovery dirs, which
the suite's ``skills_session`` fixture arranges.
"""

from __future__ import annotations

import re

from framework.harness import Step, TUICase, setup_select_sonnet5


def _command_discovery_case(case_name: str, skill_name: str) -> TUICase:
    """Open the "/" command menu filtered to ``skill_name``; assert it is listed.

    No model selection: discovery never sends a turn, so these stay fast. Typing
    ``/<name>`` opens opencode's command autocomplete, which renders the matching command
    as a menu ENTRY of the form ``/<name>   <description>``. That description column is
    what distinguishes a real menu entry from the bare ``/<name>`` echoed in the input
    box, so we gate deterministically on it: the command name followed by two-or-more
    spaces and any non-space character (the start of the description). This is a stronger,
    non-flaky assertion than asking an AI judge to eyeball "menu entry vs input text"
    (which misfired on this near-identical layout), and it naturally waits for the popup
    to finish rendering.

    The gate is ``\\S`` rather than ``\\w`` on purpose: bridged descriptions now lead with
    the ``(Amplifier)`` provenance marker, whose ``(`` is not a word character. Discovery
    deliberately stays agnostic about WHAT the description says -- asserting the marker is
    the separate concern of ``_command_description_prefix_case``.
    """
    menu_entry = rf"/{re.escape(skill_name)}\s\s+\S"
    return TUICase(
        case_name,
        [
            Step("send_text", f"/{skill_name}"),
            Step("wait", menu_entry, timeout=30.0, regex=True),
            Step("send_keys", "Escape"),
        ],
    )


def _command_description_prefix_case(case_name: str, skill_name: str) -> TUICase:
    """Assert the command menu entry's DESCRIPTION starts with the ``(Amplifier)`` marker.

    Every skill bridged from amplifier-agent is prefixed with ``(Amplifier)`` so a user
    scanning opencode's "/" menu can tell at a glance which commands came from the agent
    versus opencode's own built-ins. This is a launcher-layer display concern only (the
    same shape as the ``(Amplifier)`` suffix already used for bridged mode agents); the
    description returned by ``GET /v1/skills`` is unchanged.

    The autocomplete renders each entry as ``/<name>   <description>``, so the assertion is
    the command name, the column gap, then the literal marker as the FIRST thing in the
    description column. The trailing ``\\w`` requires the skill's own description to still
    follow the marker -- the prefix must decorate the description, not replace it. Nothing
    is asserted past that word character because the popup truncates long descriptions to
    the pane width.
    """
    menu_entry = rf"/{re.escape(skill_name)}\s\s+\(Amplifier\)\s+\w"
    return TUICase(
        case_name,
        [
            Step("send_text", f"/{skill_name}"),
            Step("wait", menu_entry, timeout=30.0, regex=True),
            Step("send_keys", "Escape"),
        ],
    )


def _command_absent_case(case_name: str, skill_name: str) -> TUICase:
    """Assert ``skill_name`` is NOT offered as a command (the negative / visibility case).

    A model-invocable skill is discovered by the server but filtered out of
    ``GET /v1/skills``, so the launcher never writes a command file for it. Typing the full
    ``/<name>`` therefore matches no command and opencode's autocomplete renders the
    literal ``No matching items`` placeholder. Waiting for that placeholder is a
    deterministic proof of absence (the command name still echoes in the input box, but no
    menu entry with a description is offered), replacing a flaky AI-judge read.
    """
    return TUICase(
        case_name,
        [
            Step("send_text", f"/{skill_name}"),
            Step("wait", "No matching items", timeout=30.0),
            Step("send_keys", "Escape"),
        ],
    )


def invoke_command(command_line: str, settle_timeout: float = 90.0) -> list[Step]:
    """Steps to RUN an opencode slash command and wait for the reply to settle.

    A generated command file makes ``/<name>`` a real command. Typing ``/<name>`` opens
    opencode's command autocomplete popup with the command highlighted, and a single
    Enter there ACCEPTS the completion (leaving the text in the input) rather than
    submitting the turn -- so a bare ``/<name>`` never actually runs. We therefore press
    Enter twice: the first accepts/closes the popup, the second submits the now-plain
    input (via ``submit_message``, which does NOT retype -- retyping would re-open the
    popup and re-swallow the Enter). On submit, opencode expands the command body
    (``!amplifier:skill <name> $ARGUMENTS``) and sends it as the turn message, which
    amplifier-agent routes server-side to ``load_skill``.

    (Confirmed on a DTU run: a bare ``/<name>`` + single Enter left the command unsent in
    the input box. Escape does NOT work -- it clears the typed input. Commands WITH
    trailing args already submitted because the trailing text closes the popup.)
    """
    return [
        Step("send_text", command_line),
        Step("send_keys", "Enter"),
        Step("submit_message", "", timeout=settle_timeout),
    ]


# --------------------------------------------------------------------------- #
# Group A -- discovery: each user-invoked skill is a "/" command; model-only is not
# --------------------------------------------------------------------------- #
_DISCOVERY_CASES: list[TUICase] = [
    # amplifier-agent built-in user-invoked skills (no seeding needed).
    _command_discovery_case("discover-builtin-code-review", "code-review"),
    _command_discovery_case("discover-builtin-council", "council"),
    # user-invoked skills seeded one per amplifier discovery dir.
    _command_discovery_case("discover-amplifier-project", "e2e-amp-proj"),
    _command_discovery_case("discover-amplifier-user", "e2e-amp-user"),
    _command_discovery_case("discover-amplifier-env", "e2e-amp-env"),
    _command_discovery_case("discover-amplifier-hostconfig", "e2e-amp-hostcfg"),
    # NEGATIVE: a model-invocable amplifier skill must NOT surface as a command.
    _command_absent_case("hidden-model-invocable-skill", "e2e-amp-model"),
    # PROVENANCE: bridged skills are marked "(Amplifier)" at the head of the description.
    _command_description_prefix_case("description-prefix-builtin-code-review", "code-review"),
    _command_description_prefix_case("description-prefix-amplifier-user", "e2e-amp-user"),
]


# --------------------------------------------------------------------------- #
# Group B -- invocation: running the command runs the skill server-side
# --------------------------------------------------------------------------- #
#
# These assert on FUNCTIONAL outcomes only -- what a user sees on screen -- not on any
# internal wiring (no agent-log greps, no session-file paths, no assumptions about how the
# skill runs server-side). The probe's sentinel is the test's OWN contract for "the skill
# ran and received its arguments"; code-review is judged on the user-visible outcome of its
# public contract ("review changed code ... fix any issues found"): it finds the planted
# defect. If the implementation changes but the behavior holds, these stay green.
_INVOCATION_CASES: list[TUICase] = [
    # 1. Command with no args -> the skill runs and reports no arguments.
    TUICase(
        "invoke-command-no-args",
        [
            *setup_select_sonnet5(),
            *invoke_command("/e2e-amp-user"),
            Step("wait", "SKILL-PROBE-OK::e2e-amp-user"),  # deterministic: the skill ran
            Step(
                "judge",
                "Does the assistant's reply report that the skill ran with NO arguments "
                "(an empty argument list, e.g. ARGS empty / NONE / [])?",
            ),
        ],
    ),
    # 2. Command with trailing args -> the skill runs and receives those args verbatim.
    TUICase(
        "invoke-command-with-args",
        [
            *setup_select_sonnet5(),
            *invoke_command("/e2e-amp-user remember the banana"),
            Step("wait", "SKILL-PROBE-OK::e2e-amp-user"),  # deterministic: the skill ran
            Step(
                "judge",
                "Does the assistant's reply show the skill received the arguments "
                "'remember the banana' (that exact text appears as its argument value)?",
            ),
        ],
    ),
    # 3. The shipped amplifier-agent `code-review` command running end-to-end. Its public
    #    contract is: review the changed code and fix any issues found. The seeded git
    #    workspace (see conftest) leaves an uncommitted hardcoded-backdoor change, so a
    #    working code-review MUST surface that defect. We judge THAT user-visible outcome,
    #    not how the review is produced (agents, phases, lenses are all implementation).
    #    Generous settle timeout because a real review takes a while.
    TUICase(
        "invoke-command-code-review-finds-defect",
        [
            *setup_select_sonnet5(),
            *invoke_command("/code-review", settle_timeout=240.0),
            Step(
                "judge",
                "Did the assistant review the changed code and identify the security "
                "defect in it -- the hardcoded backdoor credential (the 'admin123' "
                "password that lets any user authenticate / bypasses the login check)? "
                "Passing requires surfacing that specific problem (or an equivalent "
                "description of the authentication bypass), not merely acknowledging that "
                "a review was requested.",
            ),
        ],
    ),
]


# Discovery first (fast, no model turn), then invocation (full send/settle round-trips).
SKILLS_CASES: list[TUICase] = [*_DISCOVERY_CASES, *_INVOCATION_CASES]
