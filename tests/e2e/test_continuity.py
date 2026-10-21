from pathlib import Path
import secrets

from support import Twin, durable_sessions, session_header, turns, word


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
    assert [turn["result"]["state"] for turn in turns(sessions, session)] == ["success", "success"]
