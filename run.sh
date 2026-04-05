#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

source venv/bin/activate

CLAUDE_BIN="$(command -v claude 2>/dev/null || echo "")"
if [ -n "$CLAUDE_BIN" ]; then
  CLAUDE_DIR="$(dirname "$CLAUDE_BIN")"
  export PATH="$CLAUDE_DIR:$PATH"
fi

python3 post_trmnl.py
