#!/bin/bash
# One-shot installer: fetches the code, builds the environment, logs you in to
# Garmin, and registers the server with Claude Desktop.
#
# Safe to re-run — it updates an existing install rather than duplicating it.

set -euo pipefail

REPO="https://github.com/Bartolome69/garmin-mcp.git"
PROJECT="${GARMIN_MCP_DIR:-$HOME/garmin-mcp}"

say() { printf '\n\033[1m%s\033[0m\n' "$1"; }

if [ "$(uname -s)" != "Darwin" ]; then
    echo "This installer is written for macOS. On Linux or Windows, follow the" >&2
    echo "manual steps in the README instead." >&2
    exit 1
fi

# -- 1. uv, which supplies a modern Python (macOS ships 3.9, too old) ---------
if ! command -v uv >/dev/null 2>&1; then
    say "Installing uv (used to fetch Python)"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # The installer adds this to PATH for future shells; use it in this one.
    export PATH="$HOME/.local/bin:$PATH"
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "uv installed but isn't on PATH. Close this window, open a new one," >&2
    echo "and run this command again." >&2
    exit 1
fi

# -- 2. The code -------------------------------------------------------------
if [ -d "$PROJECT/.git" ]; then
    say "Updating $PROJECT"
    git -C "$PROJECT" pull --ff-only
else
    say "Downloading to $PROJECT"
    git clone --quiet "$REPO" "$PROJECT"
fi

# -- 3. Python environment ---------------------------------------------------
say "Setting up Python (first run downloads it, ~30 seconds)"
cd "$PROJECT"
[ -x ".venv/bin/python" ] || uv venv --python 3.12 .venv
VIRTUAL_ENV="$PROJECT/.venv" uv pip install --quiet -r requirements.txt

# -- 4. Garmin login ---------------------------------------------------------
if [ -f "$HOME/.garmin-mcp/tokens.json" ]; then
    say "Garmin session already saved — skipping login"
else
    say "Logging in to Garmin"
    echo "Your password is sent straight to Garmin and never saved to disk."
    echo "Only the session token Garmin issues is kept."
    echo
    "$PROJECT/.venv/bin/python" -m garmin_mcp.login
fi

# -- 5. Claude Desktop -------------------------------------------------------
# Claude Desktop loads its config at launch and writes its own copy back later,
# so a change made while it is running gets silently discarded.
if ps -Ao comm= | grep -x "/Applications/Claude.app/Contents/MacOS/Claude" >/dev/null; then
    say "Almost there — one manual step"
    cat <<MSG
Claude Desktop is running, and it will overwrite any change made now.

  1. Quit Claude Desktop completely (Cmd-Q, not just closing the window)
  2. Run this:

       $PROJECT/scripts/install-claude-desktop.sh

  3. Open Claude Desktop again

MSG
    exit 0
fi

"$PROJECT/scripts/install-claude-desktop.sh"

say "Done"
echo "Open Claude Desktop and start a new chat — it can now read your Garmin data."
