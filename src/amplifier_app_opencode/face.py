"""Run the Amplifier HTTP face for one opencode working directory."""

import json
import os
from pathlib import Path
import signal
import sys
import threading
import time
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from amplifier_agent import McpServer


def _exit_with_parent() -> None:
    """Stop the face when the process that started it is gone, so a killed opencode leaves no server behind."""
    parent = os.getppid()

    def watch() -> None:
        while os.getppid() == parent:
            time.sleep(2)
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=watch, name="parent-watch", daemon=True).start()


def _inputs(raw: str) -> tuple[list[str], list["McpServer"]]:
    """Existing skill directories and MCP servers from the plugin's JSON inputs."""
    from amplifier_agent import McpServer

    value: Any = json.loads(raw) if raw.strip() else {}
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    skills = [str(path) for path in value.get("skills") or [] if Path(path).is_dir()]
    fields = ("name", "transport", "command", "args", "env", "url", "headers")
    servers = [
        McpServer(**{key: server[key] for key in fields if key in server}) for server in value.get("mcp_servers") or []
    ]
    return skills, servers


def run(port: int, token_env: str, approvals: Literal["allow", "deny"], inputs_env: str | None = None) -> int:
    # Tools the agent runs inherit this environment; the token stays with the face.
    token = os.environ.pop(token_env, "")
    if not token.strip():
        print(f"Set {token_env} to the face's bearer token.", file=sys.stderr)
        return 2
    # MCP server environments and headers can hold secrets; they stay with the face as well.
    raw = os.environ.pop(inputs_env, "") if inputs_env else ""
    try:
        skills, servers = _inputs(raw)
    except (ValueError, TypeError) as error:
        print(f"{inputs_env} does not hold valid face inputs: {error}", file=sys.stderr)
        return 2

    from amplifier_agent import AgentOptions
    from amplifier_agent_http import Settings, create_app
    import uvicorn

    try:
        settings = Settings(token=token, bind="127.0.0.1", port=port)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    options = AgentOptions(
        approvals=approvals,
        working_directory=Path.cwd(),
        skills=skills or None,
        mcp_servers=servers or None,
    )
    _exit_with_parent()
    uvicorn.run(create_app(settings, options), host=settings.bind, port=settings.port, log_level="info")
    return 0
