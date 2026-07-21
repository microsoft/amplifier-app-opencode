"""Case data for the skills suite: discovery + invocation over the opencode TUI.

Two contracts are encoded here.

Group A (discovery): every skill, from every discovery directory, is advertised in
opencode's native ``/skills`` popup. One case per source dir, plus the opencode
built-in baseline (``customize-opencode``) and the amplifier-agent built-in
(``code-review``). Each opens ``/skills`` and asserts (via the AI judge) that a
seeded probe skill's name appears.

Group B (invocation): once a skill is advertised, the three real input forms actually
RUN it. Because opencode DEAD-ENDS a bare ``/name`` + Enter (it shows a command
dropdown "No matching items" and swallows Enter), invocation sends must use either a
trailing space (``/name `` -> sent as a message) or trailing args (``/name text``) or
plain natural language. The synthetic probes emit a deterministic sentinel
(``SKILL-PROBE-OK::<name>::ARGS=...``) so a case passes only if the skill genuinely ran;
the shipped ``code-review`` case exercises the real ``/command`` + fork path instead.

Every amplifier-dir case is EXPECTED to fail today: nothing yet bridges amplifier-agent
skill dirs into opencode's native ``/skills`` menu. The opencode-native and built-in
cases are the "don't regress" baseline.
"""

from __future__ import annotations

from framework.harness import Step, TUICase, send_and_settle, setup_select_sonnet5

# Same ground-truth marker the chat suite asserts: the amplifier-agent server logs an
# inbound completion request when a turn round-trips.
_AGENT_LOG_MARKER = "POST /v1/chat/completions"


def _discovery_case(case_name: str, skill_name: str) -> TUICase:
    """Open ``/skills`` and assert ``skill_name`` shows in the popup, then close it.

    No model selection: a default model is preset, and discovery never sends a turn, so
    these stay fast. ``wait`` gives an early, precise failure if the name never renders;
    the ``judge`` step is the authoritative pass/fail on the captured screen.
    """
    return TUICase(
        case_name,
        [
            Step("send_text", "/skills"),
            Step("send_keys", "Enter"),
            Step("wait", skill_name),
            Step("judge", f"Is a skill named '{skill_name}' shown in the skills list on screen?"),
            Step("send_keys", "Escape"),
        ],
    )


# --------------------------------------------------------------------------- #
# Group A -- discovery: every skill is advertised in /skills
# --------------------------------------------------------------------------- #
_DISCOVERY_CASES: list[TUICase] = [
    # amplifier-agent discovery dirs (all EXPECTED to fail until the bridge exists).
    _discovery_case("discover-amplifier-project", "e2e-amp-proj"),
    _discovery_case("discover-amplifier-user", "e2e-amp-user"),
    _discovery_case("discover-amplifier-env", "e2e-amp-env"),
    _discovery_case("discover-amplifier-hostconfig", "e2e-amp-hostcfg"),
    # amplifier-agent built-in shipped skill (no seeding needed).
    _discovery_case("discover-amplifier-builtin", "code-review"),
    # opencode-native + claude-compat discovery dirs (baseline: should already work).
    _discovery_case("discover-opencode-global", "e2e-oc-global"),
    _discovery_case("discover-opencode-project", "e2e-oc-project"),
    _discovery_case("discover-claude-compat", "e2e-claude"),
    # opencode's own built-in skill -- the "native /skills works at all" sanity baseline.
    _discovery_case("baseline-builtin-present", "customize-opencode"),
]


# --------------------------------------------------------------------------- #
# Group B -- invocation: the three input forms run the skill
#
# NOTE the LITERAL trailing space in the slash sends below. opencode DEAD-ENDS a bare
# ``/name`` + Enter (a command-completion dropdown swallows Enter, nothing is sent). A
# single trailing space dismisses that dropdown so the text is sent as a normal message
# and the discovered skill runs. The space must survive to ``tmux send-keys -l``; the
# harness passes it through unstripped (see driver.send_text / harness._send_message).
# --------------------------------------------------------------------------- #
_INVOCATION_CASES: list[TUICase] = [
    # 1. Slash + trailing space, no args -> sentinel with empty/NONE args.
    TUICase(
        "invoke-slash-space",
        [
            *setup_select_sonnet5(),
            *send_and_settle("/e2e-amp-user "),  # trailing space is load-bearing
            Step(
                "judge",
                "Does the assistant's reply contain the token "
                "'SKILL-PROBE-OK::e2e-amp-user' with no arguments (ARGS empty or NONE)?",
            ),
            Step("assert_log", _AGENT_LOG_MARKER),
        ],
    ),
    # 2. Slash + trailing args -> sentinel plus the args echoed by the model.
    #    Args are echoed (e.g. ARGS=remember the banana), NOT $ARGUMENTS-substituted, and
    #    brackets are dropped, so accept either the bracketed or the ARGS=<text> form.
    TUICase(
        "invoke-slash-args",
        [
            *setup_select_sonnet5(),
            *send_and_settle("/e2e-amp-user remember the banana"),
            Step(
                "judge",
                "Does the assistant's reply contain 'SKILL-PROBE-OK::e2e-amp-user' AND "
                "include the text 'remember the banana' as its arguments? Accept either "
                "bracketed or 'ARGS=remember the banana' form.",
            ),
            Step("assert_log", _AGENT_LOG_MARKER),
        ],
    ),
    # 3. Natural-language request -> the model decides to invoke the skill.
    TUICase(
        "invoke-natural-language",
        [
            *setup_select_sonnet5(),
            *send_and_settle("hey, use the e2e-amp-user skill"),
            Step(
                "judge",
                "Does the assistant's reply contain the token "
                "'SKILL-PROBE-OK::e2e-amp-user' (the skill was invoked from a "
                "natural-language request)?",
            ),
            Step("assert_log", _AGENT_LOG_MARKER),
        ],
    ),
    # 4. Parity: an opencode-discovered skill runs the same way (baseline).
    TUICase(
        "invoke-opencode-parity",
        [
            *setup_select_sonnet5(),
            *send_and_settle("/e2e-oc-project "),  # trailing space is load-bearing
            Step(
                "judge",
                "Does the assistant's reply contain the token 'SKILL-PROBE-OK::e2e-oc-project'?",
            ),
            Step("assert_log", _AGENT_LOG_MARKER),
        ],
    ),
    # 5. The shipped amplifier-agent `code-review` skill: disable-model-invocation + fork,
    #    i.e. the REAL "/command" path hidden from the model's tool list. The seeded git
    #    workspace (see conftest) gives it an uncommitted backdoor-credential change to
    #    review. A generous settle timeout because code-review forks three review agents.
    #    NOTE: the assertion targets ENGAGEMENT (code-review is reviewing the change), not
    #    full multi-agent completion. If the fork exceeds the settle window when the
    #    feature is built, tune the settle timeout below.
    TUICase(
        "invoke-bundled-skill",
        [
            *setup_select_sonnet5(),
            *send_and_settle("/code-review ", 240.0),  # trailing space; long fork settle
            Step(
                "judge",
                "Is the assistant performing a code review of the changed code in this "
                "project -- e.g. it references reviewing changes, or reuse / quality / "
                "efficiency, or names a specific issue (such as the hardcoded backdoor "
                "credential) in the changed file?",
            ),
        ],
    ),
]


# Discovery first (fast, no model turn), then invocation (full send/settle round-trips).
SKILLS_CASES: list[TUICase] = [*_DISCOVERY_CASES, *_INVOCATION_CASES]
