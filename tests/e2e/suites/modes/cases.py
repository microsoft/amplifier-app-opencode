"""Case data for the modes suite: discovery + behavior over the opencode TUI.

Two contracts are encoded here.

Group A (discovery): each amplifier mode is advertised in opencode's native agent list
dialog, SUFFIXED with `` (Amplifier)``. The dialog is opened with the leader key (``ctrl+x``
then ``a``), which renders a "Select agent" picker listing every primary agent by name
(see opencode ``component/dialog-agent.tsx``). One case per built-in mode (``plan``,
``brainstorm``), one dedicated naming-convention case, and one case per seeded project/user
probe mode. Each opens the dialog and asserts (via the AI judge) that the
``<mode> (Amplifier)`` agent name appears.

The suffix lives in the generated agent file's frontmatter ``name`` field, which opencode
spreads over the filename-derived default (``config/agent.ts``). The file on disk is still
``amplifier-<mode>.md``; these tests assert only on what the user sees, so they are
deliberately blind to that.

Group B (behavior): once a mode is advertised, selecting it must actually change behavior.
The synthetic probe mode emits a deterministic sentinel (``MODE-PROBE-OK::<name>``) so a
case passes only if selecting the mode genuinely applied its guidance end-to-end; the
built-in ``plan``/``brainstorm`` cases assert their real read-only / exploratory behavior
from the visible reply. Persistence is checked purely from what the TUI shows (the active
agent stays selected across turns).

Every case is EXPECTED to fail today. The launcher does not yet read ``GET /v1/modes`` or
generate any ``amplifier-<mode>`` primary-agent files, so no amplifier mode appears in the
agent list. Group B additionally depends on amplifier-agent applying the selected mode,
which is also unbuilt. Assertions are made
ONLY on user-visible screen state via the AI judge -- never on logs or generated files --
so a test breaks only if the user-facing behavior breaks.
"""

from __future__ import annotations

from framework.harness import Step, TUICase, send_and_settle, setup_select_sonnet5

# Leader (``ctrl+x``) + ``a`` opens opencode's agent list dialog ("Select agent"). Sent as
# two tmux keys in one step: ``C-x`` (ctrl+x) then ``a``. See opencode keybind.ts
# (``agent_list: <leader>a``, ``LeaderDefault = ctrl+x``).
_OPEN_AGENT_LIST = Step("send_keys", "C-x a")
_AGENT_DIALOG_MARKER = "Select agent"


def _mode_agent(mode: str) -> str:
    """Name opencode shows for amplifier mode ``mode``: ``<mode> (Amplifier)``.

    This is the ONLY name the user ever sees. In opencode an agent has a single
    ``name`` that is simultaneously the config-map key, the string in the "Select
    agent" picker, and (title-cased) the string in the prompt status line -- there
    is no separate display field. The generated file is still ``amplifier-<mode>.md``
    on disk, but its frontmatter ``name`` overrides the filename-derived default.
    """
    return f"{mode} (Amplifier)"


def _discovery_case(case_name: str, agent_name: str) -> TUICase:
    """Open the agent list and assert ``agent_name`` shows in it, then close it.

    No model/turn is needed -- discovery just inspects the picker. ``wait`` gives an early,
    precise failure if the name never renders (the current red state); the ``judge`` step is
    the authoritative pass/fail on the captured screen.
    """
    return TUICase(
        case_name,
        [
            _OPEN_AGENT_LIST,
            Step("wait", _AGENT_DIALOG_MARKER),
            Step("wait", agent_name, timeout=15.0),
            Step("judge", f"Is an agent named '{agent_name}' shown in the agent list on screen?"),
            Step("send_keys", "Escape"),
        ],
    )


def _select_agent(agent_name: str) -> list[Step]:
    """Steps to open the agent list, filter to ``agent_name``, and select it (Enter).

    The agent dialog filters as you type, like the ``/models`` picker. Selecting a mode-agent
    makes that mode active for the session.

    The final ``judge`` step is a real selection GATE: a bare ``wait`` on the agent name
    cannot be used here because the typed filter text echoes the name into the search box
    (matching immediately even when no such agent exists). Instead we assert the picker
    actually resolved the name to an active agent. If the launcher does not generate the
    ``<mode> (Amplifier)`` agents this fails with "No results found", which is the correct red.
    """
    return [
        _OPEN_AGENT_LIST,
        Step("wait", _AGENT_DIALOG_MARKER),
        Step("send_text", agent_name),
        Step("send_keys", "Enter"),
        Step(
            "judge",
            f"Has the agent '{agent_name}' been selected and become the ACTIVE agent -- i.e. "
            f"the 'Select agent' picker has closed and '{agent_name}' (possibly title-cased, "
            f"e.g. 'Plan (Amplifier)') now shows as the active agent in the prompt footer/status "
            f"line? Answer NO if the picker is still open, shows 'No results found', or the "
            f"active agent is a different one such as Build.",
        ),
    ]


# --------------------------------------------------------------------------- #
# Group A -- discovery: each amplifier mode is a suffixed primary agent
# --------------------------------------------------------------------------- #
_DISCOVERY_CASES: list[TUICase] = [
    # Built-in amplifier modes, suffixed. A native opencode `plan` agent may also exist;
    # we specifically require amplifier's to be `plan (Amplifier)`.
    _discovery_case("discover-amplifier-plan", _mode_agent("plan")),
    _discovery_case("discover-amplifier-brainstorm", _mode_agent("brainstorm")),
    # Seeded project/user modes (see conftest), suffixed by the launcher.
    _discovery_case("discover-amplifier-project-mode", _mode_agent("e2e-proj")),
    _discovery_case("discover-amplifier-user-mode", _mode_agent("e2e-user")),
    # The explicit naming-convention contract: amplifier modes must NOT collide with
    # opencode's native agents, and must read as the mode name first with amplifier as
    # a parenthesised qualifier -- NOT as an `amplifier-` prefix.
    TUICase(
        "suffix-required-not-prefixed-or-bare",
        [
            _OPEN_AGENT_LIST,
            Step("wait", _AGENT_DIALOG_MARKER),
            Step(
                "judge",
                "In the agent list, are amplifier's modes shown as the mode name followed by "
                "'(Amplifier)' -- specifically 'plan (Amplifier)' and 'brainstorm (Amplifier)'? "
                "Answer NO if they are instead shown with an 'amplifier-' PREFIX (e.g. "
                "'amplifier-plan', 'Amplifier-Plan'), or as bare 'plan'/'brainstorm' with no "
                "Amplifier qualifier at all. A separate native opencode agent (e.g. 'build' or "
                "a native 'plan') may also be present, which is fine.",
            ),
            Step("send_keys", "Escape"),
        ],
    ),
]


# --------------------------------------------------------------------------- #
# Group B -- behavior: selecting a mode changes user-visible behavior
# --------------------------------------------------------------------------- #
# Each behavior case first selects an Amplifier model (setup_select_sonnet5), THEN
# activates a mode agent. This mirrors real usage and is REQUIRED for the mode to
# apply: mode agents carry no ``model`` field, so they inherit the session's
# current model. The mode is signalled to amplifier-agent by an
# ``[amplifier-agent:mode=<name>]`` directive in the agent prompt, which only
# reaches amplifier-agent when the turn is routed through the Amplifier provider.
# If the session were on a native (non-Amplifier) model, the turn would bypass
# amplifier-agent entirely and the mode would silently not apply.
_BEHAVIOR_CASES: list[TUICase] = [
    # Deterministic activation proof: the probe mode forces a sentinel line. Passes only if
    # selecting the mode-agent actually applied the mode's guidance end-to-end.
    TUICase(
        "activate-probe-mode-sentinel",
        [
            *setup_select_sonnet5(),
            *_select_agent(_mode_agent("e2e-user")),
            *send_and_settle("say anything"),
            Step(
                "judge",
                "Does the assistant's reply begin with the exact line 'MODE-PROBE-OK::e2e-user'?",
            ),
        ],
    ),
    # Persistence: the selected mode stays active across turns (opencode holds the primary
    # agent client-side and re-sends it every turn).
    TUICase(
        "activate-amplifier-plan-persists",
        [
            *setup_select_sonnet5(),
            *_select_agent(_mode_agent("plan")),
            *send_and_settle("hello"),
            *send_and_settle("are you still there?"),
            Step(
                "judge",
                "Is 'plan (Amplifier)' still the active agent/mode shown on screen after two "
                "turns? Its name may render title-cased (e.g. 'Plan (Amplifier)').",
            ),
        ],
    ),
    # Plan mode is read-only: asked to write a file, it must respond with a plan and NOT
    # actually create/modify the file. Judged from the visible transcript.
    TUICase(
        "activate-amplifier-plan-behavior",
        [
            *setup_select_sonnet5(),
            *_select_agent(_mode_agent("plan")),
            *send_and_settle("implement a hello world Python script and save it to hello.py"),
            Step(
                "judge",
                "Did the assistant respond with a PLAN or read-only analysis and NOT actually "
                "create or write the file? (plan mode is read-only)",
            ),
        ],
    ),
    # Brainstorm mode is exploratory: it should discuss/design rather than implement.
    TUICase(
        "activate-amplifier-brainstorm-behavior",
        [
            *setup_select_sonnet5(),
            *_select_agent(_mode_agent("brainstorm")),
            *send_and_settle("build a URL shortener"),
            Step(
                "judge",
                "Is the response exploratory brainstorming/design discussion (questions, "
                "options, trade-offs) rather than a concrete implementation with code?",
            ),
        ],
    ),
]


# --------------------------------------------------------------------------- #
# Group C -- separation: modes are agents, NOT selectable models
# --------------------------------------------------------------------------- #
# A mode is a per-turn behavior overlay. Its ONLY user-facing home is the agent
# ("Select agent", Tab / ctrl+x a) picker as ``<name> (Amplifier)``. It must NOT
# leak into opencode's ``/models`` ("Select model") picker.
#
# Today amplifier-agent advertises a synthetic ``mode-<name>`` model alias per
# mode (app.py appends it to ``available_models``, so it rides through
# ``GET /v1/models`` with ``display_name`` ``"Mode: <name>"``). The launcher
# copies every /v1/models row into opencode's provider block unfiltered, so the
# mode currently shows up as a selectable model "Mode: <name>" -- and picking it
# changes the active model. These cases pin the DESIRED contract: modes are
# absent from the model picker. They are EXPECTED TO FAIL until the alias is kept
# out of the model list (server-side: stop appending to ``available_models``;
# or launcher-side: filter ``mode-*`` ids out of the provider block).
#
# The picker filters as you type. We type the bare mode name (``plan`` /
# ``brainstorm``) -- no genuine LLM model carries that word -- so a correct
# picker shows "No results found" (or only real models), and the buggy one
# surfaces "Mode: <name>". The judge is phrased so a YES verdict == the correct
# (mode-absent) state.
_MODEL_DIALOG_MARKER = "Select model"


def _open_models_picker() -> list[Step]:
    """Steps to reach the main screen and open the ``/models`` ("Select model") picker."""
    return [
        Step("wait", "tab agents", timeout=120.0),
        Step("send_text", "/models"),
        Step("send_keys", "Enter"),
        Step("wait", _MODEL_DIALOG_MARKER),
    ]


_SEPARATION_CASES: list[TUICase] = [
    # One dual-surface case covering BOTH built-in modes: they must appear in the
    # agent picker (their correct home) yet be ABSENT from the model picker.
    TUICase(
        "modes-in-agents-not-in-models",
        [
            # 1. Agent picker: plan (Amplifier) and brainstorm (Amplifier) ARE listed.
            _OPEN_AGENT_LIST,
            Step("wait", _AGENT_DIALOG_MARKER),
            Step("wait", _mode_agent("plan"), timeout=15.0),
            Step(
                "judge",
                "In this agent list ('Select agent'), are BOTH 'plan (Amplifier)' and "
                "'brainstorm (Amplifier)' shown as selectable agents?",
            ),
            Step("send_keys", "Escape"),
            # 2. Model picker: neither mode may appear as a selectable model. Filter by
            # 'mode' so any leaked 'Mode: plan' / 'Mode: brainstorm' alias is on screen.
            *_open_models_picker(),
            Step("send_text", "mode"),
            Step(
                "judge",
                "This is now opencode's MODEL picker ('Select model'), filtered by 'mode'. Is the "
                "list FREE of amplifier MODE entries -- i.e. there is NO row 'Mode: plan', "
                "'Mode: brainstorm', 'mode-plan', or 'mode-brainstorm' offered as a selectable "
                "model? Answer YES if no such mode row appears (only genuine LLM models such as "
                "Claude/GPT, or 'No results found'). Answer NO if any mode entry like "
                "'Mode: plan' or 'Mode: brainstorm' appears as a selectable model.",
            ),
            Step("send_keys", "Escape"),
        ],
    ),
    # Activating a mode agent must NOT surface an invalid-model error. The mode
    # agent files declare ``model: amplifier/mode-<name>``; if that alias is not a
    # model opencode considers valid, opencode rejects the agent with e.g.
    # "Agent plan (Amplifier)'s configured model amplifier/mode-plan is not valid".
    # So a mode's model reference must stay RESOLVABLE for opencode even though the
    # alias must not appear in the /models picker. This case selects plan (Amplifier)
    # and asserts no such error. EXPECTED TO FAIL while the alias is hidden from
    # opencode's model list without being made otherwise valid.
    TUICase(
        "mode-activation-no-invalid-model-error",
        [
            _OPEN_AGENT_LIST,
            Step("wait", _AGENT_DIALOG_MARKER),
            Step("send_text", _mode_agent("plan")),
            Step("send_keys", "Enter"),
            Step(
                "judge",
                "The 'plan (Amplifier)' agent (an amplifier MODE) was just selected. Is the "
                "screen FREE of any error about the agent's configured model being invalid? "
                "Specifically, there must be NO message like \"Agent plan (Amplifier)'s "
                "configured model "
                "amplifier/mode-plan is not valid\", nor any 'model ... is not valid' / "
                "'invalid model' / 'unknown model' error anywhere on screen. Answer YES if no such "
                "error is present (the mode activated cleanly). Answer NO if any invalid/not-valid "
                "model error is shown.",
            ),
            Step("send_keys", "Escape"),
        ],
    ),
]


# Discovery first (fast, no model turn), then behavior (full send/settle round-trips),
# then the mode/model separation contract.
MODES_CASES: list[TUICase] = [*_DISCOVERY_CASES, *_BEHAVIOR_CASES, *_SEPARATION_CASES]
