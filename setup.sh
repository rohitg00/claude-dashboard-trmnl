#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Claude Dashboard TRMNL — Setup ==="

if ! command -v python3 &>/dev/null; then
  echo "ERROR: python3 not found. Install Python 3 first."
  exit 1
fi

if ! command -v claude &>/dev/null; then
  echo "WARNING: claude not found in PATH."
  echo "  Install Claude Code: https://docs.anthropic.com/en/docs/claude-code"
  echo "  The scraper will fail without it."
fi

echo "Creating Python virtual environment..."
python3 -m venv venv
source venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
echo "  Installed: $(pip list --format=freeze | grep -E 'pexpect|pyte' | tr '\n' ' ')"

if [ ! -f .env ]; then
  echo ""
  echo "WARNING: .env file not found."
  echo "  cp .env.example .env"
  echo "  Then add your TRMNL_WEBHOOK_URL."
else
  source .env 2>/dev/null || true
  if [ -z "${TRMNL_WEBHOOK_URL:-}" ]; then
    echo "WARNING: TRMNL_WEBHOOK_URL is empty in .env"
  else
    echo "  TRMNL webhook: configured"
  fi
  if [ -n "${RIMURU_URL:-}" ]; then
    echo "  Rimuru URL: $RIMURU_URL"
  fi
fi

echo ""
echo "Setup complete. Next steps:"
echo "  1. Edit .env with your TRMNL webhook URL"
echo "  2. Run: ./run.sh (test the pipeline)"
echo "  3. Run: ./install.sh (schedule every 5 min)"
