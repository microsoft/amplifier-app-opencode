# Install

`amplifier-opencode` is a Python CLI installed with [`uv`](https://docs.astral.sh/uv/). The
installer installs only the CLI itself; every other component self-heals on first run.

## Recommended

```bash
curl -fsSL https://raw.githubusercontent.com/microsoft/amplifier-app-opencode/main/install.sh | bash
```

## Review the script first

```bash
curl -fsSL https://raw.githubusercontent.com/microsoft/amplifier-app-opencode/main/install.sh -o install.sh
less install.sh
bash install.sh
```

## What the installer does

Exactly three things, deliberately no more:

1. Checks for native Windows first and refuses outright (see Platform support below).
2. Ensures `uv` is on `PATH`, installing it via the official astral script if missing.
3. Installs the `amplifier-opencode` CLI as a `uv` tool from
   `git+https://github.com/microsoft/amplifier-app-opencode.git@main`.

It deliberately does **not** install `amplifier-agent`, does **not** install `opencode`, and does
**not** run any credential wizard. That whole lifecycle lives inside the CLI itself: the first
`amplifier-opencode launch` (or `amplifier-opencode setup`) self-heals the rest of the stack and
walks you through connecting a provider.

This split is not an oversight. Under `curl | bash`, stdin is the pipe, not your terminal, so an
interactive wizard cannot run correctly from inside the install script. Everything that needs a
real prompt waits until you run the CLI directly, where a real TTY is available.

After the installer finishes, only the `amplifier-opencode` binary exists. Run:

```bash
amplifier-opencode launch
```

On that first run, amplifier-opencode self-heals the rest: installs `amplifier-agent` if missing
or below the required minimum, installs `opencode` using the best method for your OS, and walks
you through connecting a provider if none is configured. Anything already installed and healthy
is left untouched. For the full self-heal state machine, see
[`spec/install-and-update.md`](spec/install-and-update.md).

Want just the setup without launching into the TUI? Run `amplifier-opencode setup`.

## Manual install

Prefer to install each piece yourself? You need a few system tools and three Amplifier
components.

### 0. System tools: `git`, `curl`

Most macOS installs already have these via Xcode Command Line Tools. On a fresh Linux container:

```bash
# Ubuntu/Debian
sudo apt-get update && sudo apt-get install -y git curl

# Fedora/RHEL
sudo dnf install -y git curl

# Arch
sudo pacman -S --noconfirm git curl
```

`git` is required because amplifier-agent and amplifier-app-opencode install via
`git+https://...` URLs (neither is on PyPI yet). `curl` is required by the `uv` and opencode
installers.

### 1. amplifier-agent (the backend server)

```bash
curl -fsSL https://raw.githubusercontent.com/microsoft/amplifier-agent/main/install.sh | bash

# ensure ~/.local/bin is on PATH, then verify
amplifier-agent version --json
```

amplifier-opencode enforces a minimum amplifier-agent version (the floor). The one-command install
path force-updates an older install automatically; by hand, upgrade with `amplifier-agent update`
and re-run `amplifier-opencode doctor` to confirm. See
[`spec/agent-integration.md`](spec/agent-integration.md) for what the floor is
and why it moves.

For source builds and amplifier-agent's own installer options, see the
[amplifier-agent README](https://github.com/microsoft/amplifier-agent#install).

### 2. opencode (the TUI)

```bash
curl -fsSL https://opencode.ai/install | bash
```

Places `opencode` in `~/.opencode/bin/` and appends an `export` line to your shell rc file. Open a
new terminal, then verify:

```bash
opencode --version
```

Non-interactive shells (containers, systemd services) don't source the rc file; add
`~/.opencode/bin` to `PATH` explicitly in those environments.

For Homebrew, a manual download, or a package manager, see
[opencode.ai/docs/intro](https://opencode.ai/docs/intro).

### 3. amplifier-app-opencode (this adapter)

```bash
uv tool install --from git+https://github.com/microsoft/amplifier-app-opencode amplifier-app-opencode
```

## Verify

```bash
amplifier-opencode doctor
```

Reports every prerequisite in one shot: both binaries, the running server, the opencode config,
live models, and which providers will actually be served. Exit code `0` when everything passes,
`1` on any failure.

## Update

```bash
amplifier-opencode update                # amplifier-opencode + amplifier-agent + opencode
amplifier-opencode update --no-opencode  # update the Amplifier pieces, leave opencode pinned
```

Updates all three components in order: self (hard gate for the whole command), then
amplifier-agent, then opencode (opt out with `--no-opencode`). A stale amplifier-agent or opencode
is not fatal to the command, since either can still heal on the next launch's preflight. Full
stage-by-stage contract: [`spec/install-and-update.md`](spec/install-and-update.md).

## Uninstall

```bash
uv tool uninstall amplifier-app-opencode
```

amplifier-agent and opencode are separate installs; remove them the same way you installed them
(`uv tool uninstall amplifier-agent`, or your OS package manager for opencode).

## Platform support

macOS, Linux, and WSL are fully supported. Native Windows is **not** supported by the shell
installer, which refuses outright:

```
Error: Native Windows detected. Please run this inside WSL (`wsl --install`), then re-run.
```

The CLI itself does run on native Windows once installed directly with `uv tool install`, but
opencode's own docs recommend WSL and the self-healing installers are bash-based, so WSL is the
supported path end to end.

## Related

- Full self-heal state machine and `update` contract:
  [`spec/install-and-update.md`](spec/install-and-update.md)
- Provider credentials and the onboarding wizard: [`CONFIGURATION.md`](CONFIGURATION.md)
- Agent version floor and lifecycle:
  [`spec/agent-integration.md`](spec/agent-integration.md)
