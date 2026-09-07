#!/usr/bin/env bash
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
BIN_HOME="${HOME}/.local/bin"
DEST="$DATA_HOME/ai-agent-stack"
python3 "$SRC/ai_stack/install.py" "$SRC" "$DEST" "$BIN_HOME"
cat <<EOF
Installed AI Agent Stack $($DEST/bin/ai --version) globally.

  engine: $DEST
  command: $BIN_HOME/ai
  repo state: ${XDG_CONFIG_HOME:-$HOME/.config}/ai-agent-stack/repos/<repo-id>/

No files were written into your working repositories.
EOF
case ":$PATH:" in *":$BIN_HOME:"*) ;; *) echo; echo "Add to PATH: export PATH=\"$BIN_HOME:\$PATH\"";; esac
echo
echo "Optional tools:"
echo "  Context7: npm install -g ctx7"
echo "  Graphify: uv tool install graphifyy"
echo "  Code Review Graph: uv tool install code-review-graph"
echo "  CodeGraph: npm i -g @colbymchenry/codegraph"
echo "  RTK: brew install rtk"
