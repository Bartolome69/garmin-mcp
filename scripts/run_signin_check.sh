#!/usr/bin/env bash
# Runs scripts/check_signin.py on the hosted machine and passes only if it
# prints SIGNIN_CHECK_RESULT=ok. Used by the workflows; needs FLY_API_TOKEN
# and FLY_APP.
#
#   scripts/run_signin_check.sh            # the sign-in code that is live
#   scripts/run_signin_check.sh candidate  # this checkout's garmin_mcp/hosted.py
#
# A sign-in can take a minute and a half, longer than an ssh session should
# be trusted to stay up, and `flyctl ssh console` doesn't reliably pass an
# exit code back. So the check runs detached on the machine and this polls
# its output in short sessions until it prints how it ended, or gives up
# after five minutes, which counts as a failure.
set -euo pipefail

# Fresh names every run: flyctl's sftp won't overwrite a file, and a stale
# copy would test old code while looking like the new.
run="$(date +%s)-$RANDOM"
script="/tmp/check-signin-$run.py"
out="/tmp/check-signin-$run.out"
args="$script"
puts="put scripts/check_signin.py $script"
if [ "${1:-}" = "candidate" ]; then
  args="$args /tmp/hosted-under-test-$run.py"
  puts="$puts
put garmin_mcp/hosted.py /tmp/hosted-under-test-$run.py"
fi

flyctl ssh sftp shell --app "$FLY_APP" <<< "$puts"
flyctl ssh console --app "$FLY_APP" -C \
  "sh -c 'setsid nohup python3 $args > $out 2>&1 < /dev/null &'" > /dev/null

for _ in $(seq 1 30); do
  sleep 10
  text="$(flyctl ssh console --app "$FLY_APP" -C "cat $out" 2>/dev/null | tr -d '\r' || true)"
  if grep -q "^SIGNIN_CHECK_RESULT=\|^Traceback" <<< "$text"; then
    echo "$text"
    grep -q "^SIGNIN_CHECK_RESULT=ok" <<< "$text" && exit 0
    echo "::error::The sign-in check did not pass"
    exit 1
  fi
done
echo "$text"
echo "::error::The sign-in check did not finish within five minutes"
exit 1
