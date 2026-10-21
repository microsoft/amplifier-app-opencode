"""Run the Amplifier HTTP face for one opencode working directory."""

import os
from pathlib import Path
import signal
import sys
import threading
import time
from typing import Literal


def _exit_with_parent() -> None:
    """Stop the face when the process that started it is gone, so a killed opencode leaves no server behind."""
    parent = os.getppid()

    def watch() -> None:
        while os.getppid() == parent:
            time.sleep(2)
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=watch, name="parent-watch", daemon=True).start()


def run(port: int, token_env: str, approvals: Literal["allow", "deny"]) -> int:
    # Tools the agent runs inherit this environment; the token stays with the face.
    token = os.environ.pop(token_env, "")
    if not token.strip():
        print(f"Set {token_env} to the face's bearer token.", file=sys.stderr)
        return 2

    from amplifier_agent import AgentOptions
    from amplifier_agent_http import Settings, create_app
    import uvicorn

    try:
        settings = Settings(token=token, bind="127.0.0.1", port=port)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    options = AgentOptions(approvals=approvals, working_directory=Path.cwd())
    _exit_with_parent()
    uvicorn.run(create_app(settings, options), host=settings.bind, port=settings.port, log_level="info")
    return 0
