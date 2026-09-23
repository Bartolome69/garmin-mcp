#!/bin/bash
# Add the garmin server to Claude Desktop's config.
#
# Claude Desktop loads that file at launch and writes its own copy back later,
# so an edit made while the app is running gets silently discarded. This
# refuses to run rather than making a change that will vanish.

set -euo pipefail

CONFIG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
# Resolve the project from this script's own location, so the checkout can
# live anywhere.
PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$PROJECT/.venv/bin/python"

# Match the main app process exactly. `pgrep -f` also matches helper
# processes and the lowercase claude-code binary, which would either
# false-positive or, as it turned out, quietly match nothing.
# No `grep -q` here: it exits on first match, ps takes SIGPIPE, and with
# `pipefail` the whole pipeline reports 141 — so the guard never fired.
if ps -Ao comm= | grep -x "/Applications/Claude.app/Contents/MacOS/Claude" >/dev/null; then
    echo "Claude Desktop is still running."
    echo "Quit it completely (Cmd-Q, not just closing the window), then run this again."
    exit 1
fi

if [ ! -x "$PYTHON" ]; then
    echo "No interpreter at $PYTHON — run scripts/setup.sh first." >&2
    exit 1
fi

# The email only labels the connection in get_connection_status;
# authentication itself runs off the cached token.
if [ -z "${GARMIN_EMAIL:-}" ]; then
    read -r -p "Garmin email (used only to label the connection): " GARMIN_EMAIL
fi

if [ ! -f "$CONFIG" ]; then
    echo '{}' > "$CONFIG"
fi

cp "$CONFIG" "$CONFIG.backup-$(date +%Y%m%d-%H%M%S)"

GARMIN_EMAIL="${GARMIN_EMAIL:-}" "$PYTHON" - "$CONFIG" "$PROJECT" <<'PY'
import json, os, sys

config_path, project = sys.argv[1], sys.argv[2]
with open(config_path) as fh:
    config = json.load(fh)

config.setdefault("mcpServers", {})["garmin"] = {
    "command": f"{project}/.venv/bin/python",
    "args": ["-m", "garmin_mcp"],
    "env": {
        "PYTHONPATH": project,
        "GARMIN_EMAIL": os.environ["GARMIN_EMAIL"],
    },
}

with open(config_path, "w") as fh:
    json.dump(config, fh, indent=2)
    fh.write("\n")

print("Added 'garmin' to", config_path)
print("Servers now configured:", ", ".join(config["mcpServers"]))
PY

echo
echo "Now open Claude Desktop. The garmin tools should appear in a new chat."
