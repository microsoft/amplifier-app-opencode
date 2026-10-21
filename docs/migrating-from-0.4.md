# Migrating from 0.4.x

`amplifier-opencode install` lists what 0.4.x generated and asks before it removes it. `--yes` removes it without
asking. It removes:

- the `provider.amplifier` block in `~/.config/opencode/opencode.jsonc`, `opencode.json`, or `config.json`, and in a
  project's `opencode.json`
- the command files listed in `.amplifier-generated-commands.json` and the agent files listed in
  `.amplifier-generated-agents.json`, and both manifests

Other changes:

- Credentials: `~/.amplifier-agent/credentials.json` (from `amplifier-agent auth set`) is no longer read. Connect the
  provider in opencode with `/connect` or `opencode auth login <provider>`, or set its key in the environment that
  starts opencode or in `environment` in `amplifier.json`.
- Commands: `launch`, `prepare`, `setup`, and `update` are gone. Run `opencode`, and update with
  `uv tool upgrade amplifier-app-opencode` followed by `amplifier-opencode install`.
- Flags and variables: `--base-url`, `--api-key`, `--workspace`, `--host-config`, `--project-dir`, `--no-start`,
  `--no-launch`, `--amplifier-agent-bin`, `--provider-id`, `--max-context`, `AMPLIFIER_AGENT_BASE_URL`,
  `AMPLIFIER_AGENT_API_KEY`, and `AMPLIFIER_OPENCODE_MAX_CONTEXT` are gone. The plugin picks its own port and token.
  Model, approvals, and credentials are options in `amplifier.json`.
- Modes are gone. Amplifier skills load from skill directories.
- Sessions from the old server are not imported.
