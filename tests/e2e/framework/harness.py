"""E2E test model + runner for TUI cases.

A test case is DATA: a name and an ordered list of ``Step``s. Each step is a small
verb -- send keys, send literal text, wait for a screen marker, ask the AI judge, or
assert on ground-truth artifacts (the agent log / session events). ``run_case``
dispatches steps against a live ``TmuxTuiDriver`` and ``AIUserJudge``.

Reusable step builders compose the shared opencode recipe (select Sonnet 5, then send
a message and wait for the reply to settle) so a new test is just a list of steps.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .assertions import agent_log_contains, session_events_exist
from .driver import TmuxTuiDriver
from .judge import AIUserJudge

StepKind = Literal[
    "send_keys",
    "send_text",
    "send_message",
    "submit_message",
    "wait",
    "judge",
    "assert_log",
    "assert_events",
]

# Busy indicator: opencode shows the ``esc interrupt`` hint in the footer WHILE the model
# streams a reply, and clears it when the turn settles. Keying settle off this live global
# state -- appear, then disappear -- is robust across turns, unlike a per-message completion
# marker (e.g. the ``· 2.3s`` duration), which lingers on screen from the PREVIOUS turn and
# would make a naive "wait for a duration" match instantly on the next turn.
BUSY_MARKER = "esc interrupt"
# How long to wait for generation to visibly start before assuming a very fast turn already
# finished. Real turns stream for seconds; polling catches the busy state well within this.
BUSY_APPEAR_TIMEOUT = 20.0


@dataclass(frozen=True)
class Step:
    """One action in a TUI case.

    Attributes:
        kind: The verb to dispatch.
        value: The payload -- keys (``send_keys``), literal text (``send_text``),
            a screen marker (``wait``), a yes/no question (``judge``), or a log
            pattern (``assert_log``). Unused by ``assert_events``.
        timeout: Seconds for ``wait`` steps.
        regex: Treat ``value`` as a regex for ``wait`` steps.
    """

    kind: StepKind
    value: str = ""
    timeout: float = 30.0
    regex: bool = False


@dataclass(frozen=True)
class TUICase:
    """A named, ordered sequence of steps (the pytest parametrize unit)."""

    name: str
    steps: list[Step]


def run_case(case: TUICase, driver: TmuxTuiDriver, judge: AIUserJudge) -> None:
    """Dispatch each step of ``case`` against the live driver + judge."""
    for i, step in enumerate(case.steps):
        label = f"[{case.name}] step {i} ({step.kind})"
        if step.kind == "wait":
            driver.wait_for_text(step.value, timeout=step.timeout, regex=step.regex)
        elif step.kind == "send_keys":
            driver.send_keys(*step.value.split())
        elif step.kind == "send_text":
            driver.send_text(step.value)
        elif step.kind == "send_message":
            _send_message(driver, step.value, settle_timeout=step.timeout)
        elif step.kind == "submit_message":
            _submit_message(driver, settle_timeout=step.timeout)
        elif step.kind == "judge":
            screen = driver.capture()
            verdict = judge.evaluate(screen, step.value)
            assert verdict.passed, (
                f"{label} judge failed: {verdict.reason}\nquestion: {step.value}\nscreen:\n{screen}"
            )
        elif step.kind == "assert_log":
            assert agent_log_contains(driver, step.value), (
                f"{label} expected agent log to contain {step.value!r}"
            )
        elif step.kind == "assert_events":
            assert session_events_exist(driver), f"{label} expected session events.jsonl to exist"
        else:  # pragma: no cover - exhaustiveness guard
            raise ValueError(f"unknown step kind: {step.kind!r}")


# --------------------------------------------------------------------------- #
# Reusable step builders (the shared opencode recipe)
# --------------------------------------------------------------------------- #


def setup_select_sonnet5() -> list[Step]:
    """Steps to reach the main screen and select ``Claude Sonnet 5`` (Amplifier).

    Opens the model picker (``/models`` + Enter), types ``sonnet 5 amplifier`` into the
    Search -- which narrows to exactly one row, disambiguating the two ``Claude Sonnet 5``
    entries (Anthropic vs Amplifier) -- selects it, and confirms the footer.
    """
    return [
        Step("wait", "tab agents", timeout=120.0),
        Step("send_text", "/models"),
        Step("send_keys", "Enter"),
        Step("wait", "Select model"),
        Step("send_text", "sonnet 5 amplifier"),
        Step("wait", "Claude Sonnet 5"),
        Step("send_keys", "Enter"),
        Step("wait", "Claude Sonnet 5 Amplifier"),
    ]


def send_and_settle(text: str, settle_timeout: float = 90.0) -> list[Step]:
    """Send a chat message and wait for the reply to fully settle.

    One ``send_message`` step: type the text, submit, and wait for generation to start
    (``esc interrupt`` appears) and then finish (it clears). Robust across turns because
    it keys off the live busy indicator, not a per-message completion marker that a
    prior turn leaves on screen.
    """
    return [Step("send_message", text, timeout=settle_timeout)]


def _send_message(driver: TmuxTuiDriver, text: str, settle_timeout: float) -> None:
    """Type ``text`` then submit and block until the reply settles."""
    driver.send_text(text)
    _submit_message(driver, settle_timeout=settle_timeout)


def _submit_message(driver: TmuxTuiDriver, settle_timeout: float) -> None:
    """Press Enter to submit the current input and block until the reply settles.

    Split out from ``_send_message`` so a caller that has already typed (and, for a
    slash command, dismissed opencode's autocomplete popup with Escape) can submit the
    existing input WITHOUT retyping -- retyping would re-open the popup and re-swallow
    the Enter.
    """
    driver.send_keys("Enter")
    # Generation kicks off: the interrupt hint streams in. A very fast turn could finish
    # before we sample it, so treat "never appeared" as "already done" and fall through.
    try:
        driver.wait_for_text(BUSY_MARKER, timeout=BUSY_APPEAR_TIMEOUT, poll=0.2)
    except TimeoutError:
        return
    # Settled: the interrupt hint clears when the reply finishes.
    driver.wait_until_gone(BUSY_MARKER, timeout=settle_timeout, poll=0.5)
