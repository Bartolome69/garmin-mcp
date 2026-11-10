"""The app's notifications: the encryption, the signature, and when they go (Nov 10).

Run: .venv/bin/python tests/push_test.py
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cryptography.fernet import Fernet  # noqa: E402

os.environ["GARMIN_MCP_DB"] = str(Path(tempfile.mkdtemp()) / "push.sqlite3")
os.environ["GARMIN_MCP_SECRET"] = Fernet.generate_key().decode()
os.environ.pop("GARMIN_MCP_POSTHOG_KEY", None)

from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature  # noqa: E402
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402
from cryptography.hazmat.primitives.kdf.hkdf import HKDF  # noqa: E402

from garmin_mcp import push, store  # noqa: E402
import garmin_mcp.hosted as hosted  # noqa: E402

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


def point(key) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def decrypt(body: bytes, receiver, auth: bytes) -> bytes:
    """RFC 8291 from the phone's side, written out on its own to check push.encrypt against."""
    salt, rs, idlen = body[:16], int.from_bytes(body[16:20], "big"), body[20]
    sender_point = body[21:21 + idlen]
    sealed = body[21 + idlen:]
    sender = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), sender_point)
    shared = receiver.exchange(ec.ECDH(), sender)
    ikm = HKDF(hashes.SHA256(), 32, salt=auth, info=b"WebPush: info\x00" + point(receiver) + sender_point).derive(shared)
    cek = HKDF(hashes.SHA256(), 16, salt=salt, info=b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt=salt, info=b"Content-Encoding: nonce\x00").derive(ikm)
    plain = AESGCM(cek).decrypt(nonce, sealed, None)
    assert rs == 4096 and plain.endswith(b"\x02")
    return plain[:-1]


def phone() -> tuple[object, dict]:
    receiver = ec.generate_private_key(ec.SECP256R1())
    auth = os.urandom(16)
    return receiver, {"endpoint": "https://push.example.com/send/abc123",
                      "keys": {"p256dh": push.b64url(point(receiver)), "auth": push.b64url(auth)}}


print("Encryption and signature")
receiver, sub = phone()
message = {"title": "Today: Easy run 8 km", "body": "8 km · ~44 min · 5:15–5:40"}
body = push.encrypt(json.dumps(message).encode(), sub["keys"]["p256dh"], sub["keys"]["auth"])
check("what Daash sends opens with the phone's own keys, and only those",
      json.loads(decrypt(body, receiver, push.unb64url(sub["keys"]["auth"]))) == message)
other = ec.generate_private_key(ec.SECP256R1())
try:
    decrypt(body, other, push.unb64url(sub["keys"]["auth"]))
    check("another phone's key can't open it", False)
except Exception:  # noqa: BLE001
    check("another phone's key can't open it", True)

header = push._vapid_header(sub["endpoint"], now=1_000_000)
jwt, k = header[len("vapid t="):].split(", k=")
head, claims, sig = jwt.split(".")
claims_d = json.loads(push.unb64url(claims))
raw = push.unb64url(sig)
key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), push.unb64url(k))
try:
    key.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
               f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
    signed = True
except Exception:  # noqa: BLE001
    signed = False
check("the push service gets a valid ES256 signature, for its own origin, from Daash's key",
      signed and json.loads(push.unb64url(head))["alg"] == "ES256" and claims_d["aud"] == "https://push.example.com"
      and claims_d["sub"] == "mailto:support@daash.run" and claims_d["exp"] == 1_000_000 + push.TTL_SECONDS
      and k == push.public_key(), str(claims_d))
check("Daash's key is made once and kept: the same after a restart", push.public_key() == push.public_key())
check("a sound subscription is taken, a broken one isn't",
      push.valid_subscription(sub) is not None
      and push.valid_subscription({**sub, "endpoint": "http://push.example.com/x"}) is None
      and push.valid_subscription({**sub, "keys": {"p256dh": "short", "auth": "x"}}) is None
      and push.valid_subscription("nope") is None)

print("Storage")
user = store.save_user("r***@example.com", '{"di_token": "x"}', email_hash="hash-r")
owner = store.push_owner(user)
endpoint, p256dh, auth = push.valid_subscription(sub)
store.save_push(user, endpoint, p256dh, auth, "Europe/London")
check("a phone that turned them on is kept, under the person", store.has_push(user) and len(store.push_subs()) == 1)
check("someone else can't turn another person's phone off", not store.drop_push(endpoint, "someone-else"))
store.forget_push(user, store.email_hash_for(user))
check("disconnecting forgets every phone", not store.has_push(user) and not store.push_subs())

print("What they say")
today = "2026-11-11"
m = hosted.morning_message({"next": {"date": today, "name": "Easy run 8 km",
                                     "stats": [{"v": "8", "u": "km"}, {"v": "~44", "u": "min"}, {"v": "5:15–5:40", "u": ""}]}}, today)
check("a session day's morning: the session and its numbers, opening Today",
      m == {"title": "Today: Easy run 8 km", "body": "8 km · ~44 min · 5:15–5:40",
            "url": "/app/#/today?from=push_morning", "tag": f"morning-{today}"}, str(m))
check("a rest day sends nothing", hosted.morning_message({"next": {"date": "2026-11-12", "name": "X"}}, today) is None
      and hosted.morning_message({"empty": True}, today) is None)
r = hosted.run_message({"activity_id": 77, "name": "16 km easy", "verdict": "On pace, with heart rate steady.",
                        "effect": {"head": "Counts as done"}, "credit": "Garmin Forerunner 965"})
check("a run on the plan: ticked, its verdict, and Garmin's credit with the watch",
      r["title"] == "16 km easy: on plan ✓" and r["body"] == "On pace, with heart rate steady.\nFrom your Garmin Forerunner 965"
      and r["url"] == "/app/#/run/77?from=push_run", str(r))
r = hosted.run_message({"activity_id": 78, "name": "Easy 8 km", "verdict": "x", "effect": {"head": "Counts as done"},
                        "easy_check": {"head": "Easy run, run harder than planned", "sub": "40% of it ran above 140 bpm."}})
check("an easy run run hard says so, credited to Garmin when the watch isn't known",
      r["title"] == "Easy 8 km: harder than planned" and r["body"].endswith("From your Garmin"), str(r))
check("a run that didn't load sends nothing", hosted.run_message({"error": "x"}) is None)

print("When they go")
sent: list[tuple[str, dict]] = []
hosted.push.send = lambda s, msg, urgency="normal": (sent.append((s["endpoint"], msg)), "sent")[1]
plan_day = {"next": {"date": today, "name": "Easy run 8 km", "stats": [{"v": "8", "u": "km"}]}}
runs = {"latest": None}
reads = []


def as_person(token, fn, *args):
    reads.append(getattr(fn, "__name__", "?"))
    if fn is hosted.app_routes.today_payload:
        return plan_day
    if fn is hosted.app_routes.run_payload:
        return {"activity_id": args[0], "name": "16 km easy", "verdict": "On pace.", "effect": {"head": "Counts as done"},
                "credit": "Garmin Forerunner 965"}
    return [runs["latest"]] if runs["latest"] else []


hosted._as_person = as_person
store.save_push(user, endpoint, p256dh, auth, "Europe/London")
london = ZoneInfo("Europe/London")
at = lambda h, m=0: datetime(2026, 11, 11, h, m, tzinfo=london).timestamp()  # noqa: E731
hosted.notify_once(at(6, 50), spacing=0)
check("nothing before 7am", not sent, str(sent))
hosted.notify_once(at(7, 5), spacing=0)
hosted.notify_once(at(7, 15), spacing=0)
check("the morning heads-up goes once, at 7", [m["title"] for _, m in sent] == ["Today: Easy run 8 km"], str(sent))
sent.clear()
plan_day = {"next": {"date": "2026-11-13", "name": "Threshold"}}
hosted.notify_once(datetime(2026, 11, 12, 7, 5, tzinfo=london).timestamp(), spacing=0)
check("and not at all on a rest day", not sent, str(sent))

old = {"activityId": 1, "activityType": {"typeKey": "running"}, "startTimeGMT": "2026-11-10 08:00:00"}
runs["latest"] = old
hosted.notify_once(at(12), spacing=0)
check("the run already there when they turned it on isn't news", not sent, str(sent))
fresh_start = datetime.fromtimestamp(at(12, 30) - 3600, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
runs["latest"] = {"activityId": 2, "activityType": {"typeKey": "running"}, "startTimeGMT": fresh_start}
hosted.notify_once(at(12, 30), spacing=0)
hosted.notify_once(at(12, 40), spacing=0)
check("a new run's verdict goes once, credited",
      len(sent) == 1 and sent[0][1]["title"] == "16 km easy: on plan ✓" and "Garmin Forerunner 965" in sent[0][1]["body"],
      str(sent))
sent.clear()
runs["latest"] = {"activityId": 3, "activityType": {"typeKey": "cycling"}, "startTimeGMT": fresh_start}
hosted.notify_once(at(12, 50), spacing=0)
check("a ride isn't a run", not sent, str(sent))
reads.clear()
hosted.notify_once(datetime(2026, 11, 12, 2, 0, tzinfo=london).timestamp(), spacing=0)
check("nothing is read from Garmin in the night", not reads, str(reads))

# Read from Garmin's official API: held until Garmin's 30 days' notice is up.
hosted.store.official_only = lambda token: "garmin-id"
runs["latest"] = {"activityId": 4, "activityType": {"typeKey": "running"}, "startTimeGMT": fresh_start}
hosted.notify_once(at(13), spacing=0)
check("a run read from Garmin's official API waits for the notice period", not sent, str(sent))
hosted.OFFICIAL_RUN_PUSH_FROM = "2026-11-01"
runs["latest"] = {"activityId": 5, "activityType": {"typeKey": "running"}, "startTimeGMT": fresh_start}
hosted.notify_once(at(13, 10), spacing=0)
check("and goes once it's over", len(sent) == 1, str(sent))

print()
if FAILED:
    print(f"{len(FAILED)} failed: " + "; ".join(FAILED))
    sys.exit(1)
print("all checks passed")
