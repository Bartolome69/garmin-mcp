#!/bin/bash
# Log in to Garmin once and cache the session token.
# Needs a real terminal: it prompts for your password and any MFA code.

set -euo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "$PROJECT/.venv/bin/python" -m garmin_mcp.login
