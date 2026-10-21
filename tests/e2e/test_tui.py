from support import Twin, wait_for

SESSION = "opencode-tui"


def test_the_tui_shows_amplifiers_reply(opencode: Twin) -> None:
    project = opencode.project("tui")
    opencode.check(
        f"tmux kill-session -t {SESSION} 2>/dev/null; tmux new-session -d -s {SESSION} -x 160 -y 45 opencode",
        workdir=project,
    )

    def screen() -> list[str]:
        return [line.strip() for line in opencode.run(f"tmux capture-pane -p -t {SESSION}").stdout.splitlines()]

    try:
        assert wait_for(lambda: any("Amplifier default" in line for line in screen()), timeout=120), screen()
        opencode.check(f"tmux send-keys -t {SESSION} -l 'Reply with exactly the word papaya'")
        opencode.check(f"tmux send-keys -t {SESSION} Enter")
        assert wait_for(lambda: "papaya" in screen(), timeout=180), "\n".join(screen())

        def closed_by_ctrl_c() -> bool:
            # send-keys fails once opencode has exited and taken the tmux session with it.
            return opencode.run(f"tmux send-keys -t {SESSION} C-c").exit_code != 0

        assert wait_for(closed_by_ctrl_c, timeout=30), "\n".join(screen())
    finally:
        opencode.run(f"tmux kill-session -t {SESSION}")
