#!/bin/bash
# Check the installation and say what is wrong with it.
set -euo pipefail
PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# See the note in login.sh: garmin_mcp is found via PYTHONPATH, not site-packages.
export PYTHONPATH="$PROJECT${PYTHONPATH:+:$PYTHONPATH}"
exec "$PROJECT/.venv/bin/python" -m garmin_mcp.doctor
