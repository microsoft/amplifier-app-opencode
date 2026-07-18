#!/usr/bin/env bash
#
# amplifier-opencode one-line installer.
#
#   curl -fsSL https://raw.githubusercontent.com/microsoft/amplifier-app-opencode/main/install.sh | bash
#
# Review-first (recommended):
#
#   curl -fsSL https://raw.githubusercontent.com/microsoft/amplifier-app-opencode/main/install.sh -o install.sh
#   less install.sh
#   bash install.sh
#
# What this does, and deliberately no more:
#   1. Ensures `uv` is available (installs it via the official astral script if missing).
#   2. Installs the `amplifier-opencode` CLI as a uv tool from GitHub.
#   3. Prints the next command to run.
#
# It intentionally does NOT install amplifier-agent or opencode, and does NOT
# run any credential wizard. That lifecycle lives INSIDE the CLI: the first
# `amplifier-opencode` launch (or `amplifier-opencode setup`) self-heals the
# rest of the stack and walks you through provider credentials. Keeping the
# bootstrap in the tool -- not this script -- is intentional: stdin here is the
# curl pipe, not you, so an interactive wizard cannot run correctly from here.
#
set -euo pipefail

REPO_URL="https://github.com/microsoft/amplifier-app-opencode.git"
REF="${AMPLIFIER_OPENCODE_REF:-main}"

info()  { printf '\033[36m%s\033[0m\n' "$*"; }
ok()    { printf '\033[32m%s\033[0m\n' "$*"; }
warn()  { printf '\033[33m%s\033[0m\n' "$*" >&2; }
die()   { printf '\033[31mError: %s\033[0m\n' "$*" >&2; exit 1; }

# --- OS guard --------------------------------------------------------------
# Native Windows (Git Bash / MSYS) cannot run this reliably; opencode itself
# recommends WSL. Detect and redirect rather than fail cryptically.
case "$(uname -s 2>/dev/null || echo unknown)" in
  MINGW* | MSYS* | CYGWIN*)
    die "Native Windows detected. Please run this inside WSL (\`wsl --install\`), then re-run."
    ;;
esac

# --- 1. ensure uv ----------------------------------------------------------
if command -v uv >/dev/null 2>&1; then
  ok "✓ uv found: $(uv --version 2>/dev/null || echo present)"
else
  info "uv not found; installing it via https://astral.sh/uv/install.sh ..."
  command -v curl >/dev/null 2>&1 || die "curl is required to install uv."
  curl -fsSL https://astral.sh/uv/install.sh | sh
  # The uv installer drops the binary in ~/.local/bin (or $XDG_BIN_HOME); make
  # sure it is visible to THIS shell so the next step can use it. Only prepend
  # $XDG_BIN_HOME when it is actually set -- an empty value would leave a "::"
  # in PATH, which POSIX reads as the current directory (a footgun).
  export PATH="${HOME}/.local/bin:${PATH}"
  [ -n "${XDG_BIN_HOME:-}" ] && export PATH="${XDG_BIN_HOME}:${PATH}"
  command -v uv >/dev/null 2>&1 || die "uv installed but not on PATH. Open a new terminal and re-run."
  ok "✓ uv installed: $(uv --version 2>/dev/null || echo present)"
fi

# --- 2. install the CLI ----------------------------------------------------
SPEC="git+${REPO_URL}@${REF}"
info "Installing amplifier-opencode from ${SPEC} ..."
uv tool install --force "${SPEC}"

# --- 3. what's next --------------------------------------------------------
echo
ok "✓ amplifier-opencode installed."
echo
info "Next step -- run:"
echo "    amplifier-opencode"
echo
echo "On first launch it will install amplifier-agent and opencode if needed,"
echo "and walk you through connecting a model provider. To do just that setup"
echo "without launching, run:  amplifier-opencode setup"
echo
echo "If 'amplifier-opencode' is not found, open a new terminal (uv adds"
echo "~/.local/bin to PATH) or run:  export PATH=\"\$HOME/.local/bin:\$PATH\""
