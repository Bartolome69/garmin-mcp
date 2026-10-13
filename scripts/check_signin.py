"""Sign in to Garmin for real, the way the hosted server does, and say if it works.

Runs on the hosted machine (the workflows upload it there), as an account
that doesn't exist. Healthy is Garmin answering that the email and password
are wrong: only the last step of a sign-in can say that, so getting that far
proves everything before it works, the proxy and every way of reaching Garmin
included. Loading the sign-in page proves much less: on Oct 12 a bad change
stopped every sign-in for 18 hours while the page answered fine.

    python3 check_signin.py                    # the sign-in code that is live
    python3 check_signin.py /tmp/hosted.py     # a candidate hosted.py, before it ships

A candidate replaces only hosted.py, where the sign-in code lives; the rest is
what the machine already runs. Each run is recorded in PostHog as
`sign_in_check`, so the connection alert sees a failing one too. Prints only
reasons, timings and method names, never the proxy or anything secret.
"""

import importlib.util
import logging
import secrets
import sys
import threading
import time

sys.path.insert(0, "/app")  # where the image keeps the server

candidate = sys.argv[1] if len(sys.argv) > 1 else None
if candidate:
    spec = importlib.util.spec_from_file_location("garmin_mcp.hosted", candidate)
    hosted = importlib.util.module_from_spec(spec)
    sys.modules["garmin_mcp.hosted"] = hosted
    spec.loader.exec_module(hosted)
else:
    from garmin_mcp import hosted
from garmin_mcp import analytics
from garminconnect import Garmin

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

email = f"daash-check-{secrets.token_hex(4)}@example.com"
reasons: list[str] = []
began = time.monotonic()
# One refused address is normal; three in a row is not.
for _ in range(3):
    steps.clear()
    started = time.monotonic()
    with hosted.login_egress(hosted.login_proxy()):
        try:
            Garmin(email, secrets.token_urlsafe(12)).login()
            reason = "signed in"
        except Exception as exc:  # noqa: BLE001 - every failure is a result here
            reason = hosted._failure_reason(exc)
    print(f"{reason} after {time.monotonic() - started:.1f}s, methods tried: {', '.join(steps)}")
    reasons.append(reason)
    if reason == "bad_credentials":
        break

ok = "bad_credentials" in reasons
analytics.capture("sign_in_check", None, {
    "ok": ok,
    "reasons": reasons,
    "tries": len(reasons),
    "seconds": round(time.monotonic() - began, 1),
    "code": "candidate" if candidate else "live",
})
for t in threading.enumerate():
    if t is not threading.current_thread() and t.daemon:
        t.join(10)

if not ok:
    print("::error::No sign-in reached Garmin's password check. People cannot connect.")
    raise SystemExit(1)
print("ok: a sign-in reached Garmin's password check")
