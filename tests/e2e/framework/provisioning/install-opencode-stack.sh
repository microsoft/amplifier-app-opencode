#!/usr/bin/env bash
# Install the opencode + amplifier-agent + amplifier-app-opencode stack inside the DTU.
#
# BEST-EFFORT / SKELETON: this script encodes the intended install story but has NOT yet
# been validated against a live DTU. Correctness of the version pins, installer URLs, and
# PATH wiring is a milestone M2 task. Everything about HOW the stack is installed lives
# in THIS file; the profile skeleton and dtu.py do not change when the install story does.
set -euo pipefail

OPENCODE_VERSION="1.17.20"

echo "[install] OS prerequisites (git, curl, ca-certificates, tmux)"
if ! command -v git >/dev/null 2>&1 || ! command -v tmux >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq git curl ca-certificates tmux
fi

# uv drives the amplifier installs; bootstrap it if the base image lacks it.
if ! command -v uv >/dev/null 2>&1; then
  echo "[install] bootstrapping uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

echo "[install] opencode (pinned ${OPENCODE_VERSION})"
curl -fsSL https://opencode.ai/install | VERSION="${OPENCODE_VERSION}" bash

echo "[install] amplifier-agent"
uv tool install --reinstall --force \
  --from git+https://github.com/microsoft/amplifier-agent amplifier-agent
# Post-install hook (module wiring). Non-fatal if absent.
amplifier-agent-post-install || true

echo "[install] amplifier-app-opencode"
# The distribution package is `amplifier-app-opencode`; it provides the
# `amplifier-opencode` console script. The install REQUEST must name the package.
uv tool install --reinstall --force \
  --from git+https://github.com/microsoft/amplifier-app-opencode amplifier-app-opencode

# Ensure the opencode + uv tool bin dirs are on PATH for all future shells.
cat >/etc/profile.d/opencode.sh <<'EOF'
export PATH="$HOME/.opencode/bin:$HOME/.local/bin:$PATH"
EOF
export PATH="$HOME/.opencode/bin:$HOME/.local/bin:$PATH"

echo "[install] versions:"
opencode --version || true
amplifier-agent --version || true
amplifier-opencode --version || true
