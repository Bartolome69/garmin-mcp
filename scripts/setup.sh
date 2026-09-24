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

# --only-binary refuses to compile anything from source. Without it, a machine
# with no matching prebuilt package silently starts a C and Rust build that needs
# a toolchain most people don't have, and fails minutes later in compiler output.
install() {
    VIRTUAL_ENV="$PROJECT/.venv" uv pip install --quiet --only-binary :all: "$@" \
        -r requirements.txt
}

echo "Installing dependencies..."
if ! install 2>/dev/null; then
    # The current packages need macOS 10.15+ on an Intel Mac. Older releases
    # still publish builds for 10.13, and carry every API this server uses.
    echo "No prebuilt packages for this machine; trying older releases..."
    if ! install --constraints constraints-legacy.txt; then
        cat >&2 <<'MSG'

Install failed: none of the available packages have a prebuilt build for this Mac.

That normally means macOS is older than 10.13. Check it under the Apple menu >
About This Mac. Updating macOS is the fix; building these from source needs Rust
and OpenSSL and is not worth the fight.

MSG
        exit 1
    fi
    echo "Installed older releases for compatibility with this macOS version."
fi

if [ "${GARMIN_MCP_BOOTSTRAP:-}" != "1" ]; then
    echo
    echo "Done. Next:"
    echo "    $PROJECT/scripts/login.sh"
fi
