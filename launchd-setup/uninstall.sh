#!/usr/bin/env bash
set -euo pipefail

LABEL="com.claude-dashboard-trmnl.agent"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"

echo "=== Uninstalling scheduled job ==="

if [ -f "$PLIST" ]; then
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Removed: $PLIST"
else
  echo "No plist found at $PLIST — nothing to uninstall."
fi

echo "Done. The venv and source files are still here if you want them."
