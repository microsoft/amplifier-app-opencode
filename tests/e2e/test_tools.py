from decimal import Decimal
from pathlib import Path
import secrets

from support import Twin, session_header, turn_cost, turns, word


def test_amplifiers_tool_calls_show_as_opencode_tools(opencode: Twin) -> None:
    project = opencode.project("tools")
    token = f"shown-{secrets.token_hex(4)}"

    reply = opencode.opencode_run(
        project,
        f"Run the shell command: echo {token} . Then use your file writing tool to create the file shown.txt whose only "
        f"content is {token} . "
        "Then reply with exactly the word done",
    )

    assert word(reply.text) == "done"
    assert opencode.read(f"{project}/shown.txt").strip() == token
    messages = [message for message in opencode.export(reply.session_id)["messages"] if message["type"] == "assistant"]
    tools = [part for message in messages for part in message["content"] if part["type"] == "tool"]
    # Amplifier ran every call; shell commands and writes show as calls the provider ran.
    assert all(part["executed"] is True for part in tools if part["name"] in ("bash", "write")), tools
    shell = [part for part in tools if part["name"] == "bash" and token in part["state"]["input"].get("command", "")]
    assert shell, tools
    assert shell[0]["state"]["status"] == "completed"
    assert any(token in content["text"] for content in shell[0]["state"]["content"]), shell[0]
    written = [part for part in tools if part["name"] == "write" and part["state"]["status"] == "completed"]
    assert len(written) == 1, tools
    assert written[0]["state"]["input"]["path"].endswith("shown.txt"), written[0]
    assert written[0]["state"]["input"]["content"].strip() == token, written[0]
    assert written[0]["state"]["content"], written[0]


def test_edits_and_searches_show_their_diff_and_counts(opencode: Twin, tmp_path: Path) -> None:
    project = opencode.project("proxied")
    opencode.check(f"printf 'alpha\\nbeta\\ngamma\\n' > {project}/notes.txt")

    reply = opencode.opencode_run(
        project,
        "In notes.txt replace the line beta with BETA, editing the file in place. Then search the .txt files for the "
        "text BETA with your grep tool, and list the .txt files with your glob tool. "
        "Then reply with exactly the word done",
    )

    assert word(reply.text) == "done"
    assert opencode.read(f"{project}/notes.txt") == "alpha\nBETA\ngamma\n"
    messages = [message for message in opencode.export(reply.session_id)["messages"] if message["type"] == "assistant"]
    # Each batch of these calls ends a step, so the turn spans more than one assistant message.
    assert len(messages) >= 2, messages
    tools = [part for message in messages for part in message["content"] if part["type"] == "tool"]
    by_name = {part["name"]: part for part in tools if part["state"]["status"] == "completed"}

    [edit] = [part for name, part in by_name.items() if name in ("edit", "patch")]
    assert edit["executed"] is False, edit
    [changed] = edit["state"]["metadata"]["files"]
    assert changed["file"] == "notes.txt"
    assert changed["status"] == "modified"
    assert (changed["additions"], changed["deletions"]) == (1, 1)
    assert "-beta" in changed["patch"], changed
    assert "+BETA" in changed["patch"], changed

    assert by_name["grep"]["state"]["metadata"]["matches"] >= 1, by_name["grep"]
    assert by_name["glob"]["state"]["metadata"]["count"] >= 1, by_name["glob"]
    # Each step carries only the cost it added, so the steps add up to the turn's cost.
    [turn] = turns(opencode.pull_sessions(project, tmp_path), session_header(reply.session_id))
    spent = sum((Decimal(str(message["cost"])) for message in messages), Decimal(0))
    assert abs(spent - turn_cost(turn)) < Decimal("0.000001"), (spent, turn_cost(turn))
