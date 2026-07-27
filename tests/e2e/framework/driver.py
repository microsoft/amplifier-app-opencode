"""``TmuxTuiDriver`` -- drive the opencode TUI reliably via tmux.

The TUI runs under a fixed-size tmux session; we send keystrokes with ``send-keys``
and read the rendered screen with ``capture-pane -p``. The same driver works locally
(``exec_prefix=None`` -> commands run as ``tmux ...``) and inside a Digital Twin
(``exec_prefix=["amplifier-digital-twin","exec","<id>","--"]`` -> commands run as
``amplifier-digital-twin exec <id> -- tmux ...``). No PTY flag is needed either way.

Proven driving rules baked in:
  * Fixed size ``-x 120 -y 40``; set ``remain-on-exit on`` right after ``new-session``
    so a crashed/exited app leaves a readable final screen instead of vanishing.
  * Pass each tmux token as a separate argv element -- never wrap in ``bash -c``.
  * ``capture-pane -p`` pads with trailing blank lines up to ``-y``; strip them.

Over the DTU boundary, ``amplifier-digital-twin exec`` wraps command output in a JSON
envelope (``{exit_code, stdout, stderr, ...}``); the driver unwraps it transparently
so ``capture()`` always returns the real screen text in both modes.
"""

from __future__ import annotations

import json
import subprocess
import time


def _strip_trailing_blank_lines(text: str) -> str:
    """Drop trailing all-blank lines that ``capture-pane -p`` pads up to ``-y``."""
    lines = text.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


class TmuxTuiDriver:
    """Send keystrokes to and capture the screen of a tmux-hosted TUI."""

    def __init__(
        self,
        session: str,
        exec_prefix: list[str] | None = None,
        cols: int = 120,
        rows: int = 40,
    ) -> None:
        self.session = session
        self.exec_prefix = list(exec_prefix) if exec_prefix else []
        self.cols = cols
        self.rows = rows
        self._via_dtu = bool(exec_prefix)

    # -- low-level -------------------------------------------------------- #

    def run_command(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        """Run ``argv`` in the target (local or DTU), unwrapping the DTU envelope.

        Used by the tmux calls and by ``assertions.py`` to run shell probes inside
        the same target the TUI lives in.
        """
        full = [*self.exec_prefix, *argv]
        proc = subprocess.run(full, capture_output=True, text=True)
        return self._unwrap(proc) if self._via_dtu else proc

    def _unwrap(self, proc: subprocess.CompletedProcess[str]) -> subprocess.CompletedProcess[str]:
        """Recover the real stdout/stderr/exit_code from the DTU exec JSON envelope."""
        try:
            envelope = json.loads(proc.stdout)
        except (json.JSONDecodeError, ValueError):
            return proc  # not JSON (e.g. the DTU CLI itself failed) -> pass raw through
        if not isinstance(envelope, dict) or "exit_code" not in envelope:
            return proc
        return subprocess.CompletedProcess(
            args=proc.args,
            returncode=int(envelope.get("exit_code", 0)),
            stdout=str(envelope.get("stdout", "")),
            stderr=str(envelope.get("stderr", "")),
        )

    def _tmux(self, *args: str) -> subprocess.CompletedProcess[str]:
        """Build ``exec_prefix + ["tmux", *args]`` and run it."""
        return self.run_command(["tmux", *args])

    # -- lifecycle -------------------------------------------------------- #

    def spawn(self, command: str) -> None:
        """Start a detached, fixed-size tmux session running ``command``."""
        self._tmux(
            "new-session",
            "-d",
            "-s",
            self.session,
            "-x",
            str(self.cols),
            "-y",
            str(self.rows),
            command,
        )
        # Keep the final screen readable if the app exits/crashes.
        self._tmux("set-option", "-t", self.session, "remain-on-exit", "on")

    def close(self) -> None:
        """Kill the tmux session (best-effort)."""
        self._tmux("kill-session", "-t", self.session)

    # -- input ------------------------------------------------------------ #

    def send_keys(self, *keys: str) -> None:
        """Send named keys (e.g. ``"Enter"``, ``"Left"``) via ``tmux send-keys``."""
        self._tmux("send-keys", "-t", self.session, *keys)

    def send_text(self, text: str) -> None:
        """Send literal text (``-l``) via ``tmux send-keys -l -- <text>``."""
        self._tmux("send-keys", "-t", self.session, "-l", "--", text)

    # -- output ----------------------------------------------------------- #

    def capture(self) -> str:
        """Return the current screen text, trailing blank lines stripped."""
        proc = self._tmux("capture-pane", "-t", self.session, "-p")
        return _strip_trailing_blank_lines(proc.stdout)

    def wait_for_text(
        self,
        marker: str,
        timeout: float = 30.0,
        poll: float = 0.5,
        regex: bool = False,
    ) -> str:
        """Poll ``capture()`` until ``marker`` appears; return the final screen.

        Args:
            marker: Substring, or a regex when ``regex=True``.
            timeout: Seconds to wait before raising.
            poll: Seconds between captures.
            regex: Treat ``marker`` as a regular expression (``re.search``).

        Raises:
            TimeoutError: If the marker does not appear in time (includes the last
                captured screen for debugging).
        """
        import re

        deadline = time.monotonic() + timeout
        screen = ""
        while time.monotonic() < deadline:
            screen = self.capture()
            found = re.search(marker, screen) if regex else (marker in screen)
            if found:
                return screen
            time.sleep(poll)
        kind = "regex" if regex else "text"
        raise TimeoutError(
            f"timed out after {timeout:.0f}s waiting for {kind} {marker!r}\nlast screen:\n{screen}"
        )

    def wait_until_gone(
        self,
        marker: str,
        timeout: float = 60.0,
        poll: float = 0.5,
        regex: bool = False,
    ) -> str:
        """Poll ``capture()`` until ``marker`` is ABSENT; return the final screen.

        The inverse of ``wait_for_text``. Used to settle on a busy→idle transition:
        an in-progress indicator (e.g. the ``esc interrupt`` hint shown while the model
        streams) that is present during work and clears when the work is done. Because
        it keys off a live global indicator rather than a per-message marker, it is
        robust to stale markers left on screen from a previous turn.

        Raises:
            TimeoutError: If the marker is still present when the timeout elapses
                (includes the last captured screen for debugging).
        """
        import re

        deadline = time.monotonic() + timeout
        screen = ""
        while time.monotonic() < deadline:
            screen = self.capture()
            present = re.search(marker, screen) if regex else (marker in screen)
            if not present:
                return screen
            time.sleep(poll)
        kind = "regex" if regex else "text"
        raise TimeoutError(
            f"timed out after {timeout:.0f}s waiting for {kind} {marker!r} to disappear"
            f"\nlast screen:\n{screen}"
        )
