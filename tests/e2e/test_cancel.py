from pathlib import Path

from support import Tui, Twin, latest_session, session_header, turns, wait_for

SESSION = "opencode-cancel"


def test_escape_cancels_amplifiers_turn(opencode: Twin, tmp_path: Path) -> None:
    project = opencode.project("cancel")
    tui = Tui.start(opencode, project, SESSION)
    try:
        tui.send("Run the shell command: sleep 300 && touch slept.txt . Then reply with exactly the word done")
        assert wait_for(lambda: tui.shows("sleep 300 && touch slept.txt"), timeout=180), tui.text()
        assert wait_for(lambda: opencode.run("pgrep -f '[s]leep 300'").exit_code == 0, timeout=30), tui.text()
        # opencode interrupts on the second Escape.
        tui.keys("Escape")
        assert wait_for(lambda: tui.shows("esc again to interrupt"), timeout=10), tui.text()
        tui.keys("Escape")
        assert wait_for(lambda: tui.shows("interrupted") and tui.idle(), timeout=60), tui.text()
    finally:
        tui.close()

    assert wait_for(lambda: opencode.run("pgrep -f '[s]leep 300'").exit_code != 0, timeout=30)
    assert not opencode.exists(f"{project}/slept.txt")
    session = session_header(latest_session(opencode, project))
    [turn] = turns(opencode.pull_sessions(project, tmp_path), session)
    assert turn["result"]["state"] == "cancelled"
