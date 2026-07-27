# File locations

Where amplifier-opencode and the processes it spawns read and write on disk.
Useful when debugging; end users don't normally need this (see the top-level
`README.md` for install and usage).

| Path | What |
|---|---|
| `<project-dir>/opencode.json` OR `~/.config/opencode/opencode.jsonc` | Generated config (overwritten on every launch) |
| `<tempdir>/amplifier-agent.log` | stdout/stderr from the spawned amplifier-agent process |
| `<tempdir>/amplifier-opencode-agent.pid` | PID of the spawned amplifier-agent |
| `~/.config/opencode/command/` OR `<project>/.opencode/command/` | Bridged skill commands (one `.md` per skill) |
| `~/.config/opencode/agent/` OR `<project>/.opencode/agent/` | Bridged mode agents (one `amplifier-<mode>.md` per mode) |
| `~/.config/opencode/.amplifier-generated-commands.json` OR `<project>/.opencode/.amplifier-generated-commands.json` | Ownership manifest for generated command files |
| `~/.config/opencode/.amplifier-generated-agents.json` OR `<project>/.opencode/.amplifier-generated-agents.json` | Ownership manifest for generated agent files |
| `~/.amplifier-agent/state/workspaces/` | amplifier-agent's session storage |
| `~/.amplifier-agent/credentials.json` | Persistent credentials (mode 0600) — set via `amplifier-agent auth` |
| `~/.local/share/opencode/log/opencode.log` | opencode's own log file |

`<tempdir>` is the platform temp directory: `$TMPDIR` (falling back to `/tmp`)
on macOS/Linux and `%TEMP%` on Windows, as resolved by Python's
`tempfile.gettempdir()`. It is **not** hardcoded to `/tmp`.
