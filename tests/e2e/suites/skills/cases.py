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

Every amplifier case is EXPECTED to fail until the launcher bridge exists. The env and
hostcfg cases additionally need the launched server pointed at those dirs (see the
LIMITATION notes in ``conftest.py``).
"""

from __future__ import annotations

from framework.harness import Step, TUICase, send_and_settle, setup_select_sonnet5


def _command_discovery_case(case_name: str, skill_name: str) -> TUICase:
    """Open the "/" command menu filtered to ``skill_name``; assert it is listed.

    No model selection: discovery never sends a turn, so these stay fast (the
    ``opencode_session`` fixture already gates on the main screen being ready). Typing
    ``/<name>`` opens opencode's command palette filtered to that command. ``wait`` is an
    early, cheap gate that the screen rendered; the ``judge`` step is the authoritative
    pass/fail on whether ``/<name>`` is a real command in the menu (not just input echo).
    """
    return TUICase(
        case_name,
        [
            Step("send_text", f"/{skill_name}"),
            Step("wait", skill_name),
            Step(
                "judge",
                f"Is '/{skill_name}' shown as an available COMMAND in opencode's command "
                f"menu/list on screen (a selectable command entry, not merely the text "
                f"typed into the input box)?",
            ),
            Step("send_keys", "Escape"),
        ],
    )


def _command_absent_case(case_name: str, skill_name: str) -> TUICase:
    """Assert ``skill_name`` is NOT offered as a command (the negative / visibility case).

    A model-invocable skill is discovered by the server but filtered out of
    ``GET /v1/skills``, so the launcher never writes a command file for it. Typing
    ``/<name>`` should therefore match no command (opencode shows "No matching items").
    The judge passes ONLY if the command is absent.
    """
    return TUICase(
        case_name,
        [
            Step("send_text", f"/{skill_name}"),
            Step(
                "judge",
                f"Confirm there is NO available command named '/{skill_name}' in opencode's "
                f"command menu -- the menu shows no matching command (e.g. 'No matching "
                f"items') or the command is simply absent. The same text appearing only in "
                f"the input box does NOT count as a command. Answer PASS only if no such "
                f"command is offered.",
            ),
            Step("send_keys", "Escape"),
        ],
    )


def invoke_command(command_line: str, settle_timeout: float = 90.0) -> list[Step]:
    """Steps to RUN an opencode slash command and wait for the reply to settle.

    A generated command file makes ``/<name>`` a real command, so typing it and pressing
    Enter runs it: opencode expands the command body (``!amplifier:skill <name>
    $ARGUMENTS``) and sends it as the turn message, which amplifier-agent routes
    server-side to ``load_skill``. Reuses the busy-indicator settle logic in
    ``send_and_settle`` (type text, Enter, wait for generation to start then finish).

    NOTE (tune on first DTU run): if opencode's command palette intercepts Enter to SELECT
    the highlighted command instead of running it, this may need an extra key (select, then
    send). This mirrors the trailing-space nuance the earlier /skills flow required. Keep
    the interaction fix here so the cases stay declarative.
    """
    return send_and_settle(command_line, settle_timeout=settle_timeout)


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
