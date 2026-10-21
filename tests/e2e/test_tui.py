import json
from pathlib import Path
import re

from support import Twin, durable_sessions, session_header, turns, wait_for

SESSION = "opencode-tui"
# The footer's context meter: tokens of the last message and the share of the model's context window.
METER = re.compile(r"\d+(\.\d)?K \(\d+%\)")


def test_the_tui_shows_amplifiers_reply_and_continues_after_compact(opencode: Twin, tmp_path: Path) -> None:
    project = opencode.project("tui")
    opencode.check(
        f"tmux kill-session -t {SESSION} 2>/dev/null; tmux new-session -d -s {SESSION} -x 160 -y 45 opencode",
        workdir=project,
    )

    def screen() -> list[str]:
        return [line.strip() for line in opencode.run(f"tmux capture-pane -p -t {SESSION}").stdout.splitlines()]

    def replied(text: str) -> bool:
        # A reply starts its line; the sidebar can share it.
        return any(line.split()[:1] == [text] for line in screen())

    try:
        assert wait_for(lambda: any("Amplifier · GPT 6.1 Sol" in line for line in screen()), timeout=120), screen()

        def medium() -> bool:
            if any(line.endswith("· medium") for line in screen()):
                return True
            # ctrl+t cycles the model's reasoning variants.
            opencode.check(f"tmux send-keys -t {SESSION} C-t")
            return False

        assert wait_for(medium, timeout=30, interval=1), screen()
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
        opencode.run(f"tmux kill-session -t {SESSION}")

    listed = json.loads(opencode.check("opencode session list --format json", workdir=project))
    latest = max((entry for entry in listed if entry["directory"] == project), key=lambda entry: entry["updated"])
    sessions = opencode.pull_sessions(project, tmp_path)
    session = session_header(latest["id"])
    assert durable_sessions(sessions)[session]["turn_count"] == 2
    assert [turn["result"]["state"] for turn in turns(sessions, session)] == ["success", "success"]
