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

from dataclasses import dataclass

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

from . import official_data, official_fit, store

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
    return _garmin_page({"u": opened["u"]})


def signup_url(flow: str) -> str:
    """Garmin's approval page for someone signing up from Claude or ChatGPT.

    `flow` is the connector's waiting sign-in request, carried through Garmin
    and back in the sealed state, so the callback can hand the new account
    back to the app that asked.
    """
    return _garmin_page({"f": flow})


def signup_flow(state: str) -> str | None:
    """The connector request a sign-up's state carries, or None for a link sign-in or a lapsed one."""
    opened = _open(state)
    return opened.get("f") if opened else None


def _garmin_page(carry: dict) -> str:
    verifier = secrets.token_urlsafe(64)[:96]
    # The verifier and who is signing in travel in the state, sealed, and
    # come back with the code: nothing to keep in memory between the two.
    state = _seal({**carry, "v": verifier})
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
    garmin_user_id, tokens, permissions = _exchange(code, opened["v"])
    if permissions is None:
        earlier = store.official_link(user_token)
        permissions = earlier.permissions if earlier else None
    store.save_official_link(user_token, garmin_user_id, tokens, permissions)
    store.queue_backfill(garmin_user_id)
    return garmin_user_id


@dataclass(frozen=True)
class Approval:
    """Someone approved Daash on Garmin's page while signing up."""
    user_token: str
    garmin_user_id: str
    flow: str
    new: bool
    permissions: list[str] | None


def finish_signup(code: str, state: str) -> Approval:
    """Exchange Garmin's code and find or make the Daash account it belongs to.

    Garmin's approval says which Garmin account this is, so someone who
    signed up this way before is recognised by it and gets their own account
    back. Anyone else gets a new account on the official API alone.
    """
    opened = _open(state)
    if not opened or not opened.get("f") or not opened.get("v"):
        raise OfficialError("That sign-in has expired. Start again from the connector in Claude.")
    garmin_user_id, tokens, permissions = _exchange(code, opened["v"])
    known = store.official_link_for_garmin_user(garmin_user_id)
    if known is not None and store.get_user(known.user_token) is not None:
        if permissions is None:
            permissions = known.permissions  # unknown now: keep what Garmin said before
        store.save_official_link(known.user_token, garmin_user_id, tokens, permissions)
        store.queue_backfill(garmin_user_id)
        return Approval(known.user_token, garmin_user_id, opened["f"], False, permissions)
    user_token = store.save_official_user(garmin_user_id, tokens, permissions)
    # Garmin sends only what syncs from now on; the past has to be asked for (backfill.py).
    store.queue_backfill(garmin_user_id)
    return Approval(user_token, garmin_user_id, opened["f"], True, permissions)


def _exchange(code: str, verifier: str) -> tuple[str, dict, list[str]]:
    answer = _post_form(TOKEN_URL, {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": f"{base_url()}/official/callback",
    })
    tokens = _tokens_from(answer)
    garmin_user_id = str(_call(tokens["access_token"], "GET", "/partner-gateway/rest/user/id").get("userId") or "")
    if not garmin_user_id:
        raise OfficialError("Garmin didn't say which account this is. Try connecting again.")
    return garmin_user_id, tokens, _permissions(tokens["access_token"])


def _permissions(access_token: str) -> list[str] | None:
    """What the account agreed to share, or None when Garmin won't say yet.

    Garmin has refused this read (403) for a fresh approval it had just given,
    while the account itself was readable. The list only lets Daash say in
    advance that workouts can't go to the watch, and Garmin pushes it again
    whenever it changes, so a refusal is tried once more and then left
    unknown rather than stopping the sign-up.
    """
    for attempt in range(2):
        try:
            return list(_call(access_token, "GET", "/partner-gateway/rest/user/permissions").get("permissions") or [])
        except OfficialError:
            if attempt == 0:
                time.sleep(1.5)
    log.warning("official permissions unreadable at sign-up; carrying on without them")
    return None


def can_write(user_token: str) -> bool:
    """Whether the person let Daash put workouts on their watch when they approved it.

    Unknown counts as yes: Garmin answers the write itself if it isn't allowed.
    """
    link = store.official_link(user_token)
    return link is not None and (link.permissions is None or "WORKOUT_IMPORT" in link.permissions)


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
    # An activity's details carry their start inside the summary they wrap.
    if isinstance(summary.get("summary"), dict) and "startTimeInSeconds" not in summary:
        summary = summary["summary"]
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
    for a Garmin account nobody here is linked to is dropped, not kept, and
    what is kept is trimmed first (official_data says what goes).
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
        if kind == "activityFiles":
            files = _keep_files(items)
            if files:
                kept[kind] = files
            continue
        if kind not in official_data.KEPT:
            continue
        by_user: dict[str, list[tuple[str, int | None, dict]]] = {}
        for item in items:
            if not isinstance(item, dict) or not item.get("userId"):
                continue
            summary_id = str(item.get("summaryId") or item.get("activityId") or "")
            kept_item = official_data.keep(kind, item) if summary_id else None
            if kept_item is None:
                continue
            by_user.setdefault(str(item["userId"]), []).append((summary_id, _start(item), kept_item))
        for garmin_user_id, rows in by_user.items():
            if store.official_link_for_garmin_user(garmin_user_id) is None:
                continue
            kept[kind] = kept.get(kind, 0) + store.save_official_summaries(garmin_user_id, kind, rows)
    return kept


def _keep_files(items: list) -> int:
    """Read each activity's FIT file for its summary figures, keep those, let the file go."""
    kept = 0
    for item in items:
        if not isinstance(item, dict) or not item.get("userId") or not item.get("activityId"):
            continue
        if str(item.get("fileType") or "FIT").upper() != "FIT" or not isinstance(item.get("callbackURL"), str):
            continue
        link = store.official_link_for_garmin_user(str(item["userId"]))
        if link is None or not official_fit.available():
            continue
        try:
            figures = official_fit.summary(fetch_file(link.user_token, item["callbackURL"]))
        except OfficialError as exc:
            log.warning("activity file %s not fetched: %s", item.get("activityId"), exc)
            continue
        if not figures:
            continue
        figures["activityId"] = item["activityId"]
        kept += store.save_official_summaries(link.garmin_user_id, "activityFit",
                                              [(str(item["activityId"]), _start(item), figures)])
    return kept


def fetch_file(user_token: str, url: str) -> bytes:
    """The activity file at Garmin's callback address, for the linked account."""
    if urllib.parse.urlparse(url).hostname != urllib.parse.urlparse(API).hostname:
        raise OfficialError("The file isn't on Garmin's API.")
    access_token, _ = _access_token(user_token)
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}", "User-Agent": "daash-official"})
    try:
        data = _send(request)
    except urllib.error.URLError as exc:
        if isinstance(exc, urllib.error.HTTPError):
            raise _refused(exc, "activityFile") from None
        raise OfficialError("Daash couldn't reach Garmin for the file.") from None
    if len(data) > official_fit.MAX_FILE_BYTES:
        raise OfficialError("The file is too big to be an activity.")
    return data


# --------------------------------------------------------------------------
# Workouts through the Training API
# --------------------------------------------------------------------------

# Daash's sports, as the Training API names them. Walking and hiking have no
# sport of their own there.
# Daash's sports, as the Training API names them. Walking and hiking have no
# sport of their own there.
_SPORTS = {"running": "RUNNING", "cycling": "CYCLING", "swimming": "LAP_SWIMMING", "lap_swimming": "LAP_SWIMMING",
           "strength_training": "STRENGTH_TRAINING", "cardio_training": "CARDIO_TRAINING", "yoga": "YOGA",
           "pilates": "PILATES", "hiit": "HIIT"}
_SPORTS_BACK = {v: k for k, v in _SPORTS.items()} | {"LAP_SWIMMING": "swimming"}
_INTENSITY = {"warmup": "WARMUP", "cooldown": "COOLDOWN", "interval": "INTERVAL",
              "recovery": "RECOVERY", "rest": "REST"}
_INTENSITY_BACK = {v: k for k, v in _INTENSITY.items()} | {"ACTIVE": "interval", "MAIN": "interval"}
_TARGETS = {"pace.zone": "SPEED", "heart.rate.zone": "HEART_RATE", "power.zone": "POWER", "cadence.zone": "CADENCE"}
_TARGETS_BACK = {v: k for k, v in _TARGETS.items()} | {"PACE": "pace.zone", "SPEED_LAP": "pace.zone",
                                                        "HEART_RATE_LAP": "heart.rate.zone", "POWER_LAP": "power.zone"}
_STROKES = {"free": "FREESTYLE", "backstroke": "BACKSTROKE", "breaststroke": "BREASTSTROKE", "fly": "BUTTERFLY",
            "individual_medley": "IM", "mixed": "MIXED", "any": "CHOICE", "choice": "CHOICE", "drill": "CHOICE"}
_STROKES_BACK = {v: k for k, v in _STROKES.items()} | {"CHOICE": "any", "RIMO": "individual_medley"}
_EQUIPMENT = {"none": "NONE", "fins": "SWIM_FINS", "kickboard": "SWIM_KICKBOARD", "paddles": "SWIM_PADDLES",
              "pull_buoy": "SWIM_PULL_BUOY", "snorkel": "SWIM_SNORKEL"}
_EQUIPMENT_BACK = {v: k for k, v in _EQUIPMENT.items()}


def _field(obj: Any, name: str) -> Any:
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def _key(obj: Any, *names: str) -> str:
    """The first key-like string among nested Garmin type objects, lowercased."""
    for name in names:
        value = _field(obj, name)
        if isinstance(value, str) and value:
            return value.lower()
    return ""


def _step(step: Any, order: list[int], swim: bool) -> dict:
    order[0] += 1
    kind = _key(_field(step, "stepType"), "stepTypeKey")
    if kind == "repeat":
        here = order[0]
        children = [_step(child, order, swim) for child in (_field(step, "workoutSteps") or [])]
        return {"type": "WorkoutRepeatStep", "stepOrder": here, "repeatType": "REPEAT_UNTIL_STEPS_CMPLT",
                "repeatValue": float(_field(step, "numberOfIterations") or 0), "steps": children}
    condition = _key(_field(step, "endCondition"), "conditionTypeKey")
    value = _field(step, "endConditionValue")
    out: dict[str, Any] = {"type": "WorkoutStep", "stepOrder": order[0], "intensity": _INTENSITY.get(kind, "ACTIVE"),
                           "description": _field(step, "description") or None}
    if condition == "distance":
        out.update(durationType="DISTANCE", durationValue=float(value), durationValueType="METER")
    elif condition == "time" and swim and kind == "rest":
        out.update(durationType="FIXED_REST", durationValue=float(value), durationValueType=None)
    elif condition == "time":
        out.update(durationType="TIME", durationValue=float(value), durationValueType=None)
    elif condition == "reps":
        out.update(durationType="REPS", durationValue=float(value), durationValueType=None)
    else:
        out.update(durationType="OPEN", durationValue=None, durationValueType=None)
    target = _TARGETS.get(_key(_field(step, "targetType"), "workoutTargetTypeKey"))
    low, high = _field(step, "targetValueOne"), _field(step, "targetValueTwo")
    zone = _field(step, "zoneNumber")
    if target and low is not None and high is not None:
        out.update(targetType=target, targetValueLow=float(min(low, high)), targetValueHigh=float(max(low, high)))
    elif target in ("HEART_RATE", "POWER") and zone:
        out.update(targetType=target, targetValue=float(zone))
    else:
        out.update(targetType="OPEN", targetValueLow=None, targetValueHigh=None)
    # A strength set names its exercise and load; a swim its stroke and kit.
    category, exercise = _field(step, "category"), _field(step, "exerciseName")
    if category or exercise:
        out.update(exerciseCategory=category, exerciseName=exercise)
        grams = _field(step, "weightValue")
        if isinstance(grams, (int, float)) and grams > 0:
            out.update(weightValue=round(float(grams) / 1000, 2), weightDisplayUnit="KILOGRAM")
    stroke = _key(_field(step, "strokeType"), "strokeTypeKey")
    if swim and stroke:
        out["strokeType"] = _STROKES.get(stroke, "CHOICE")
    kit = _key(_field(step, "equipmentType"), "equipmentTypeKey")
    if swim and kit:
        out["equipmentType"] = _EQUIPMENT.get(kit, "NONE")
    return out


def training_workout(model: Any, sport: str | None = None) -> dict:
    """A workout built by workouts.build_workout, in the Training API's shape."""
    key = (sport or _key(_field(model, "sportType"), "sportTypeKey") or "").lower()
    api_sport = _SPORTS.get(key)
    if api_sport is None:
        raise OfficialError(f"The official API doesn't take {key or 'that kind of'} workouts yet.")
    swim = api_sport == "LAP_SWIMMING"
    segments = _field(model, "workoutSegments") or []
    order = [0]
    steps = [_step(step, order, swim) for segment in segments for step in (_field(segment, "workoutSteps") or [])]
    description = _field(model, "description")
    out = {
        "workoutName": _field(model, "workoutName"),
        "description": (description or "")[:1024] or None,
        "sport": api_sport,
        "workoutProvider": PROVIDER,
        "workoutSourceId": PROVIDER,
        "segments": [{"segmentOrder": 1, "sport": api_sport, "steps": steps}],
    }
    if swim:
        pool = _field(model, "poolLength")
        unit = _key(_field(model, "poolLengthUnit"), "unitKey")
        out.update(poolLength=float(pool) if isinstance(pool, (int, float)) else None,
                   poolLengthUnit="YARD" if unit.startswith("yard") else "METER")
        out["segments"][0].update(poolLength=out["poolLength"], poolLengthUnit=out["poolLengthUnit"])
    return out


def connect_workout(training: dict) -> dict:
    """A Training API workout back in Garmin Connect's shape, which the tools read."""
    sport_key = _SPORTS_BACK.get(str(training.get("sport") or ""), str(training.get("sport") or "").lower())
    segments = training.get("segments") or []
    steps = [step for segment in segments if isinstance(segment, dict) for step in (segment.get("steps") or [])]
    return {
        "workoutId": training.get("workoutId"),
        "workoutName": training.get("workoutName"),
        "description": training.get("description"),
        "sportType": {"sportTypeKey": sport_key},
        "estimatedDurationInSecs": training.get("estimatedDurationInSecs"),
        "estimatedDistanceInMeters": training.get("estimatedDistanceInMeters"),
        "poolLength": training.get("poolLength"),
        "poolLengthUnit": {"unitKey": "yard" if training.get("poolLengthUnit") == "YARD" else "meter"} if training.get("poolLength") else None,
        "workoutSegments": [{"segmentOrder": 1, "sportType": {"sportTypeKey": sport_key},
                             "workoutSteps": [_connect_step(step) for step in steps if isinstance(step, dict)]}],
    }


def _connect_step(step: dict) -> dict:
    if step.get("type") == "WorkoutRepeatStep":
        return {"type": "RepeatGroupDTO", "stepOrder": step.get("stepOrder"), "stepType": {"stepTypeKey": "repeat"},
                "numberOfIterations": int(step.get("repeatValue") or 0),
                "endCondition": {"conditionTypeKey": "iterations"}, "endConditionValue": step.get("repeatValue"),
                "workoutSteps": [_connect_step(s) for s in (step.get("steps") or []) if isinstance(s, dict)]}
    duration_type = str(step.get("durationType") or "OPEN")
    condition = {"TIME": "time", "FIXED_REST": "time", "DISTANCE": "distance", "REPS": "reps"}.get(duration_type, "lap.button")
    target = _TARGETS_BACK.get(str(step.get("targetType") or ""))
    out: dict[str, Any] = {
        "type": "ExecutableStepDTO", "stepOrder": step.get("stepOrder"),
        "stepType": {"stepTypeKey": _INTENSITY_BACK.get(str(step.get("intensity") or ""), "interval")},
        "endCondition": {"conditionTypeKey": condition},
        "endConditionValue": step.get("durationValue") if condition != "lap.button" else None,
        "targetType": {"workoutTargetTypeKey": target or "no.target"},
        "targetValueOne": step.get("targetValueLow") if target else None,
        "targetValueTwo": step.get("targetValueHigh") if target else None,
        "zoneNumber": int(step["targetValue"]) if target and step.get("targetValue") and step.get("targetValueLow") is None else None,
        "description": step.get("description") or None,
    }
    if step.get("exerciseCategory") or step.get("exerciseName"):
        out.update(category=step.get("exerciseCategory"), exerciseName=step.get("exerciseName"))
        if isinstance(step.get("weightValue"), (int, float)):
            out.update(weightValue=float(step["weightValue"]) * 1000, weightUnit={"unitKey": "kilogram", "factor": 1000.0})
    if step.get("strokeType"):
        out["strokeType"] = {"strokeTypeKey": _STROKES_BACK.get(str(step["strokeType"]), "any")}
    if step.get("equipmentType") and step["equipmentType"] != "NONE":
        out["equipmentType"] = {"equipmentTypeKey": _EQUIPMENT_BACK.get(str(step["equipmentType"]), "none")}
    return out


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


def scheduled(user_token: str, start: str, end: str) -> dict:
    """What Garmin's Training API lists on the calendar between two dates, as Garmin returns it.

    The question it answers is which scheduled workouts Garmin shows an app:
    only the ones this app scheduled, or everything on the person's calendar.
    """
    found = call(user_token, "GET", f"/training-api/schedule?startDate={start}&endDate={end}")
    rows = found if isinstance(found, list) else found.get("workoutSchedules") if isinstance(found, dict) else None
    if not isinstance(rows, list):
        return {"from": start, "to": end, "raw": found}
    listed = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        listed.append({k: v for k, v in row.items() if k != "workout"} | (
            {"workout_name": row["workout"].get("workoutName"), "sport": row["workout"].get("sport")}
            if isinstance(row.get("workout"), dict) else {}))
    return {"from": start, "to": end, "count": len(listed), "scheduled": listed}


def read(user_token: str, what: str) -> list[dict] | None:
    """The linked account's recent "activities" or "days", or None when not linked."""
    link = store.official_link(user_token)
    if link is None:
        return None
    if what == "activities":
        return official_data.activities(link.garmin_user_id)
    return official_data.days(link.garmin_user_id)


# --------------------------------------------------------------------------
# Status, for the preview tool
# --------------------------------------------------------------------------


def status(user_token: str) -> dict:
    from . import backfill  # it calls Garmin through this module

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
        "official_only": store.official_only(user_token) is not None,
        "permissions": link.permissions,
        "received": {kind: {"count": c["count"], "latest": fmt(c["latest_start"]), "arrived": fmt(c["last_received"])}
                     for kind, c in sorted(counts.items())},
        "history": backfill.summary(link.garmin_user_id),
        "push_url": push_url(),
        "push_setup": "Paste push_url into every summary type at apis.garmin.com/tools/endpoints, "
                      "including deregistrations and user permissions.",
    }
