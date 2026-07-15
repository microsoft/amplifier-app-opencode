# File locations

Where amplifier-opencode and the processes it spawns read and write on disk.
Useful when debugging; end users don't normally need this (see the top-level
`README.md` for install and usage).

| Path | What |
|---|---|
| `<project-dir>/opencode.json` OR `~/.config/opencode/opencode.jsonc` | Generated config (overwritten on every launch) |
| `/tmp/amplifier-agent.log` | stdout/stderr from the spawned amplifier-agent process |
| `/tmp/amplifier-opencode-agent.pid` | PID of the spawned amplifier-agent |
| `~/.amplifier-agent/state/workspaces/` | amplifier-agent's session storage |
| `~/.amplifier-agent/credentials.json` | Persistent credentials (mode 0600) — set via `amplifier-agent auth` |
| `~/.local/share/opencode/log/opencode.log` | opencode's own log file |
