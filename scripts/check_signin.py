"""Sign in to Garmin for real, the way the hosted server does, and say if it works.

Runs on the hosted machine (the workflows upload it there), in one of two ways:

  As an account that doesn't exist (the default). Healthy is Garmin answering
  that the email and password are wrong: only the last step of a sign-in can
  say that, so getting that far proves everything before it works, the proxy
  and every way of reaching Garmin included. Loading the sign-in page proves
  much less: on Oct 12 a bad change stopped every sign-in for 18 hours while
  the page answered fine.

  As the spare test account (--full). Everything a person connecting goes
  through: the sign-in, the session the server would store, and that session
  opened again the way a tool call opens it, reading the account's name. The
  test account has two-step verification off; a code request is a failure.

    python3 check_signin.py [--full CREDENTIALS] [CANDIDATE]

CREDENTIALS is a file holding the test account's email and password on two
lines; it is deleted as soon as it is read. CANDIDATE is the garmin_mcp
package to check before it ships, unpacked in a directory, in place of the
live one (an older form passed a single hosted.py, which still works, but
can't carry a module the live machine lacks). scripts/run_signin_check.sh
runs this from a workflow.

Each run is recorded in PostHog as `sign_in_check`, so the connection alert
sees a failing one too, and nothing in it identifies the test account. Prints
only reasons, timings and method names, never the proxy, the account or
anything secret. Nothing is stored: the test account never becomes a user.
"""

import importlib.util
import logging
import os
import secrets
import sys
import time

sys.path.insert(0, "/app")  # where the image keeps the server

args = sys.argv[1:]
full = None
if args[:1] == ["--full"]:
    path = args[1]
    with open(path) as f:
        lines = f.read().splitlines()
    os.remove(path)
    full = (lines[0].strip(), lines[1]) if len(lines) >= 2 else None
    if not full or not full[0] or not full[1]:
        print("::error::The test account's email or password is missing", flush=True)
        print("SIGNIN_CHECK_RESULT=failed", flush=True)
        raise SystemExit(1)
    args = args[2:]
candidate = args[0] if args else None

if candidate and os.path.isdir(candidate):
    # Ahead of /app, so every garmin_mcp import below is the candidate's.
    sys.path.insert(0, candidate)
    from garmin_mcp import hosted
elif candidate:
    spec = importlib.util.spec_from_file_location("garmin_mcp.hosted", candidate)
    hosted = importlib.util.module_from_spec(spec)
    sys.modules["garmin_mcp.hosted"] = hosted
    spec.loader.exec_module(hosted)
else:
    from garmin_mcp import hosted
from garmin_mcp import analytics
from garmin_mcp.session import GarminSession, build_client

# How far each try got: the library names each method as it starts it.
steps: list[str] = []


class Steps(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if msg.startswith("Trying login strategy:"):
            steps.append(msg.split(":", 1)[1].strip())


lib = logging.getLogger("garminconnect.client")
lib.setLevel(logging.DEBUG)
lib.addHandler(Steps())
lib.propagate = False  # only the summary below reaches the log


def no_code() -> str:
    raise RuntimeError("multi-factor code required")


def attempt(email: str, password: str) -> str:
    """One sign-in from a fresh address, as the connect page makes it."""
    with hosted.login_egress(hosted.login_proxy()):
        try:
            client = build_client(prompt_mfa=no_code, email=email, password=password)
            client.return_on_mfa = True
            result = client.login()
        except Exception as exc:  # noqa: BLE001 - every failure is a result here
            return hosted._failure_reason(exc)
    if isinstance(result, tuple) and result and result[0] == "needs_mfa":
        return "asked_for_code"
    if not full:
        return "signed in"
    # What the server stores, then what a tool call does with it. Data calls
    # leave from the server's own address, so this is outside the proxy.
    try:
        blob = client.client.dumps()
    except Exception as exc:  # noqa: BLE001
        return f"no_session_to_store ({type(exc).__name__})"
    try:
        name = GarminSession(tokenstore=blob, reconnect_hint="lapsed").run(
            lambda c: c.get_full_name()
        )
    except Exception as exc:  # noqa: BLE001
        return f"stored_session_failed ({type(exc).__name__})"
    return "connected" if name else "connected_but_no_name"


if full:
    email, password = full
    healthy = "connected"
else:
    email, password = f"daash-check-{secrets.token_hex(4)}@example.com", secrets.token_urlsafe(12)
    healthy = "bad_credentials"

reasons: list[str] = []
began = time.monotonic()
# One refused address is normal; three in a row is not.
for _ in range(3):
    steps.clear()
    started = time.monotonic()
    reason = attempt(email, password)
    print(f"{reason} after {time.monotonic() - started:.1f}s, methods tried: {', '.join(steps)}", flush=True)
    reasons.append(reason)
    # Only an answer about the address is worth another address.
    if reason == healthy or reason not in ("rate_limited", "blocked_by_garmin", "proxy_failed", "other"):
        break

ok = healthy in reasons
print(f"result: {'ok' if ok else 'failed'}", flush=True)

# Sent here and now rather than on analytics' own background thread, which
# this short-lived process could leave before the request went out.
sent = False
if analytics.enabled():
    try:
        analytics._post({
            "api_key": analytics.KEY,
            "event": "sign_in_check",
            "distinct_id": "daash-sign-in-check",
            "properties": {
                "ok": ok,
                "kind": "full" if full else "unknown_account",
                "reasons": reasons,
                "tries": len(reasons),
                "seconds": round(time.monotonic() - began, 1),
                "code": "candidate" if candidate else "live",
                "$process_person_profile": False,
                "$lib": "garmin-mcp-hosted",
            },
        })
        sent = True
    except Exception as exc:  # noqa: BLE001 - the check's answer matters more
        print(f"not sent to PostHog: {type(exc).__name__}", flush=True)
print(f"sent to PostHog: {sent}", flush=True)

# scripts/run_signin_check.sh looks for this exact line rather than trusting
# an exit code, so a check that dies halfway never reads as a pass.
if not ok:
    what = "connect the test account" if full else "reach Garmin's password check"
    print(f"::error::No sign-in managed to {what}. People cannot connect.", flush=True)
    print("SIGNIN_CHECK_RESULT=failed", flush=True)
    raise SystemExit(1)
print("SIGNIN_CHECK_RESULT=ok", flush=True)
