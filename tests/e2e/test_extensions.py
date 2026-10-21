import json
import secrets
import shlex

from support import STATE, Twin, word

# A stdio MCP server with one tool, `get_token`, that returns the token it is started with.
TOKEN_SERVER = """
import json
import sys

TOOL = {"name": "get_token", "description": "Returns the project token.", "inputSchema": {"type": "object"}}


def send(message):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", **message}) + "\\n")
    sys.stdout.flush()


for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    method = request.get("method")
    if method == "initialize":
        version = request.get("params", {}).get("protocolVersion", "2025-06-18")
        info = {"name": "token", "version": "1.0.0"}
        send({"id": request["id"], "result": {"protocolVersion": version, "capabilities": {"tools": {}}, "serverInfo": info}})
    elif method == "tools/list":
        send({"id": request["id"], "result": {"tools": [TOOL]}})
    elif method == "tools/call":
        send({"id": request["id"], "result": {"content": [{"type": "text", "text": sys.argv[1]}]}})
    else:
        send({"id": request["id"], "error": {"code": -32601, "message": "method not found"}})
"""


def write(twin: Twin, path: str, content: str) -> None:
    twin.check(f"mkdir -p $(dirname {shlex.quote(path)}) && printf %s {shlex.quote(content)} > {shlex.quote(path)}")


def test_amplifier_uses_a_project_skill(opencode: Twin) -> None:
    project = opencode.project("skills")
    secret = f"word-{secrets.token_hex(4)}"
    write(
        opencode,
        f"{project}/.opencode/skills/secret-word/SKILL.md",
        f"---\nname: secret-word\ndescription: Tells the secret word of this project.\n---\nThe secret word is {secret}.\n",
    )

    reply = opencode.opencode_run(project, "Use the secret-word skill. Reply with only the secret word")

    assert word(reply.text) == secret


def test_amplifier_calls_a_tool_of_a_configured_mcp_server(opencode: Twin) -> None:
    project = opencode.project("mcp")
    token = f"mcp-{secrets.token_hex(4)}"
    write(opencode, f"{project}/token_server.py", TOKEN_SERVER)
    command = ["uv", "run", "--no-project", "--quiet", "python", f"{project}/token_server.py", token]
    write(
        opencode,
        f"{project}/opencode.json",
        json.dumps({"mcp": {"servers": {"token": {"type": "local", "command": command}}}}),
    )

    reply = opencode.opencode_run(project, "Call the get_token tool of the token MCP server. Reply with only the token")

    assert word(reply.text) == token
    messages = [message for message in opencode.export(reply.session_id)["messages"] if message["type"] == "assistant"]
    tools = [part for message in messages for part in message["content"] if part["type"] == "tool"]
    called = [part for part in tools if part["name"] == "mcp_token_get_token"]
    assert called, tools
    assert called[0]["state"]["status"] == "completed", called[0]
    assert any(token in content.get("text", "") for content in called[0]["state"]["content"]), called[0]


def test_the_face_restarts_after_it_crashes(opencode: Twin) -> None:
    project = opencode.project("restart")
    first = opencode.opencode_run(project, "Reply with exactly the word plum")
    assert word(first.text) == "plum"

    # The face of this project, killed as a crash would end it.
    killed = opencode.check(
        "for pid in $(pgrep -f 'amplifier-opencode face'); do "
        f'[ "$(readlink /proc/$pid/cwd)" = {shlex.quote(project)} ] && kill -9 $pid && echo $pid; done; true'
    ).split()
    assert len(killed) == 1, killed

    second = opencode.opencode_run(project, "Reply with exactly the word quince")

    assert word(second.text) == "quince"
    log = opencode.read(f"{STATE}/plugin.log")
    assert f"the face for {project} exited with SIGKILL; restarting it" in log, log[-3000:]
