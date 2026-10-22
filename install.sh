#!/usr/bin/env bash
# Install amplifier-app-opencode with uv and add its plugin to opencode.
set -euo pipefail

REF="${AMPLIFIER_OPENCODE_REF:-main}"

if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

uv tool install --force "git+https://github.com/microsoft/amplifier-app-opencode@${REF}"

BIN="$(uv tool dir --bin)"
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo "Add $BIN to your PATH to run amplifier-opencode." ;;
esac

"$BIN/amplifier-opencode" install "$@"
