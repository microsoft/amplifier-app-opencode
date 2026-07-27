# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.3] — 2026-07-27

### Added

- **Skills bridge.** amplifier-agent skills become opencode slash commands
  (`/<name>`, running server-side). Written to `~/.config/opencode/command/`
  globally or `<project>/.opencode/command/` with `--project-dir`.
- **Modes bridge.** amplifier-agent modes become opencode primary agents,
  shown as `<mode> (Amplifier)` in the agent picker. Written to
  `~/.config/opencode/agent/` or `<project>/.opencode/agent/`.
- Bridged files are ownership-tracked (`.amplifier-generated-commands.json`,
  `.amplifier-generated-agents.json`); a command or agent file you wrote
  yourself is never overwritten.
- Name conflicts (a skill or mode name found in more than one discovery
  location) are reported at launch, showing which file runs and which are
  shadowed.
- `amplifier-opencode setup` — install/heal the stack and connect a provider
  without launching.
- `--version` flag — reports amplifier-opencode's version plus the installed
  and required minimum amplifier-agent version.
- `update --no-opencode` — update amplifier-opencode and amplifier-agent
  while leaving opencode at its current version.
- Self-healing preflight on every launch (`--yes`, `--no-bootstrap`):
  installs/heals amplifier-agent and opencode, and walks through provider
  credential setup when none is configured.
- DTU-based end-to-end test framework driving the real opencode TUI
  (`tests/e2e/`).

### Changed

- Minimum required amplifier-agent version raised to `0.9.3`.

### Security

- Skill/mode names are validated as safe bare filenames before they can
  become a command or agent file, closing a path-traversal vector.
