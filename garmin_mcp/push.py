"""Notifications from the Daash app, sent the web's standard way (Nov 10).

A phone that adds the app to its home screen can ask for notifications. Its
browser gives Daash an address at the phone's own push service (Apple's,
Google's, Mozilla's) and two keys. What Daash sends there is encrypted to
those keys (RFC 8291), so the push service carries it without reading it, and
signed with Daash's own key (VAPID, RFC 8292) so the service knows who sent it.

Daash's VAPID key is made once and kept in the database, encrypted like the
Garmin sessions, so there is nothing to set up and it survives restarts.
Nothing here needs more than the cryptography package Daash already uses.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import struct
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from . import store

log = logging.getLogger(__name__)

# Who to contact about these pushes, as the push services ask.
CONTACT = "mailto:support@daash.run"
# How long a push service holds a notification for a phone that's off.
TTL_SECONDS = 12 * 3600
_TIMEOUT = 10
# A record is the whole message: these are short.
_RECORD_SIZE = 4096


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _point(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def _vapid_key() -> ec.EllipticCurvePrivateKey:
    def make() -> bytes:
        key = ec.generate_private_key(ec.SECP256R1())
        return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                 serialization.NoEncryption())
    pem = store.secret_setting("vapid_private_key", make)
    return serialization.load_pem_private_key(pem, password=None)  # type: ignore[return-value]


def public_key() -> str:
    """Daash's VAPID public key, as the browser's subscribe call takes it."""
    return b64url(_point(_vapid_key().public_key()))


def _vapid_header(endpoint: str, now: float | None = None) -> str:
    """The Authorization header that signs a push for this endpoint's service."""
    parts = urlsplit(endpoint)
    claims = {"aud": f"{parts.scheme}://{parts.netloc}", "exp": int((now or time.time()) + TTL_SECONDS),
              "sub": CONTACT}
    head = b64url(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    body = b64url(json.dumps(claims, separators=(",", ":")).encode())
    key = _vapid_key()
    r, s = decode_dss_signature(key.sign(f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256())))
    jwt = f"{head}.{body}.{b64url(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"
    return f"vapid t={jwt}, k={b64url(_point(key.public_key()))}"


def encrypt(payload: bytes, p256dh: str, auth: str, *, salt: bytes | None = None,
            sender: ec.EllipticCurvePrivateKey | None = None) -> bytes:
    """The aes128gcm body of RFC 8291 for one phone's keys."""
    receiver_point = unb64url(p256dh)
    receiver = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), receiver_point)
    sender = sender or ec.generate_private_key(ec.SECP256R1())
    sender_point = _point(sender.public_key())
    shared = sender.exchange(ec.ECDH(), receiver)
    ikm = HKDF(hashes.SHA256(), 32, salt=unb64url(auth),
               info=b"WebPush: info\x00" + receiver_point + sender_point).derive(shared)
    salt = salt or os.urandom(16)
    cek = HKDF(hashes.SHA256(), 16, salt=salt, info=b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt=salt, info=b"Content-Encoding: nonce\x00").derive(ikm)
    # One record, so the padding delimiter is 2 ("last record").
    sealed = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + struct.pack("!IB", _RECORD_SIZE, len(sender_point)) + sender_point + sealed


def send(sub: dict[str, Any], message: dict[str, Any], *, urgency: str = "normal") -> str:
    """Send one notification to one phone. Returns "sent", "gone" or "failed".

    A phone that has turned notifications off, or a subscription the push
    service no longer knows, answers 404 or 410: that phone is forgotten.
    """
    body = encrypt(json.dumps(message, separators=(",", ":")).encode(), sub["p256dh"], sub["auth"])
    request = urllib.request.Request(sub["endpoint"], data=body, method="POST", headers={
        "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
        "TTL": str(TTL_SECONDS), "Urgency": urgency, "Authorization": _vapid_header(sub["endpoint"]),
    })
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            return "sent" if 200 <= response.status < 300 else "failed"
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 410):
            store.drop_push(sub["endpoint"])
            return "gone"
        log.info("push refused: %s", exc.code)
        return "failed"
    except Exception as exc:  # noqa: BLE001 - one phone's push never stops the others
        log.info("push failed (%s)", type(exc).__name__)
        return "failed"


def valid_subscription(raw: Any) -> tuple[str, str, str] | None:
    """(endpoint, p256dh, auth) from what the browser's subscribe call gave, if it's sound."""
    if not isinstance(raw, dict):
        return None
    endpoint = raw.get("endpoint")
    keys = raw.get("keys") if isinstance(raw.get("keys"), dict) else {}
    p256dh, auth = keys.get("p256dh"), keys.get("auth")
    if not (isinstance(endpoint, str) and endpoint.startswith("https://") and len(endpoint) < 1024):
        return None
    if not (isinstance(p256dh, str) and isinstance(auth, str) and len(p256dh) < 200 and len(auth) < 100):
        return None
    try:
        if len(unb64url(p256dh)) != 65 or len(unb64url(auth)) != 16:
            return None
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), unb64url(p256dh))
    except Exception:  # noqa: BLE001
        return None
    return endpoint, p256dh, auth
