"""Garmin's official developer programme, built alongside the private API.

Daash reads and writes Garmin through the same private API as Garmin's own
apps. This is the move to the official one: people approve Daash on Garmin's
own sign-in page (OAuth 2.0 with PKCE), Garmin pushes their data to Daash as
it syncs, and workouts go to their calendar through the Training API.

While it is being built only preview accounts see it, and nothing here
touches anyone's private-API session. Configured by environment, both needed:

    GARMIN_OFFICIAL_CLIENT_ID      the app's client id, from Garmin's developer portal
    GARMIN_OFFICIAL_CLIENT_SECRET  its secret

The push address carries a key derived from the secret, so nothing else has
to be set; the preview tool shows it, for pasting into Garmin's endpoint tool.
"""

from __future__ import annotations

import base64
import calendar
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from contextvars import ContextVar
from typing import Any

from cryptography.fernet import InvalidToken

from . import store

log = logging.getLogger(__name__)

AUTHORIZE_URL = "https://connect.garmin.com/oauth2Confirm"
TOKEN_URL = "https://diauth.garmin.com/di-oauth2-service/oauth/token"
API = "https://apis.garmin.com"
_TIMEOUT = 20
# A link from the preview tool, and the sign-in it starts, stay good this long.
LINK_SECONDS = 15 * 60
# Refresh a little before Garmin's expiry, as Garmin asks, so a slow request
# never goes out on a token that lapses on the way.
REFRESH_MARGIN = 600
# Shown to the person in Garmin Connect beside each workout Daash sends.
PROVIDER = "Daash"

# Garmin names each kind of pushed summary by its list key. Where a summary
# starts, when it has a start at all.
_START_FIELDS = ("startTimeInSeconds", "calendarDate", "measurementTimeInSeconds")

_user: ContextVar["str | None"] = ContextVar("garmin_official_user", default=None)


class OfficialError(RuntimeError):
    """Something the person can act on; the message says what."""


def configured() -> bool:
    return bool(os.environ.get("GARMIN_OFFICIAL_CLIENT_ID") and os.environ.get("GARMIN_OFFICIAL_CLIENT_SECRET"))


def base_url() -> str:
    return os.environ.get("GARMIN_MCP_BASE_URL", "").strip().rstrip("/") or "https://mcp.garmin.daash.run"


def use(user_token: str | None):
    """Bind the person making this request, the way crews and sessions are bound."""
    return _user.set(user_token)


def reset(token) -> None:
    _user.reset(token)


def current() -> str | None:
    return _user.get()


# --------------------------------------------------------------------------
# Links and sign-in
# --------------------------------------------------------------------------


def _seal(payload: dict) -> str:
    return store._cipher().encrypt(json.dumps(payload).encode()).decode()


def _open(token: str, max_age: int = LINK_SECONDS) -> dict | None:
    try:
        return json.loads(store._cipher().decrypt(token.encode(), ttl=max_age).decode())
    except (InvalidToken, ValueError):
        return None


def connect_link(user_token: str) -> str:
    """A short-lived link that starts Garmin's sign-in for this person.

    The link is sealed with the server's key, so it can't be forged or made
    to point at someone else, and it stops working after LINK_SECONDS.
    """
    return f"{base_url()}/official/start?t={urllib.parse.quote(_seal({'u': user_token}))}"


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def authorize_url(link_token: str) -> str | None:
    """Where to send someone who opened a connect link, or None if it has lapsed."""
    opened = _open(link_token)
    if not opened or not opened.get("u"):
        return None
    verifier = secrets.token_urlsafe(64)[:96]
    # The verifier and who is signing in travel in the state, sealed, and
    # come back with the code: nothing to keep in memory between the two.
    state = _seal({"u": opened["u"], "v": verifier})
    query = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": os.environ["GARMIN_OFFICIAL_CLIENT_ID"].strip(),
        "code_challenge": _challenge(verifier),
        "code_challenge_method": "S256",
        "redirect_uri": f"{base_url()}/official/callback",
        "state": state,
    })
    return f"{AUTHORIZE_URL}?{query}"


def _relay() -> str:
    """The UK relay the private sign-in already uses, for when Garmin turns away the server."""
    return os.environ.get("GARMIN_MCP_LOGIN_PROXY", "").strip()


def _blocked(exc: urllib.error.HTTPError, body: bytes) -> bool:
    """A 403 that is a wall in front of Garmin, not an answer from its API.

    Garmin's own answers are JSON; the wall that turns away data-centre
    addresses sends a web page.
    """
    return exc.code == 403 and not body.lstrip().startswith((b"{", b"["))


def _send(request: urllib.request.Request) -> bytes:
    """Make the request directly, and once more through the relay if Garmin blocks the server."""
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read() or b""
        if not (_relay() and _blocked(exc, body)):
            exc.body = body  # read once; kept for whoever explains it
            raise
        log.info("official request to %s blocked directly; trying the relay", urllib.parse.urlparse(request.full_url).hostname)
    except urllib.error.URLError:
        if not _relay():
            raise
        log.info("official request to %s failed directly; trying the relay", urllib.parse.urlparse(request.full_url).hostname)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": _relay(), "https": _relay()}))
    try:
        with opener.open(request, timeout=_TIMEOUT) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        exc.body = exc.read() or b""
        raise


def _oauth_reason(exc: urllib.error.HTTPError) -> str:
    """Garmin's OAuth error code, which says what to fix without echoing anything secret."""
    body = getattr(exc, "body", b"") or b""
    try:
        answer = json.loads(body)
    except ValueError:
        return "web page" if body.lstrip().startswith(b"<") else ""
    if not isinstance(answer, dict):
        return ""
    code = str(answer.get("error") or answer.get("errorCode") or "")
    detail = str(answer.get("error_description") or answer.get("message") or "")[:120]
    return ": ".join(part for part in (code, detail) if part)


def _client_auth() -> str:
    """The app's credentials as an HTTP Basic header.

    Garmin's token endpoint takes them this way, as its examples send them:
    sent as form fields instead, it answers 401 with a web page.
    """
    pair = f"{os.environ['GARMIN_OFFICIAL_CLIENT_ID'].strip()}:{os.environ['GARMIN_OFFICIAL_CLIENT_SECRET'].strip()}"
    return "Basic " + base64.b64encode(pair.encode()).decode()


def _post_form(url: str, fields: dict[str, str]) -> dict:
    request = urllib.request.Request(
        url, data=urllib.parse.urlencode(fields).encode(), method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json",
                 "Authorization": _client_auth(), "User-Agent": "daash-official"},
    )
    try:
        raw = _send(request)
    except urllib.error.HTTPError as exc:
        reason = _oauth_reason(exc)
        log.warning("official token request refused: %s %s", exc.code, reason)
        raise OfficialError(f"Garmin refused the sign-in ({exc.code}{f', {reason}' if reason else ''}). "
                            "Ask Claude for a new link and try again; if it says the same, send this to support.") from None
    except urllib.error.URLError as exc:
        log.warning("official token request failed: %s", type(exc.reason).__name__)
        raise OfficialError("Daash couldn't reach Garmin to finish connecting. Try a new link in a minute.") from None
    return json.loads(raw or b"{}")


def _tokens_from(answer: dict) -> dict:
    now = int(time.time())
    return {
        "access_token": answer["access_token"],
        "refresh_token": answer["refresh_token"],
        "access_expires_at": now + int(answer.get("expires_in") or 0),
        "refresh_expires_at": now + int(answer.get("refresh_token_expires_in") or 0),
    }


def finish(code: str, state: str) -> str:
    """Exchange Garmin's code for tokens and link the account. Returns the Garmin user id."""
    opened = _open(state)
    if not opened or not opened.get("u") or not opened.get("v"):
        raise OfficialError("That sign-in has expired. Ask for a new connect link and try again.")
    user_token = opened["u"]
    if store.get_user(user_token) is None:
        raise OfficialError("Daash doesn't recognise this account any more. Connect Daash again first.")
    answer = _post_form(TOKEN_URL, {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": opened["v"],
        "redirect_uri": f"{base_url()}/official/callback",
    })
    tokens = _tokens_from(answer)
    garmin_user_id = str(_call(tokens["access_token"], "GET", "/partner-gateway/rest/user/id").get("userId") or "")
    if not garmin_user_id:
        raise OfficialError("Garmin didn't say which account this is. Try connecting again.")
    permissions = _call(tokens["access_token"], "GET", "/partner-gateway/rest/user/permissions").get("permissions") or []
    store.save_official_link(user_token, garmin_user_id, tokens, list(permissions))
    return garmin_user_id


# --------------------------------------------------------------------------
# Calling Garmin
# --------------------------------------------------------------------------


def _call(access_token: str, method: str, path: str, body: Any = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        API + path, data=data, method=method,
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json",
                 "Accept": "application/json", "User-Agent": "daash-official"},
    )
    try:
        raw = _send(request)
    except urllib.error.URLError as exc:
        if isinstance(exc, urllib.error.HTTPError):
            raise _refused(exc, path) from None
        log.warning("official %s %s failed: %s", method, path, type(exc.reason).__name__)
        raise OfficialError("Daash couldn't reach Garmin. Try again in a minute.") from None
    return json.loads(raw) if raw else {}


def _refused(exc: urllib.error.HTTPError, path: str) -> OfficialError:
    """What a refusal from Garmin's API means for the person, logged with Garmin's own code."""
    reason = _oauth_reason(exc)
    log.warning("official call %s refused: %s %s", path, exc.code, reason)
    reasons = {401: "Garmin no longer accepts this connection. Connect again.",
               403: "Garmin doesn't allow this for Daash's app.",
               412: "This Garmin account hasn't given Daash permission for that. "
                    "Check Daash's permissions in Garmin Connect.",
               429: "Garmin is limiting how often Daash can ask. Try again in a while."}
    said = f"Garmin answered {exc.code}{f' ({reason})' if reason else ''}."
    return OfficialError(reasons.get(exc.code, said))


def _access_token(user_token: str) -> tuple[str, store.OfficialLink]:
    link = store.official_link(user_token)
    if link is None:
        raise OfficialError("This account isn't connected to Garmin's official API yet.")
    tokens = link.tokens
    if time.time() < tokens["access_expires_at"] - REFRESH_MARGIN:
        return tokens["access_token"], link
    if time.time() >= tokens["refresh_expires_at"]:
        raise OfficialError("The Garmin connection has expired. Connect again.")
    answer = _post_form(TOKEN_URL, {
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
    })
    # Garmin issues a new refresh token every time; the old one is spent.
    fresh = _tokens_from(answer)
    store.update_official_tokens(user_token, fresh)
    return fresh["access_token"], link


def call(user_token: str, method: str, path: str, body: Any = None) -> Any:
    token, _ = _access_token(user_token)
    return _call(token, method, path, body)


def disconnect(user_token: str) -> bool:
    """End the official link: tell Garmin, then delete everything kept for it.

    Garmin asks for the call whenever someone disconnects inside the app, so
    the account shows Daash as gone at their end too. What's kept here goes
    even if Garmin can't be reached.
    """
    link = store.official_link(user_token)
    if link is None:
        return False
    try:
        call(user_token, "DELETE", "/partner-gateway/rest/user/registration")
    except (OfficialError, urllib.error.URLError, OSError, KeyError) as exc:
        log.warning("official registration not deleted at Garmin: %s", type(exc).__name__)
    store.forget_official_user(link.garmin_user_id)
    return True


# --------------------------------------------------------------------------
# What Garmin pushes
# --------------------------------------------------------------------------


def push_key() -> str:
    """The secret part of the push address, derived from the client secret."""
    secret = os.environ.get("GARMIN_OFFICIAL_CLIENT_SECRET", "")
    return hmac.new(secret.encode(), b"daash-official-push", hashlib.sha256).hexdigest()[:32]


def push_url() -> str:
    return f"{base_url()}/official/push/{push_key()}"


def _start(summary: dict) -> int | None:
    for field in _START_FIELDS:
        value = summary.get(field)
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str) and len(value) == 10:
            try:
                return calendar.timegm(time.strptime(value, "%Y-%m-%d"))
            except ValueError:
                continue
    return None


def handle_push(body: bytes) -> dict[str, int]:
    """Keep what Garmin pushed, for accounts linked here. Returns counts per kind.

    One push can carry several kinds, each a list under its own key. Data
    for a Garmin account nobody here is linked to is dropped, not kept.
    """
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        return {}
    kept: dict[str, int] = {}
    if not isinstance(payload, dict):
        return kept
    for kind, items in payload.items():
        if not isinstance(items, list):
            continue
        if kind == "deregistrations":
            for item in items:
                if isinstance(item, dict) and item.get("userId"):
                    store.forget_official_user(str(item["userId"]))
            kept[kind] = len(items)
            continue
        if kind == "userPermissionsChange":
            for item in items:
                if isinstance(item, dict) and item.get("userId"):
                    store.update_official_permissions(str(item["userId"]), list(item.get("permissions") or []))
            kept[kind] = len(items)
            continue
        by_user: dict[str, list[tuple[str, int | None, dict]]] = {}
        for item in items:
            if not isinstance(item, dict) or not item.get("userId"):
                continue
            summary_id = str(item.get("summaryId") or item.get("activityId") or "")
            if not summary_id:
                continue
            by_user.setdefault(str(item["userId"]), []).append((summary_id, _start(item), item))
        for garmin_user_id, rows in by_user.items():
            if store.official_link_for_garmin_user(garmin_user_id) is None:
                continue
            kept[kind] = kept.get(kind, 0) + store.save_official_summaries(garmin_user_id, kind, rows)
    return kept


# --------------------------------------------------------------------------
# Workouts through the Training API
# --------------------------------------------------------------------------

# Daash's sports, as the Training API names them. Walking and hiking have no
# sport of their own there.
_SPORTS = {"running": "RUNNING", "cycling": "CYCLING"}
_INTENSITY = {"warmup": "WARMUP", "cooldown": "COOLDOWN", "interval": "INTERVAL",
              "recovery": "RECOVERY", "rest": "REST"}
_TARGETS = {"pace.zone": "SPEED", "heart.rate.zone": "HEART_RATE", "power.zone": "POWER"}


def _field(obj: Any, name: str) -> Any:
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def _step(step: Any, order: list[int]) -> dict:
    order[0] += 1
    kind = (_field(_field(step, "stepType"), "stepTypeKey") or "").lower()
    if kind == "repeat":
        here = order[0]
        children = [_step(child, order) for child in (_field(step, "workoutSteps") or [])]
        return {"type": "WorkoutRepeatStep", "stepOrder": here, "repeatType": "REPEAT_UNTIL_STEPS_CMPLT",
                "repeatValue": float(_field(step, "numberOfIterations") or 0), "steps": children}
    condition = (_field(_field(step, "endCondition"), "conditionTypeKey") or "").lower()
    value = _field(step, "endConditionValue")
    out: dict[str, Any] = {"type": "WorkoutStep", "stepOrder": order[0], "intensity": _INTENSITY.get(kind, "ACTIVE"),
                           "description": _field(step, "description") or None}
    if condition == "distance":
        out.update(durationType="DISTANCE", durationValue=float(value), durationValueType="METER")
    elif condition == "time":
        out.update(durationType="TIME", durationValue=float(value), durationValueType=None)
    else:
        out.update(durationType="OPEN", durationValue=None, durationValueType=None)
    target = _TARGETS.get(_field(_field(step, "targetType"), "workoutTargetTypeKey") or "")
    low, high = _field(step, "targetValueOne"), _field(step, "targetValueTwo")
    if target and low is not None and high is not None:
        out.update(targetType=target, targetValueLow=float(min(low, high)), targetValueHigh=float(max(low, high)))
    else:
        out.update(targetType="OPEN", targetValueLow=None, targetValueHigh=None)
    return out


def training_workout(model: Any, sport: str) -> dict:
    """A workout built by workouts.build_workout, in the Training API's shape."""
    api_sport = _SPORTS.get(sport)
    if api_sport is None:
        raise OfficialError(f"The official API doesn't take {sport} workouts yet; runs and rides only.")
    segments = _field(model, "workoutSegments") or []
    order = [0]
    steps = [_step(step, order) for segment in segments for step in (_field(segment, "workoutSteps") or [])]
    description = _field(model, "description")
    return {
        "workoutName": _field(model, "workoutName"),
        "description": (description or "")[:1024] or None,
        "sport": api_sport,
        "workoutProvider": PROVIDER,
        "workoutSourceId": PROVIDER,
        "segments": [{"segmentOrder": 1, "sport": api_sport, "steps": steps}],
    }


def send_workout(user_token: str, workout: dict, date: str | None = None) -> dict:
    """Create the workout on the person's account and, given a date, schedule it."""
    created = call(user_token, "POST", "/workoutportal/workout/v2", workout)
    workout_id = created.get("workoutId")
    result: dict[str, Any] = {"workout_id": workout_id, "name": created.get("workoutName")}
    if date and workout_id:
        scheduled = call(user_token, "POST", "/training-api/schedule/", {"workoutId": workout_id, "date": date})
        result.update(schedule_id=_field(scheduled, "scheduleId") if isinstance(scheduled, dict) else scheduled,
                      scheduled_for=date)
    return result


# --------------------------------------------------------------------------
# Status, for the preview tool
# --------------------------------------------------------------------------


def status(user_token: str) -> dict:
    link = store.official_link(user_token)
    if link is None:
        return {"connected": False, "connect_link": connect_link(user_token),
                "link_good_for_minutes": LINK_SECONDS // 60}
    counts = store.official_summary_counts(link.garmin_user_id)
    fmt = lambda t: time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(t)) if t else None  # noqa: E731
    return {
        "connected": True,
        "since": fmt(link.created_at),
        # Garmin's id for this account, which the developer portal's tools
        # (the Summary Resender) ask for.
        "garmin_user_id": link.garmin_user_id,
        "permissions": link.permissions,
        "received": {kind: {"count": c["count"], "latest": fmt(c["latest_start"]), "arrived": fmt(c["last_received"])}
                     for kind, c in sorted(counts.items())},
        "push_url": push_url(),
        "push_setup": "Paste push_url into every summary type at apis.garmin.com/tools/endpoints, "
                      "including deregistrations and user permissions.",
    }
