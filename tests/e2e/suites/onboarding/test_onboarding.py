"""DTU-backed test: a failed `amplifier-agent auth set` must never leak the plaintext key.

``docs/spec/providers-and-credentials.md``'s "Secret hygiene" section states the
contract this test proves: on a non-zero exit from the delegated ``auth set`` command,
"every occurrence of the plaintext value is replaced with ``***`` before the message is
shown to the user." ``_auth_set`` (``src/amplifier_app_opencode/onboarding.py``)
implements this as defense-in-depth on top of the stdin-only channel -- the key never
appears on argv, but if the delegated command ever echoes it back in its own error text
for any other reason, the scrub is the last thing standing between that echo and the
user's terminal (and scrollback).

Ground-truth assertions only -- no TUI, no AI judge. This drives ``amplifier-opencode
--yes setup`` under tmux (the wizard's key prompt uses hidden input, which needs a real
TTY) with a FAKE ``amplifier-agent`` binary prepended to PATH for that one invocation
(see ``conftest.py`` for why the real agent cannot be made to fail this way on demand,
and the safety precautions taken since this DTU is shared with every other suite).

Both assertions below are required together, per the task this suite protects:

* The sentinel secret must NEVER appear on screen. Alone, this is a vacuous check --
  it would pass just as well if the wizard never ran at all, or if the key prompt never
  appeared, or if the screen were blank.
* The redaction marker ``***`` MUST appear, inside the actual failure message. This
  proves the code reached the redaction branch and performed the substitution, not
  merely that the secret happens to be absent for some unrelated reason.

A third assertion (the failure message text itself is present) proves the run actually
executed the failure path end to end, rather than dying early on an unrelated error.
"""

from __future__ import annotations

import pytest
from framework.driver import TmuxTuiDriver
from suites.onboarding.conftest import FAKE_AGENT_VERSION, FAKE_BIN_DIR, SENTINEL

# ``dtu`` only -- deliberately NOT ``fresh_dtu``. ``amplifier-opencode setup`` never
# starts the amplifier-agent server and never depends on anything fixed at server
# startup (unlike the skills/modes discovery suites), so this suite has no need to force
# a fresh DTU provision and can safely reuse the warm one.
pytestmark = pytest.mark.dtu

_FAILURE_TEXT = "amplifier-agent auth set failed"


def _wait_no_deadline_exceeded(driver: TmuxTuiDriver, marker: str, *, timeout: float) -> str:
    """Thin wrapper over ``wait_for_text`` that always includes the marker in a clear
    failure if it times out (``wait_for_text`` already raises with the last screen
    attached; this just keeps call sites in this file uniform and short).
    """
    return driver.wait_for_text(marker, timeout=timeout)


def test_auth_set_failure_redacts_secret_from_the_screen(
    onboarding_driver: TmuxTuiDriver,
) -> None:
    """A key echoed back by a failing `auth set` must be scrubbed before it reaches the
    user's terminal.

    Drives the real wizard end to end against a fake ``amplifier-agent`` that reports
    zero resolvable providers (triggering onboarding) and then fails `auth set` while
    echoing the just-typed secret back on stderr (simulating exactly the leak
    `_auth_set`'s scrub defends against). See the module docstring for why both the
    "sentinel absent" and "*** present" assertions are required together, and why a
    third assertion on the failure text itself is not optional either.
    """
    driver = onboarding_driver
    # Defensive: a previous failed run (this test or an interrupted one) could have left
    # a stale session with this name; killing first makes this test self-sufficient
    # regardless of what a prior run left behind.
    driver.close()

    driver.spawn(f"env PATH={FAKE_BIN_DIR}:$PATH amplifier-opencode --yes setup")
    try:
        preflight_screen = _wait_no_deadline_exceeded(
            driver, "Checking prerequisites", timeout=30.0
        )
        assert FAKE_AGENT_VERSION in _wait_no_deadline_exceeded(
            driver, FAKE_AGENT_VERSION, timeout=30.0
        ), (
            f"expected the preflight to report the fake agent's version "
            f"({FAKE_AGENT_VERSION}), confirming the fake binary (not the real "
            f"amplifier-agent) was resolved first on PATH; screen:\n{preflight_screen}"
        )

        # The provider menu + "Choose a provider" prompt appears once the preflight
        # passes and `needs_onboarding()` triggers (the fake agent reports zero
        # resolvable providers). Accept the default (the only offered provider,
        # "anthropic") by pressing Enter with no typed input.
        menu_screen = _wait_no_deadline_exceeded(driver, "Choose a provider", timeout=30.0)
        assert "Anthropic (Claude)" in menu_screen, (
            f"expected the wizard's provider menu to list Anthropic (the only provider "
            f"the fake agent reports); screen:\n{menu_screen}"
        )
        driver.send_keys("Enter")

        # The key prompt uses hidden input (`click.prompt(..., hide_input=True)`), so
        # the sentinel is never echoed as it is typed -- this is exactly what makes the
        # "sentinel absent from the final screen" assertion below meaningful: if it
        # shows up later, it can only have gotten there via our own printed message.
        key_screen = _wait_no_deadline_exceeded(driver, "Anthropic API key", timeout=30.0)
        assert SENTINEL not in key_screen, (
            f"the sentinel must not be visible even at the moment of typing (hidden "
            f"input); screen:\n{key_screen}"
        )
        driver.send_text(SENTINEL)
        driver.send_keys("Enter")

        # `_auth_set` prints its "Storing ... credentials" line before invoking the
        # subprocess, then the failure line after. Wait for the terminal state
        # ("Setup complete" always prints at the end of `setup`, regardless of whether
        # onboarding succeeded) so the full failure message has had time to render.
        final_screen = _wait_no_deadline_exceeded(driver, "Setup complete", timeout=30.0)
    finally:
        driver.close()

    # Assertion 1: the plaintext sentinel must NEVER appear anywhere on the final
    # screen. Alone this is vacuous (it would pass on a blank screen too) -- it only
    # means something in combination with assertions 2 and 3 below.
    assert SENTINEL not in final_screen, (
        "SECRET LEAK: the sentinel value appeared on screen after a failed "
        "`amplifier-agent auth set`. The redaction in `_auth_set` did not scrub it "
        f"before printing. Full captured screen:\n{final_screen}"
    )

    # Assertion 2: the redaction marker must be present, and specifically INSIDE the
    # failure message -- proving the code actually reached the redaction branch and
    # performed the substitution, not merely that the secret is coincidentally absent.
    assert "***" in final_screen, (
        "expected the redaction marker '***' in place of the scrubbed secret, but it "
        f"was not found anywhere on screen. Full captured screen:\n{final_screen}"
    )

    # Assertion 3: the failure path actually executed, rather than the run dying early
    # for an unrelated reason (which could otherwise make assertion 1 pass vacuously).
    assert _FAILURE_TEXT in final_screen, (
        f"expected the documented failure message ({_FAILURE_TEXT!r}) proving the "
        f"`auth set` failure path executed; full captured screen:\n{final_screen}"
    )

    # Tie assertions 2 and 3 together explicitly: the marker must be part of THIS
    # failure message, not an unrelated '***' elsewhere on screen.
    failure_line = next((line for line in final_screen.splitlines() if _FAILURE_TEXT in line), None)
    assert failure_line is not None and "***" in failure_line, (
        f"expected '***' inside the same line as the failure message, got failure "
        f"line: {failure_line!r}; full screen:\n{final_screen}"
    )
    assert SENTINEL not in failure_line, (
        f"the failure line itself must not contain the plaintext sentinel: {failure_line!r}"
    )
