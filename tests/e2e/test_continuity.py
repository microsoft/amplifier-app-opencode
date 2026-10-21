import json
from pathlib import Path
import secrets
import shlex

import pytest
from support import Twin, context_sizes, durable_sessions, session_header, turn_cost, turns, word


def test_a_second_turn_answers_from_the_first_turns_tool_work(opencode: Twin, tmp_path: Path) -> None:
    project = opencode.project("continuity")
    token = f"token-{secrets.token_hex(4)}"
    opencode.check(f"echo {token} > secret.txt", workdir=project)

    first = opencode.opencode_run(
        project, "Read the file secret.txt in the current directory. Reply with exactly the word ready"
    )
    opencode.check("rm secret.txt", workdir=project)
    second = opencode.opencode_run(
        project,
        "secret.txt has been deleted. What was its content? Reply with only that content",
        "--session",
        first.session_id,
    )

    assert word(first.text) == "ready"
    assert word(second.text) == token
    assert second.session_id == first.session_id
    sessions = opencode.pull_sessions(project, tmp_path)
    session = session_header(first.session_id)
    assert list(durable_sessions(sessions)) == [session]
    assert durable_sessions(sessions)[session]["turn_count"] == 2
    records = turns(sessions, session)
    assert [turn["result"]["state"] for turn in records] == ["success", "success"]

    # opencode shows the last message's tokens as the context size and adds up message costs.
    messages = [message for message in opencode.export(first.session_id)["messages"] if message["type"] == "assistant"]
    sizes = context_sizes(sessions, session)
    assert len(messages) == len(sizes) == len(records) == 2
    for message, size, record in zip(messages, sizes, records, strict=True):
        cost = float(turn_cost(record))
        tokens = message["tokens"]
        assert size > 0
        assert cost > 0
        meter = tokens["input"] + tokens["output"] + tokens["reasoning"] + tokens["cache"]["read"]
        assert meter + tokens["cache"]["write"] == size, (tokens, size)
        assert tokens["output"] == tokens["reasoning"] == tokens["cache"]["read"] == 0, tokens
        assert abs(tokens["cache"]["write"] - cost) < 1e-6, (tokens, cost)
        assert abs(message["cost"] - cost) < 1e-6, (message["cost"], cost)


def test_a_session_continues_after_reasoning_is_sent_back(opencode: Twin, tmp_path: Path) -> None:
    project = opencode.project("continuity-reasoning")

    # The model reports its reasoning only when it reasons at some length, which this count almost always takes.
    for _ in range(5):
        first = opencode.opencode_run(
            project,
            "How many integers from 1 to 10000 have digits that add up to 20? Reply with only the number",
        )
        reasoned = next(
            message for message in opencode.export(first.session_id)["messages"] if message["type"] == "assistant"
        )
        if any(part["type"] == "reasoning" for part in reasoned["content"]):
            break
    else:
        pytest.fail("no first turn came back with reasoning")
    second = opencode.opencode_run(
        project, "Repeat your previous answer exactly, with nothing else", "--session", first.session_id
    )

    assert word(first.text) == "633"
    assert word(second.text) == "633"
    sessions = opencode.pull_sessions(project, tmp_path)
    session = session_header(first.session_id)
    assert durable_sessions(sessions)[session]["turn_count"] == 2
    assert [turn["result"]["state"] for turn in turns(sessions, session)] == ["success", "success"]


def test_a_session_continues_after_opencode_compacts_it(opencode: Twin, tmp_path: Path) -> None:
    # opencode's service keeps a directory's configuration while it runs, so a reused name could miss opencode.json.
    project = opencode.project(f"continuity-compaction-{secrets.token_hex(4)}")
    # A buffer larger than the context window puts every turn past opencode's automatic compaction threshold.
    config = json.dumps({"compaction": {"buffer": 100_000_000}})
    opencode.check(f"printf '%s\\n' {shlex.quote(config)} > opencode.json", workdir=project)

    first = opencode.opencode_run(project, "My favorite fruit is quince. Reply with exactly the word noted")
    second = opencode.opencode_run(
        project, "What is my favorite fruit? Reply with only the fruit", "--session", first.session_id
    )

    assert word(first.text) == "noted"
    assert word(second.text) == "quince"
    compactions = [
        message for message in opencode.export(first.session_id)["messages"] if message["type"] == "compaction"
    ]
    assert compactions, "opencode did not compact the session"
    assert all(message["reason"] == "auto" and message["status"] == "completed" for message in compactions)
    sessions = opencode.pull_sessions(project, tmp_path)
    session = session_header(first.session_id)
    assert list(durable_sessions(sessions)) == [session]
    assert durable_sessions(sessions)[session]["turn_count"] == 2
    assert [turn["result"]["state"] for turn in turns(sessions, session)] == ["success", "success"]
