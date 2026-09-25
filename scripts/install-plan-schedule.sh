#!/bin/bash
# Install (or reinstall) the daily job that refreshes and publishes the plan.
set -euo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.garmin-mcp.plan"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"

mkdir -p "$HOME/Library/LaunchAgents"
sed "s|__PROJECT__|$PROJECT|g" "$PROJECT/scripts/$LABEL.plist" > "$TARGET"

# bootout first so a reinstall picks up changes rather than silently keeping
# the old definition.
launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID" "$TARGET"

echo "Installed $LABEL — runs daily at 07:15."
echo "  check it:   launchctl print gui/$UID/$LABEL | head -5"
echo "  run now:    launchctl kickstart -k gui/$UID/$LABEL"
echo "  remove it:  launchctl bootout gui/$UID/$LABEL && rm $TARGET"
echo "  its log:    $PROJECT/.plan-refresh.log"
