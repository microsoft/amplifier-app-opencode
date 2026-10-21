import json
from pathlib import Path
import secrets
import shlex

from support import Tui, Twin, latest_session, session_header, turns, wait_for

SESSION = "opencode-approvals"


def test_opencode_asks_before_amplifier_runs_a_command(opencode: Twin, tmp_path: Path) -> None:
    project = opencode.project(f"approvals-{secrets.token_hex(4)}")
    config = json.dumps({"permission": {"bash": "ask"}})
    opencode.check(f"printf '%s\\n' {shlex.quote(config)} > opencode.json", workdir=project)
    tui = Tui.start(opencode, project, SESSION)
    try:
        tui.send("Run the shell command: touch approved.txt . Then reply with exactly the word done")
        assert wait_for(lambda: tui.shows("Permission required") and tui.shows("touch approved.txt"), timeout=180), (
            tui.text()
        )
        # Allow once.
        tui.keys("Enter")
        assert wait_for(lambda: opencode.exists(f"{project}/approved.txt") and tui.idle(), timeout=180), tui.text()

        tui.send("Run the shell command: touch rejected.txt . Then reply with exactly the word done")
        assert wait_for(lambda: tui.shows("Permission required") and tui.shows("touch rejected.txt"), timeout=180), (
            tui.text()
        )
        # Reject is the third choice.
        tui.keys("Right", "Right", "Enter")
        assert wait_for(tui.idle, timeout=120), tui.text()

        tui.send("Reply with exactly the word kiwi")
        assert wait_for(lambda: any(line.split()[:1] == ["kiwi"] for line in tui.screen()), timeout=180), tui.text()
    finally:
        tui.close()

    assert not opencode.exists(f"{project}/rejected.txt")
    session = session_header(latest_session(opencode, project))
    records = turns(opencode.pull_sessions(project, tmp_path), session)
    assert [turn["result"]["state"] for turn in records] == ["success", "rejected", "success"]
