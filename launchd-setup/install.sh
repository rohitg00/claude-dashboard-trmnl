#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

LABEL="com.claude-dashboard-trmnl.agent"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
RUN_SCRIPT="$SCRIPT_DIR/run.sh"

CLAUDE_PATH_ENTRY=""
for bin in claude grok zstd; do
  BIN_PATH="$(command -v "$bin" 2>/dev/null || true)"
  if [ -n "$BIN_PATH" ]; then
    CLAUDE_PATH_ENTRY="$(dirname "$BIN_PATH"):${CLAUDE_PATH_ENTRY}"
  fi
done

echo "=== Installing scheduled job ==="
echo "  Script: $RUN_SCRIPT"
echo "  Schedule: every 10 minutes"
echo "  Plist: $PLIST"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${RUN_SCRIPT}</string>
  </array>
  <key>StartInterval</key>
  <integer>600</integer>
  <key>StandardOutPath</key>
  <string>/tmp/${LABEL}.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/${LABEL}.err</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>${CLAUDE_PATH_ENTRY}/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
    <key>HOME</key>
    <string>${HOME}</string>
  </dict>
</dict>
</plist>
EOF

echo ""
echo "About to install and load the launchd job."
read -r -p "Continue? [y/N] " confirm
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
  echo "Cancelled."
  rm -f "$PLIST"
  exit 0
fi

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo ""
echo "Installed. The script will run every 10 minutes."
echo ""
echo "Useful commands:"
echo "  launchctl start $LABEL          # run now"
echo "  tail -f /tmp/${LABEL}.log       # stdout"
echo "  tail -f /tmp/${LABEL}.err       # stderr"
echo "  ./uninstall.sh                  # remove"
