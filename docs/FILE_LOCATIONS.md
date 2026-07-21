# File locations

Where amplifier-opencode and the processes it spawns read and write on disk.
Useful when debugging; end users don't normally need this (see the top-level
`README.md` for install and usage).

| Path | What |
|---|---|
| `<project-dir>/opencode.json` OR `~/.config/opencode/opencode.jsonc` | Generated config (overwritten on every launch) |
| `<tempdir>/amplifier-agent.log` | stdout/stderr from the spawned amplifier-agent process |
| `<tempdir>/amplifier-opencode-agent.pid` | PID of the spawned amplifier-agent |

`<tempdir>` is the platform temp directory: `$TMPDIR` (falling back to `/tmp`)
on macOS/Linux and `%TEMP%` on Windows, as resolved by Python's
`tempfile.gettempdir()`. It is **not** hardcoded to `/tmp`.
| `~/.amplifier-agent/state/workspaces/` | amplifier-agent's session storage |
| `~/.amplifier-agent/credentials.json` | Persistent credentials (mode 0600) — set via `amplifier-agent auth` |
| `~/.local/share/opencode/log/opencode.log` | opencode's own log file |
