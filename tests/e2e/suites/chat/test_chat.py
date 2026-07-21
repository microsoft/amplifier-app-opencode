"""DTU-backed chat-flow tests driving the opencode TUI.

Thin wrapper: the cases are DATA in ``cases.py``; this file just parametrizes them
into ``run_case`` with the live ``opencode_session`` driver and ``judge`` fixtures.
Marked ``dtu`` so they self-skip without a warm DTU.
"""

from __future__ import annotations

import pytest
from framework.driver import TmuxTuiDriver
from framework.harness import TUICase, run_case
from framework.judge import AIUserJudge
from suites.chat.cases import CHAT_CASES

pytestmark = pytest.mark.dtu


@pytest.mark.parametrize("case", CHAT_CASES, ids=[c.name for c in CHAT_CASES])
def test_chat(case: TUICase, opencode_session: TmuxTuiDriver, judge: AIUserJudge) -> None:
    run_case(case, opencode_session, judge)
