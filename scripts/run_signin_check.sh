#!/usr/bin/env bash
# Runs scripts/check_signin.py on the hosted machine and passes only if it
# prints SIGNIN_CHECK_RESULT=ok. Used by the workflows; needs FLY_API_TOKEN
# and FLY_APP.
#
#   scripts/run_signin_check.sh                 # the sign-in code that is live
#   scripts/run_signin_check.sh candidate       # this checkout's garmin_mcp package
#   scripts/run_signin_check.sh full [candidate]
#       signs the spare test account in all the way, from GARMIN_CHECK_EMAIL
#       and GARMIN_CHECK_PASSWORD. They reach the machine as a file only root
#       can read, which the check deletes as soon as it has read it.
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
full_opt=""
cand_opt=""
puts="put scripts/check_signin.py $script"
creds=""
cand_tar=""
cleanup_local() { [ -n "$creds" ] && rm -f "$creds"; [ -n "$cand_tar" ] && rm -f "$cand_tar"; return 0; }
trap cleanup_local EXIT
for word in "$@"; do
  case "$word" in
    full)
      if [ -z "${GARMIN_CHECK_EMAIL:-}" ] || [ -z "${GARMIN_CHECK_PASSWORD:-}" ]; then
        echo "::error::GARMIN_CHECK_EMAIL and GARMIN_CHECK_PASSWORD must be set for a full check"
        exit 1
      fi
      creds="$(umask 077; mktemp)"
      printf '%s\n%s\n' "$GARMIN_CHECK_EMAIL" "$GARMIN_CHECK_PASSWORD" > "$creds"
      full_opt="--full /tmp/check-signin-$run.creds"
      puts="$puts
put $creds /tmp/check-signin-$run.creds"
      ;;
    candidate)
      # The whole package, not hosted.py alone: a hosted.py that imports a
      # module the live machine doesn't have yet must still be checkable.
      cand_tar="$(mktemp --suffix=.tgz)"
      tar czf "$cand_tar" --exclude=__pycache__ garmin_mcp
      cand_opt="/tmp/candidate-$run"
      puts="$puts
put $cand_tar /tmp/candidate-$run.tgz"
      ;;
    *) echo "unknown option: $word"; exit 1 ;;
  esac
done
args="$script $full_opt $cand_opt"

flyctl ssh sftp shell --app "$FLY_APP" <<< "$puts"
if [ -n "$cand_opt" ]; then
  flyctl ssh console --app "$FLY_APP" -C \
    "sh -c 'mkdir -p $cand_opt && tar xzf $cand_opt.tgz -C $cand_opt && rm -f $cand_opt.tgz'" > /dev/null
fi
if [ -n "$creds" ]; then
  flyctl ssh console --app "$FLY_APP" -C "chmod 600 /tmp/check-signin-$run.creds" > /dev/null
fi
flyctl ssh console --app "$FLY_APP" -C \
  "sh -c 'setsid nohup python3 $args > $out 2>&1 < /dev/null &'" > /dev/null

cleanup_remote() {
  [ -n "$creds" ] && flyctl ssh console --app "$FLY_APP" -C "rm -f /tmp/check-signin-$run.creds" > /dev/null 2>&1 || true
  [ -n "$cand_opt" ] && flyctl ssh console --app "$FLY_APP" -C "rm -rf $cand_opt" > /dev/null 2>&1 || true
}

for _ in $(seq 1 30); do
  sleep 10
  text="$(flyctl ssh console --app "$FLY_APP" -C "cat $out" 2>/dev/null | tr -d '\r' || true)"
  if grep -q "^SIGNIN_CHECK_RESULT=\|^Traceback" <<< "$text"; then
    echo "$text"
    cleanup_remote
    grep -q "^SIGNIN_CHECK_RESULT=ok" <<< "$text" && exit 0
    echo "::error::The sign-in check did not pass"
    exit 1
  fi
done
cleanup_remote
echo "$text"
echo "::error::The sign-in check did not finish within five minutes"
exit 1
