# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Entries for 0.1.x were reconstructed from git history when this file was added
in 0.2.0.

## [Unreleased]

### Changed

- **Agent floor and pinned ref raised to 0.16.0.** This is an adoption bump, not a
  capability requirement: nothing here depends on what 0.16.0 changed. That release
  renamed the engine's built-in bundle (`amplifier-agent-behavioral-anchor` →
  `amplifier-agent-anchors`) and dropped descriptive prose from the head of its system
  prompt. Neither reaches this application — the HTTP face, `GET /v1/models`,
  `/v1/skills`, `/v1/modes`, host-config handling passed through `--host-config`,
  `PROTOCOL_VERSION`, and the `run`/`serve` contract are all unchanged, and nothing here
  names that bundle. The floor moves so `AGENT_PINNED_REF` — the exact tag the
  launch-time self-heal installs — tracks the current engine instead of drifting a
  release behind.

  Existing installs below the floor are healed at next launch: the preflight forces a
  reinstall to `v0.16.0` rather than trusting the agent's own `update`.

## [0.4.0] — 2026-07-29

### Changed

- **Agent floor and pinned ref raised to 0.12.0.** `--host-config` has always
  been forwarded to `amplifier-agent serve --config`, but below 0.12.0 the
  agent's HTTP face dropped the file's `provider.config` block on every turn, so
  the flag silently did less under `serve` than it does under `run`. 0.12.0 is
  the first version where it actually applies. This also makes the agent's new
  `debug.rawLlmPayloads` key reachable from opencode: below 0.12.0 it is
  rejected as an unknown top-level key. No launcher change was needed — the
  passthrough was already correct; only the engine it points at had to catch up.

## [0.3.0] — 2026-07-29

### Added

- E2E DTU profile forwards `GITHUB_TOKEN`, so the harness can exercise
  amplifier-agent's GitHub Copilot provider alongside anthropic. Optional —
  unset is fine and the stack runs anthropic-only. Requires amplifier-agent
  0.11.0+, which namespaces Copilot's model ids (`github-copilot/<model>`) so
  they no longer collide with the native provider's. No launcher change was
  needed: Copilot models flow through the existing `display_name` → opencode
  `name` mapping and render as `<Model> (GitHub)` in the picker.
- README documents GitHub Copilot as a provider option, including the
  `export GITHUB_TOKEN=$(gh auth token)` bridge.

### Changed

- Minimum required amplifier-agent version raised to `0.11.0`, the first
  release that namespaces reseller model ids. Below it, Copilot's
  `claude-sonnet-5` and `claude-opus-5` are served under ids byte-identical to
  the native anthropic provider's and silently capture its traffic — so the
  Copilot setup this release documents is only safe at or above that floor.
  `AGENT_PINNED_REF` follows it to `v0.11.0`, meaning the launch-time
  auto-install/self-heal now targets that tag.

### Fixed

- `__version__` was left at `0.1.3` when 0.2.0 shipped, so `--version` and
  `doctor` under-reported the installed adapter. Now tracks the version in
  `pyproject.toml`.

## [0.2.0] — 2026-07-27

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
- DTU-based end-to-end test framework driving the real opencode TUI
  (`tests/e2e/`).

### Changed

- Minimum required amplifier-agent version raised to `0.10.0`. That is the
  first release shipping `GET /v1/skills` and `GET /v1/modes`, which both
  bridges read; against an older agent the bridges silently produce nothing.

### Security

- Skill/mode names are validated as safe bare filenames before they can
  become a command or agent file, closing a path-traversal vector.

## [0.1.3] — 2026-07-21

### Added

- `--version` flag — reports amplifier-opencode's version plus the installed
  and required minimum amplifier-agent version.

## [0.1.2] — 2026-07-21

### Added

- `amplifier-opencode setup` — install/heal the stack and connect a provider
  without launching.
- Self-healing preflight on every launch (`--yes`, `--no-bootstrap`):
  installs/heals amplifier-agent and opencode, and walks through provider
  credential setup when none is configured. Auto-install targets a pinned
  amplifier-agent tag rather than a moving branch.
- `update --no-opencode` — update amplifier-opencode and amplifier-agent
  while leaving opencode at its current version.

### Changed

- Minimum required amplifier-agent version raised to `0.9.3`, the first
  release shipping `auth set --stdin` (used to hand the provider key to the
  agent off-argv).

## [0.1.1] — 2026-07-14

### Changed

- Minimum required amplifier-agent version raised to `0.9.1`, and
  `amplifier-opencode update` now cascades into updating the agent.

## [0.1.0] — 2026-06-20

### Added

- Initial release. `amplifier-opencode` launches opencode against a local
  amplifier-agent HTTP server, materializing opencode config from
  `GET /v1/models` on every launch. Includes the `doctor` subcommand.
