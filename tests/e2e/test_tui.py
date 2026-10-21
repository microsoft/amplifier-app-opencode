import json
from pathlib import Path
import re

from support import Tui, Twin, durable_sessions, session_header, turns, wait_for

SESSION = "opencode-tui"
# The footer's context meter: tokens of the last message and the share of the model's context window.
METER = re.compile(r"\d+(\.\d)?K \(\d+%\)")


def test_the_tui_shows_amplifiers_reply_and_continues_after_compact(opencode: Twin, tmp_path: Path) -> None:
    project = opencode.project("tui")
    tui = Tui.start(opencode, project, SESSION)
    screen = tui.screen

    def replied(text: str) -> bool:
        # A reply starts its line; the sidebar can share it.
        return any(line.split()[:1] == [text] for line in screen())

    try:
        opencode.check(f"tmux send-keys -t {SESSION} -l 'Reply with exactly the word papaya'")
        opencode.check(f"tmux send-keys -t {SESSION} Enter")
        assert wait_for(lambda: replied("papaya"), timeout=180), "\n".join(screen())
        assert wait_for(lambda: any(METER.search(line) for line in screen()), timeout=30), "\n".join(screen())

        opencode.check(f"tmux send-keys -t {SESSION} -l /compact")
        assert wait_for(lambda: any("Compact session" in line for line in screen()), timeout=30), "\n".join(screen())
        opencode.check(f"tmux send-keys -t {SESSION} Enter")
        summary = "Amplifier manages this conversation's context itself."
        assert wait_for(lambda: any(summary in line for line in screen()), timeout=60), "\n".join(screen())
        opencode.check(
            f"tmux send-keys -t {SESSION} -l 'Which word did you reply with before? Reply with only that word, in capital letters'"
        )
        opencode.check(f"tmux send-keys -t {SESSION} Enter")
        assert wait_for(lambda: replied("PAPAYA"), timeout=180), "\n".join(screen())

        def closed_by_ctrl_c() -> bool:
            # send-keys fails once opencode has exited and taken the tmux session with it.
            return opencode.run(f"tmux send-keys -t {SESSION} C-c").exit_code != 0

        assert wait_for(closed_by_ctrl_c, timeout=30), "\n".join(screen())
    finally:
        tui.close()

    listed = json.loads(opencode.check("opencode session list --format json", workdir=project))
    latest = max((entry for entry in listed if entry["directory"] == project), key=lambda entry: entry["updated"])
    sessions = opencode.pull_sessions(project, tmp_path)
    session = session_header(latest["id"])
    assert durable_sessions(sessions)[session]["turn_count"] == 2
    assert [turn["result"]["state"] for turn in turns(sessions, session)] == ["success", "success"]


def test_the_tui_shows_an_edits_diff_and_amplifiers_usage(opencode: Twin) -> None:
    project = opencode.project("tui-edit")
    opencode.check(f"printf 'alpha\\nbeta\\ngamma\\n' > {project}/notes.txt")
    session = f"{SESSION}-edit"
    tui = Tui.start(opencode, project, session)

    def shown(pattern: str) -> bool:
        return any(re.search(pattern, line) for line in tui.screen())

    try:
        tui.send(
            "In notes.txt replace the line beta with BETA, editing the file in place. Then reply with exactly the word done"
        )
        assert wait_for(lambda: shown(r"(^|\s)done$") and tui.idle(), timeout=180), tui.text()
        # The diff of the edit, with its removed and added lines.
        assert shown(r"-\s*beta\b"), tui.text()
        assert shown(r"\+\s*BETA\b"), tui.text()
        # The sidebar's usage panel: the model that ran, and what the turn cost.
        assert tui.shows("Amplifier usage"), tui.text()
        assert tui.shows("openai/gpt-6.1-sol"), tui.text()
        assert shown(r"Turn \$\d+\.\d{4}"), tui.text()
        assert shown(r"Session \$\d+\.\d{4}"), tui.text()
    finally:
        tui.close()
    assert opencode.read(f"{project}/notes.txt") == "alpha\nBETA\ngamma\n"
