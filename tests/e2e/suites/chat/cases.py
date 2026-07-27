"""Case data for the chat suite: the core conversation loop over the opencode TUI.

``single-turn-hello`` selects Sonnet 5, sends "hello", and checks (via the AI judge)
that the assistant replied -- plus that the ground-truth agent log and session events
show the turn round-tripped. ``multi-turn-memory`` checks that memory carries across
two turns within one session. Add further TUI scenarios here as ``TUICase`` entries.
"""

from __future__ import annotations

from framework.harness import Step, TUICase, send_and_settle, setup_select_sonnet5

CHAT_CASES: list[TUICase] = [
    TUICase(
        "single-turn-hello",
        [
            *setup_select_sonnet5(),
            *send_and_settle("hello"),
            Step(
                "judge",
                "Does the assistant's reply respond to the user's greeting "
                "(e.g. a greeting or offer to help)?",
            ),
            Step("assert_log", "POST /v1/chat/completions"),
            Step("assert_events", ""),
        ],
    ),
    TUICase(
        "multi-turn-memory",
        [
            *setup_select_sonnet5(),
            *send_and_settle("remember that I like bananas"),
            *send_and_settle("what do I like?"),
            Step(
                "judge",
                "Does the assistant's most recent reply state that the user likes bananas?",
            ),
        ],
    ),
]
