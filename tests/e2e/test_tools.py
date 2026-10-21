import secrets

from support import Twin, word


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
    [message] = [message for message in opencode.export(reply.session_id)["messages"] if message["type"] == "assistant"]
    tools = [part for part in message["content"] if part["type"] == "tool"]
    assert all(part["executed"] is True for part in tools), tools
    shell = [part for part in tools if part["name"] == "bash" and token in part["state"]["input"].get("command", "")]
    assert shell, tools
    assert shell[0]["state"]["status"] == "completed"
    assert any(token in content["text"] for content in shell[0]["state"]["content"]), shell[0]
    written = [part for part in tools if part["name"] == "write" and part["state"]["status"] == "completed"]
    assert len(written) == 1, tools
    assert written[0]["state"]["input"]["path"].endswith("shown.txt"), written[0]
    assert written[0]["state"]["input"]["content"].strip() == token, written[0]
    assert written[0]["state"]["content"], written[0]
