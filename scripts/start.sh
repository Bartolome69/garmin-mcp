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

# A Tigris bucket made with `fly storage create -a <app>` sets its own secrets
# (BUCKET_NAME, AWS_ENDPOINT_URL_S3, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY).
# Use them when the LITESTREAM_* ones aren't set, so making the bucket is all
# it takes.
if [ -z "${LITESTREAM_BUCKET:-}" ] && [ -n "${BUCKET_NAME:-}" ] && [ -n "${AWS_ENDPOINT_URL_S3:-}" ]; then
  export LITESTREAM_BUCKET="$BUCKET_NAME"
  export LITESTREAM_ENDPOINT="$AWS_ENDPOINT_URL_S3"
  export LITESTREAM_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-}"
  export LITESTREAM_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-}"
  export LITESTREAM_REGION="${LITESTREAM_REGION:-auto}"
fi
# The web process and the background worker side by side (garmin_mcp/supervise.py).
# GARMIN_MCP_WORKER=inline runs the web process alone, with the background jobs
# on its own threads as before Nov 25: the way back if the worker misbehaves.
if [ "${GARMIN_MCP_WORKER:-separate}" = "inline" ]; then
  SERVER="python -m garmin_mcp.hosted"
else
  SERVER="python -m garmin_mcp.supervise"
fi

if [ -n "${LITESTREAM_BUCKET:-}" ]; then
  export LITESTREAM_REGION="${LITESTREAM_REGION:-auto}"
  export LITESTREAM_ENDPOINT="${LITESTREAM_ENDPOINT:-}"
  litestream restore -config "$CONFIG" -if-db-not-exists -if-replica-exists "$GARMIN_MCP_DB"
  exec litestream replicate -config "$CONFIG" -exec "$SERVER"
fi

exec $SERVER
