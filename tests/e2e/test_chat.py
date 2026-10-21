from pathlib import Path

from support import Twin, durable_sessions, session_header, turns, word


def test_a_plain_run_goes_to_the_amplifier_agent(opencode: Twin, tmp_path: Path) -> None:
    project = opencode.project("chat")

    reply = opencode.opencode_run(project, "Reply with exactly the word pineapple")

    assert word(reply.text) == "pineapple"
    assistant = next(
        message for message in opencode.export(reply.session_id)["messages"] if message["type"] == "assistant"
    )
    assert assistant["agent"] == "amplifier"
    assert assistant["model"] == {"id": "default", "providerID": "amplifier"}
    sessions = opencode.pull_sessions(project, tmp_path)
    assert list(durable_sessions(sessions)) == [session_header(reply.session_id)]


def test_a_reasoning_variant_reaches_amplifier_with_its_model_and_effort(opencode: Twin, tmp_path: Path) -> None:
    project = opencode.project("chat-variant")

    reply = opencode.opencode_run(
        project, "Reply with exactly the word kiwi", "--model", "amplifier/anthropic-claude-opus-5-5#high"
    )

    assert word(reply.text) == "kiwi"
    sessions = opencode.pull_sessions(project, tmp_path)
    session = session_header(reply.session_id)
    assert durable_sessions(sessions)[session]["model"] == "claude-opus-5-5"
    [turn] = turns(sessions, session)
    assert turn["input"]["reasoning_effort"] == "high"
    assert turn["result"]["state"] == "success"
