#!/bin/bash
# Log in to Garmin once and cache the session token.
# Needs a real terminal: it prompts for your password and any MFA code.

set -euo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Only the dependencies are installed into .venv, not the package itself, so the
# interpreter finds garmin_mcp on PYTHONPATH — the same way the Claude Desktop
# entry does. Without this the script works from inside the project directory and
# nowhere else, which is not how anyone runs it.
export PYTHONPATH="$PROJECT${PYTHONPATH:+:$PYTHONPATH}"
exec "$PROJECT/.venv/bin/python" -m garmin_mcp.login
