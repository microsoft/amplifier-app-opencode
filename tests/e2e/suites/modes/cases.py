"""Case data for the modes suite: discovery + behavior over the opencode TUI.

Two contracts are encoded here.

Group A (discovery): each amplifier mode is advertised in opencode's native agent list
dialog, PREFIXED with ``amplifier-``. The dialog is opened with the leader key (``ctrl+x``
then ``a``), which renders a "Select agent" picker listing every primary agent by name
(see opencode ``component/dialog-agent.tsx``). One case per built-in mode (``plan``,
``brainstorm``), one dedicated prefix-convention case, and one case per seeded project/user
probe mode. Each opens the dialog and asserts (via the AI judge) that the ``amplifier-<mode>``
agent name appears.

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

from framework.harness import Step, TUICase, send_and_settle

# Leader (``ctrl+x``) + ``a`` opens opencode's agent list dialog ("Select agent"). Sent as
# two tmux keys in one step: ``C-x`` (ctrl+x) then ``a``. See opencode keybind.ts
# (``agent_list: <leader>a``, ``LeaderDefault = ctrl+x``).
_OPEN_AGENT_LIST = Step("send_keys", "C-x a")
_AGENT_DIALOG_MARKER = "Select agent"


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
    actually resolved the name to an active agent. Until the launcher generates the
    ``amplifier-<mode>`` agents this fails with "No results found", which is the correct red.
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
            f"e.g. 'Amplifier-Plan') now shows as the active agent in the prompt footer/status "
            f"line? Answer NO if the picker is still open, shows 'No results found', or the "
            f"active agent is a different one such as Build.",
        ),
    ]


# --------------------------------------------------------------------------- #
# Group A -- discovery: each amplifier mode is a prefixed primary agent
# --------------------------------------------------------------------------- #
_DISCOVERY_CASES: list[TUICase] = [
    # Built-in amplifier modes, prefixed. A native opencode `plan` agent may also exist;
    # we specifically require amplifier's to be `amplifier-plan`.
    _discovery_case("discover-amplifier-plan", "amplifier-plan"),
    _discovery_case("discover-amplifier-brainstorm", "amplifier-brainstorm"),
    # Seeded project/user modes (see conftest), prefixed by the launcher.
    _discovery_case("discover-amplifier-project-mode", "amplifier-e2e-proj"),
    _discovery_case("discover-amplifier-user-mode", "amplifier-e2e-user"),
    # The explicit prefix-convention contract: amplifier modes must NOT collide with
    # opencode's native agents; they carry the `amplifier-` prefix.
    TUICase(
        "prefix-required-not-bare",
        [
            _OPEN_AGENT_LIST,
            Step("wait", _AGENT_DIALOG_MARKER),
            Step(
                "judge",
                "In the agent list, are amplifier's modes shown WITH an 'amplifier-' prefix "
                "(specifically 'amplifier-plan' and 'amplifier-brainstorm')? They must not be "
                "presented as bare 'plan'/'brainstorm'. A separate native opencode agent (e.g. "
                "'build' or a native 'plan') may also be present, which is fine.",
            ),
            Step("send_keys", "Escape"),
        ],
    ),
]


# --------------------------------------------------------------------------- #
# Group B -- behavior: selecting a mode changes user-visible behavior
# --------------------------------------------------------------------------- #
_BEHAVIOR_CASES: list[TUICase] = [
    # Deterministic activation proof: the probe mode forces a sentinel line. Passes only if
    # selecting the mode-agent actually applied the mode's guidance end-to-end.
    TUICase(
        "activate-probe-mode-sentinel",
        [
            *_select_agent("amplifier-e2e-user"),
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
            *_select_agent("amplifier-plan"),
            *send_and_settle("hello"),
            *send_and_settle("are you still there?"),
            Step(
                "judge",
                "Is 'amplifier-plan' still the active agent/mode shown on screen after two "
                "turns? Its name may render title-cased (e.g. 'Amplifier-Plan').",
            ),
        ],
    ),
    # Plan mode is read-only: asked to write a file, it must respond with a plan and NOT
    # actually create/modify the file. Judged from the visible transcript.
    TUICase(
        "activate-amplifier-plan-behavior",
        [
            *_select_agent("amplifier-plan"),
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
            *_select_agent("amplifier-brainstorm"),
            *send_and_settle("build a URL shortener"),
            Step(
                "judge",
                "Is the response exploratory brainstorming/design discussion (questions, "
                "options, trade-offs) rather than a concrete implementation with code?",
            ),
        ],
    ),
]


# Discovery first (fast, no model turn), then behavior (full send/settle round-trips).
MODES_CASES: list[TUICase] = [*_DISCOVERY_CASES, *_BEHAVIOR_CASES]
