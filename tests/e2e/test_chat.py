from pathlib import Path

from support import DEFAULT_MODEL, Twin, durable_sessions, session_header, turns, word


def test_a_run_goes_to_the_amplifier_agent(opencode: Twin, tmp_path: Path) -> None:
    project = opencode.project("chat")

    reply = opencode.opencode_run(project, "Reply with exactly the word pineapple", "--model", "amplifier/default")

    assert word(reply.text) == "pineapple"
    assistant = next(
        message for message in opencode.export(reply.session_id)["messages"] if message["type"] == "assistant"
    )
    assert assistant["agent"] == "amplifier"
    assert (assistant["model"]["providerID"], assistant["model"]["id"]) == ("amplifier", "default")
    sessions = opencode.pull_sessions(project, tmp_path)
    durable = durable_sessions(sessions)
    assert list(durable) == [session_header(reply.session_id)]
    assert durable[session_header(reply.session_id)]["model"] == DEFAULT_MODEL.split("/", 1)[1]


def test_a_reasoning_variant_reaches_amplifier_with_its_model_and_effort(opencode: Twin, tmp_path: Path) -> None:
    project = opencode.project("chat-variant")

    reply = opencode.opencode_run(
        project, "Reply with exactly the word kiwi", "--model", "amplifier/openai-gpt-6.1-sol#medium"
    )

    assert word(reply.text) == "kiwi"
    sessions = opencode.pull_sessions(project, tmp_path)
    session = session_header(reply.session_id)
    assert durable_sessions(sessions)[session]["model"] == "gpt-6.1-sol"
    [turn] = turns(sessions, session)
    assert turn["input"]["reasoning_effort"] == "medium"
    assert turn["result"]["state"] == "success"
