#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Agent dashboards for TRMNL: setup ==="

if ! command -v python3 &>/dev/null; then
  echo "ERROR: python3 not found. Install Python 3 first."
  exit 1
fi

echo "Creating Python virtual environment..."
python3 -m venv venv
source venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
echo "  Installed: $(pip list --format=freeze | grep -E 'pexpect|pyte' | tr '\n' ' ')"

echo ""
echo "Agents with local data:"
(cd "$SCRIPT_DIR/.." && python3 -m agentdash --list)

echo ""
echo "Setup complete. Next steps:"
echo "  1. Create one TRMNL private plugin per agent and put each webhook URL in .env"
echo "     as TRMNL_WEBHOOK_URL_<AGENT> (see README)"
echo "  2. Run: ./run.sh --dry-run (inspect the payloads)"
echo "  3. Run: ./run.sh (post once)"
echo "  4. Run: ./install.sh (schedule every 10 min)"
