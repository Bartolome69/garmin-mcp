#!/bin/bash
# Create the virtual environment and install dependencies.
# Safe to re-run: it reuses an existing .venv.

set -euo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT"

if ! command -v uv >/dev/null 2>&1; then
    echo "This needs 'uv' to fetch a modern Python (macOS ships 3.9, too old)."
    echo "Install it with:"
    echo "    curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo "then open a new terminal and run this again."
    exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
    echo "Creating virtual environment (downloads Python 3.12 the first time)..."
    uv venv --python 3.12 .venv
fi

echo "Installing dependencies..."
VIRTUAL_ENV="$PROJECT/.venv" uv pip install --quiet -r requirements.txt

echo
echo "Done. Next:"
echo "    $PROJECT/scripts/login.sh"
