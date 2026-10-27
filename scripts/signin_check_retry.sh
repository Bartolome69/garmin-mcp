#!/usr/bin/env bash
# A Garmin sign-in check from the deploy, tried once more after a pause.
#
# Garmin turns sign-ins away for a few minutes when it has seen several in a
# row, and a deploy makes four. A refusal like that failed checks on code
# that was fine, twice on one day (19 October 2026), the next check passing
# minutes later. So a failed check waits and runs once more: a real break in
# sign-in fails both times, a passing block doesn't stop a deploy.
# Arguments go straight to scripts/run_signin_check.sh.
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
pause="${SIGNIN_RETRY_PAUSE:-150}"

if "$here/run_signin_check.sh" "$@"; then
  exit 0
fi
echo "::warning::Garmin sign-in check failed; Garmin may be turning sign-ins away for a moment. Trying once more in ${pause}s."
sleep "$pause"
exec "$here/run_signin_check.sh" "$@"
