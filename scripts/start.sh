#!/bin/sh
# Starts the hosted server, under Litestream when off-site backup is set up.
#
# With LITESTREAM_BUCKET set (a Fly secret, with its keys), Litestream copies
# every change to the database to that bucket as it happens, and runs the
# server as its child. On an empty volume (a lost or replaced one) it first
# brings the database back from the bucket; on a volume that has one, it
# never touches it. Without LITESTREAM_BUCKET the server starts as it always
# did. See docs/RECOVERY.md.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export GARMIN_MCP_DB="${GARMIN_MCP_DB:-/data/garmin-mcp.sqlite3}"
CONFIG="${LITESTREAM_CONFIG:-/app/litestream.yml}"
SERVER="python -m garmin_mcp.hosted"

if [ -n "${LITESTREAM_BUCKET:-}" ]; then
  export LITESTREAM_REGION="${LITESTREAM_REGION:-auto}"
  export LITESTREAM_ENDPOINT="${LITESTREAM_ENDPOINT:-}"
  litestream restore -config "$CONFIG" -if-db-not-exists -if-replica-exists "$GARMIN_MCP_DB"
  exec litestream replicate -config "$CONFIG" -exec "$SERVER"
fi

exec $SERVER
