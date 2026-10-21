"""Mail to support@daash.run, passed on to whoever answers it.

Resend receives the domain's mail and tells this server about each message
with a signed webhook (email.received). The webhook carries only who it is
from and to; the server checks the signature, fetches the message from
Resend, and sends it on to the inbox set in SUPPORT_FORWARD_TO, with
Reply-To set to the sender so a reply goes back to them. Nothing is kept:
the message passes through memory once and is gone.

Configured by environment, all three needed:
    RESEND_API_KEY          a Resend key that can read received mail and send
    RESEND_WEBHOOK_SECRET   the webhook's signing secret, whsec_...
    SUPPORT_FORWARD_TO      the inbox it all goes to
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any

log = logging.getLogger(__name__)

API = "https://api.resend.com"
_TIMEOUT = 20
# Addresses at the domain that are passed on; anything else is dropped, so a
# spammer guessing names reaches nobody.
ALIASES = {a.strip().lower() for a in os.environ.get("SUPPORT_ALIASES", "support,hello").split(",") if a.strip()}
DOMAIN = "daash.run"
FROM = os.environ.get("SUPPORT_FROM", "Daash Support <support@daash.run>")
# A signed webhook older than this is refused, so a captured one can't be replayed later.
TOLERANCE_SECONDS = 5 * 60
# Resend's own limit on a sent message is 40 MB; this keeps well under it.
MAX_ATTACHMENTS_BYTES = 20 * 1024 * 1024

# Messages already passed on since the server started, so a retried webhook
# isn't forwarded twice. Resend also refuses a repeat of the same send.
_SENT: set[str] = set()


class BadSignature(Exception):
    pass


def configured() -> bool:
    return all(os.environ.get(k) for k in ("RESEND_API_KEY", "RESEND_WEBHOOK_SECRET", "SUPPORT_FORWARD_TO"))


def verify(body: bytes, headers: dict[str, str], secret: str, now: float | None = None) -> dict[str, Any]:
    """The webhook's event, once its Svix signature checks out against the raw body."""
    msg_id, stamp, signatures = (headers.get(k, "") for k in ("svix-id", "svix-timestamp", "svix-signature"))
    if not (msg_id and stamp and signatures):
        raise BadSignature("missing signature headers")
    try:
        sent_at = int(stamp)
    except ValueError:
        raise BadSignature("bad timestamp") from None
    if abs((now or time.time()) - sent_at) > TOLERANCE_SECONDS:
        raise BadSignature("too old")
    try:
        key = base64.b64decode(secret.removeprefix("whsec_"))
    except ValueError:
        raise BadSignature("bad secret") from None
    expected = base64.b64encode(
        hmac.new(key, f"{msg_id}.{stamp}.".encode() + body, hashlib.sha256).digest()
    ).decode()
    given = [s.split(",", 1)[1] for s in signatures.split() if s.startswith("v1,")]
    if not any(hmac.compare_digest(expected, g) for g in given):
        raise BadSignature("signature doesn't match")
    return json.loads(body)


def _request(method: str, path: str, payload: dict[str, Any] | None = None,
             headers: dict[str, str] | None = None) -> dict[str, Any]:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        API + path, data=data, method=method,
        headers={"Authorization": f"Bearer {os.environ['RESEND_API_KEY']}", "Content-Type": "application/json",
                 "User-Agent": "daash-support-mail", **(headers or {})},
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        return json.loads(response.read() or b"{}")


def _download(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "daash-support-mail"}),
                                timeout=_TIMEOUT) as response:
        return response.read()


def _for_us(addresses: Any) -> bool:
    for address in addresses if isinstance(addresses, list) else [addresses]:
        local, _, domain = str(address or "").strip().strip("<>").lower().rpartition("@")
        local = local.rsplit("<", 1)[-1].strip()
        if domain.rstrip(">") == DOMAIN and local in ALIASES:
            return True
    return False


def forward(event: dict[str, Any]) -> str:
    """Pass one received message on. Returns what happened, for the log."""
    if event.get("type") != "email.received":
        return "ignored: not a received email"
    data = event.get("data") or {}
    email_id = str(data.get("email_id") or "")
    if not email_id:
        return "ignored: no email id"
    if email_id in _SENT:
        return "already forwarded"
    if not _for_us((data.get("to") or []) + (data.get("cc") or [])):
        return "ignored: not to a support address"
    sender = str(data.get("from") or "")
    target = os.environ["SUPPORT_FORWARD_TO"]
    # Never pass on mail that came from here or from the inbox itself: that
    # is how a forwarding loop starts.
    if any(a in sender.lower() for a in (f"@{DOMAIN}", target.lower())):
        return "ignored: from ourselves"

    email = _request("GET", f"/emails/receiving/{email_id}")
    attachments, total, skipped = [], 0, []
    if data.get("attachments"):
        listed = _request("GET", f"/emails/receiving/{email_id}/attachments").get("data") or []
        for item in listed:
            name = item.get("filename") or "attachment"
            size = item.get("size") or 0
            if total + size > MAX_ATTACHMENTS_BYTES or not item.get("download_url"):
                skipped.append(name)
                continue
            content = _download(item["download_url"])
            total += len(content)
            attachments.append({"filename": name, "content": base64.b64encode(content).decode(),
                                **({"content_type": item["content_type"]} if item.get("content_type") else {})})

    note = f"From {sender} to {', '.join(data.get('to') or [])}"
    if skipped:
        note += f". Too large to pass on, still in Resend: {', '.join(skipped)}"
    text = email.get("text") or ""
    html = email.get("html")
    message: dict[str, Any] = {
        "from": FROM,
        "to": [target],
        "reply_to": sender,
        "subject": email.get("subject") or data.get("subject") or "(no subject)",
        "text": f"{note}\n\n{text}",
    }
    if html:
        message["html"] = f'<p style="color:#5B6270;font-size:12px">{_escape(note)}</p>{html}'
    if attachments:
        message["attachments"] = attachments
    _request("POST", "/emails", message, headers={"Idempotency-Key": f"support-forward-{email_id}"})
    _SENT.add(email_id)
    if len(_SENT) > 5000:
        _SENT.clear()
    return "forwarded"


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def handle(body: bytes, headers: dict[str, str]) -> tuple[int, str]:
    """The webhook, start to finish: (HTTP status, what happened). Resend retries anything not 2xx."""
    if not configured():
        return 503, "support mail isn't set up"
    try:
        event = verify(body, headers, os.environ["RESEND_WEBHOOK_SECRET"])
    except (BadSignature, ValueError) as exc:
        return 401, f"refused: {exc}"
    try:
        outcome = forward(event)
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        # The message stays in Resend, which tries the webhook again.
        log.warning("support mail %s not forwarded: %s", (event.get("data") or {}).get("email_id"), type(exc).__name__)
        return 502, "couldn't forward it yet"
    log.info("support mail %s: %s", (event.get("data") or {}).get("email_id"), outcome)
    return 200, outcome
