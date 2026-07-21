"""DTU-backed skills tests driving the opencode TUI.

Thin wrapper: the cases are DATA in ``cases.py``; this file just parametrizes them into
``run_case`` with the ``skills_session`` driver (which seeds probe skills before the TUI
launches) and the ``judge`` fixture. Marked ``dtu`` so they self-skip without a warm DTU.
"""

from __future__ import annotations

import pytest
from framework.driver import TmuxTuiDriver
from framework.harness import TUICase, run_case
from framework.judge import AIUserJudge
from suites.skills.cases import SKILLS_CASES

pytestmark = pytest.mark.dtu


@pytest.mark.parametrize("case", SKILLS_CASES, ids=[c.name for c in SKILLS_CASES])
def test_skills(case: TUICase, skills_session: TmuxTuiDriver, judge: AIUserJudge) -> None:
    run_case(case, skills_session, judge)
