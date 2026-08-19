"""Regression: a replayed content-less assistant turn must survive the round trip.

See ``__init__.py`` for the bug this guards. Two things are asserted, both ground truth
(no TUI, no AI judge, nothing left to a model's discretion -- the triggering shape is
authored here rather than hoped for from a live completion):

1. the HTTP response to a hand-crafted replay carries no ``cache_control`` rejection;
2. the outbound Anthropic payload that request produced placed at least one
   ``cache_control`` marker, and placed none on an empty text block.

The second assertion is not redundant. Conversation-region caching is skipped entirely
unless prompt caching is on AND some message reaching the provider carries a ``metadata``
dict. When it is skipped no breakpoint is placed, the request trivially succeeds, and a
green response proves nothing. Reading the payload is what tells a real pass from a
vacuous one, and it checks the fixed behavior directly rather than inferring it from
whether Anthropic happened to complain.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from framework.driver import TmuxTuiDriver
from suites.prompt_cache.conftest import (
    HttpResult,
    cache_control_verdict,
    post_chat_completion,
    served_model_ids,
)

# ``dtu``, not ``fresh_dtu``: the ``agent_server`` fixture restarts the agent with its own
# ``--host-config`` and restores a default server on teardown, so the warm twin is handed
# back as the next suite expects. A full twin launch would buy nothing beyond that.
pytestmark = pytest.mark.dtu

# The model the rest of the e2e suite drives, as the anthropic provider serves it on the
# wire. Asserted against ``GET /v1/models`` first, so an ``unknown_model`` 400 can never be
# mistaken for the bug.
MODEL_ID = "claude-sonnet-5"

# Anthropic's verbatim rejection. This substring -- not a status code -- identifies the
# regression: amplifier-agent returns 200 with the error embedded in the assistant content
# when the turn had already started streaming.
CACHE_CONTROL_ERROR = "cache_control cannot be set for empty text blocks"

# How amplifier-agent labels any other exception that escaped a turn mid-stream.
AGENT_ERROR_MARKER = "[amplifier-agent error:"


def _agentic_replay_messages() -> list[dict[str, object]]:
    """The crafted conversation: an opencode continuation carrying content-less turns.

        user       -> "read the config"
        assistant  -> content null, tool_calls: [read_file]
        tool       -> the file contents
        user       -> "check the port"
        assistant  -> content null, tool_calls: [bash]
        tool       -> the command output
        assistant  -> content null, NO tool_calls   <- the breakpoint's secondary target
        user       -> "summarise"                   <- becomes this turn's prompt

    The breakpoint that used to break lands on the message before the final user turn, so
    the content-less turn without ``tool_calls`` sits exactly there, with nothing else in
    its content array to absorb the marker. The surrounding ``tool_calls`` turns are the
    realistic replay, and are content-less too, so the offending shape appears three times.

    ``arguments`` is a JSON-encoded STRING because that is what OpenAI's format specifies
    and what opencode sends; a real object would test a shape no client produces. The final
    message is ``role=user`` so it becomes the prompt and everything before it is history.
    """
    return [
        {
            "role": "user",
            "content": "Read config.toml and tell me which port the server listens on.",
        },
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_e2e_read_config",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": json.dumps({"path": "config.toml"}),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_e2e_read_config",
            "content": '[server]\nhost = "127.0.0.1"\nport = 8080\n',
        },
        {
            "role": "user",
            "content": "Now check whether anything is already listening on that port.",
        },
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_e2e_check_port",
                    "type": "function",
                    "function": {
                        "name": "bash",
                        "arguments": json.dumps({"command": "ss -ltn | grep :8080 || true"}),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_e2e_check_port",
            "content": "",
        },
        {"role": "assistant", "content": None},
        {
            "role": "user",
            "content": "Summarise what you found in one short sentence.",
        },
    ]


def _request_payload() -> dict[str, object]:
    """The crafted replay, non-streaming, minimal output.

    ``stream: false`` makes the response one JSON document to search. ``max_tokens`` is
    small because only ACCEPTANCE matters here, and this is a real model call.
    """
    return {
        "model": MODEL_ID,
        "messages": _agentic_replay_messages(),
        "stream": False,
        "max_tokens": 64,
    }


def _describe(result: HttpResult) -> str:
    """Render a response for a failure message: status plus the body, verbatim."""
    return (
        f"curl exit={result.curl_returncode} http_status={result.status}\n"
        f"response body:\n{result.body}"
    )


def _fail_unrelated(reason: str, result: HttpResult) -> None:
    """Fail with the response attached, labelled as an environment fault.

    Every failure here is an ordinary ``Failed``, so the classification lives in the
    message. This is the route for anything that means the guarded bug was never reached.
    """
    pytest.fail(f"{reason}\n{_describe(result)}")


def test_replayed_content_less_assistant_turn_is_accepted(agent_server: TmuxTuiDriver) -> None:
    """POST an opencode-style replay carrying ``"content": null`` assistant turns.

    Failures say which kind they are: an environment fault (the bug was never reached), a
    REGRESSION (it is back), or VACUOUS (no breakpoint was placed, so nothing was proven).
    """
    driver = agent_server

    served = served_model_ids(driver)
    assert MODEL_ID in served, (
        f"precondition failed: {MODEL_ID!r} is not served by this amplifier-agent, so the "
        f"request would 400 with 'unknown_model'. GET /v1/models advertised: {served!r}"
    )

    result = post_chat_completion(driver, _request_payload())

    # 1. Transport. curl itself failing means no server answered -- nothing was tested.
    if result.curl_returncode != 0:
        _fail_unrelated(
            "curl could not reach amplifier-agent (connection refused, timeout, or DNS).",
            result,
        )

    # 2. The regression, checked BEFORE the status code: an error raised after the stream
    #    opened comes back embedded in a 200's content, so the marker identifies it, not
    #    the status.
    if CACHE_CONTROL_ERROR in result.body:
        pytest.fail(
            "REGRESSION: Anthropic rejected a cache_control marker on an empty text block, "
            "produced by replaying an assistant turn whose content was null. "
            f"_stamps_empty_text_block should have prevented this.\n{_describe(result)}"
        )

    # 3. Any other non-200 is an environment problem.
    if result.status != "200":
        hint = ""
        if result.status == "404":
            hint = (
                "\nHINT: a bare 404 here is usually ANTHROPIC_BASE_URL set with the wrong "
                "/v1 suffix. A host-environment fault, not the bug."
            )
        _fail_unrelated(
            f"expected HTTP 200 from /v1/chat/completions, got {result.status}.{hint}",
            result,
        )

    # 4. A 200 whose body is not a chat.completion means something else broke.
    try:
        parsed = json.loads(result.body)
    except json.JSONDecodeError:
        _fail_unrelated("HTTP 200 with a body that is not JSON.", result)
        return  # unreachable; helps the type checker past pytest.fail
    if not isinstance(parsed, dict) or "choices" not in parsed:
        _fail_unrelated("HTTP 200 with JSON that is not a chat.completion (no 'choices').", result)

    # 5. Any OTHER mid-stream error is likewise unrelated.
    if AGENT_ERROR_MARKER in result.body:
        _fail_unrelated(
            "the turn failed with an amplifier-agent error other than the one under guard.",
            result,
        )

    # 6. Everything above only proves the request was ACCEPTED -- which is also what a
    #    request with no cache breakpoint at all looks like.
    _assert_payload_invariants(driver)


def _assert_payload_invariants(driver: TmuxTuiDriver) -> None:
    """Assert the breakpoint machinery ran, and that it stamped no empty text block.

    Reads the verdict the in-DTU extractor produces from the captured outbound payload
    (see ``conftest.cache_control_verdict``).
    """
    verdict: dict[str, Any] = cache_control_verdict(driver)

    if not verdict.get("requests_with_raw"):
        pytest.fail(
            "no llm:request event carried a 'raw' payload, so the outbound request could "
            "not be inspected. Expected agent_server to have started amplifier-agent with "
            f"debug.rawLlmPayloads on. Broken setup, not a regression.\nverdict: {verdict!r}"
        )

    if not verdict.get("cache_control_count"):
        pytest.fail(
            "VACUOUS: the outbound request carried no cache_control marker at all, so no "
            "breakpoint was placed and the successful response above proved nothing. "
            "Caching is skipped whole when prompt caching is off or no message carries a "
            "'metadata' dict. This is not a regression -- it is a test that must be "
            f"repaired rather than ignored.\nverdict: {verdict!r}"
        )

    empty_stamped = verdict.get("empty_stamped") or []
    if empty_stamped:
        pytest.fail(
            "REGRESSION: a cache_control marker sits on an empty or whitespace-only text "
            "block -- the shape Anthropic rejects. The breakpoint walk must skip such "
            f"candidates.\noffending blocks: {empty_stamped!r}\nverdict: {verdict!r}"
        )
