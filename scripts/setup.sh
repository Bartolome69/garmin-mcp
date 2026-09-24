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
# See the note in bootstrap.sh: never fall back to compiling from source.
if ! VIRTUAL_ENV="$PROJECT/.venv" uv pip install --quiet --only-binary :all: -r requirements.txt; then
    cat >&2 <<'MSG'

Install failed while fetching dependencies.

The usual cause is macOS being too old for the prebuilt packages. On an Intel
Mac these need macOS 10.15 (Catalina) or newer; without a matching prebuilt
package your machine tries to compile it from source, which needs Rust and
OpenSSL and is not worth the fight.

Check your version:  Apple menu > About This Mac

If you are on 10.15 or newer and still see this, send whoever pointed you here
the last few lines above.

MSG
    exit 1
fi

echo
echo "Done. Next:"
echo "    $PROJECT/scripts/login.sh"
