#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

for env_file in "$PROJECT_DIR/.env" "$SCRIPT_DIR/.env"; do
  if [ -f "$env_file" ]; then
    set -a
    source "$env_file"
    set +a
    break
  fi
done

for venv in "$PROJECT_DIR/venv" "$SCRIPT_DIR/venv"; do
  if [ -f "$venv/bin/activate" ]; then
    source "$venv/bin/activate"
    break
  fi
done

for bin in claude grok; do
  BIN_PATH="$(command -v "$bin" 2>/dev/null || true)"
  if [ -n "$BIN_PATH" ]; then
    export PATH="$(dirname "$BIN_PATH"):$PATH"
  fi
done

exec python3 -m agentdash "$@"
