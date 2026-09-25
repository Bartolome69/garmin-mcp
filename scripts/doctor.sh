#!/bin/bash
# Check the installation and say what is wrong with it.
set -euo pipefail
PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "$PROJECT/.venv/bin/python" -m garmin_mcp.doctor
