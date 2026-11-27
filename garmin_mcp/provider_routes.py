"""Connecting and disconnecting providers besides Garmin, from the app (providers/catalog.py).

Each provider's card shows only to people its flag is on for (provider_intervals_enabled:
Daash's own accounts and the PostHog flag; Bart first). Everyone else gets the
same 404 an unknown /app/api/ path gives, so nothing says the provider exists.

  GET  /app/api/providers                    the cards this person may see
  POST /app/api/providers/{name}/connect     {auth, credentials}: checked with the provider, stored encrypted
  POST /app/api/providers/{name}/disconnect  revoked where the provider can, then deleted

The credentials are checked by the provider's Connector before anything is
stored. No adapter registered yet means Connect answers plainly that it isn't
ready: nothing is stored, nothing is guessed about the provider's API.

Events: provider_connected, provider_disconnected and provider_error, each with
the provider and, for errors, the kind; never the credentials.
"""

from __future__ import annotations

import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import Response

from . import analytics, store
from . import app as app_routes
from .providers import catalog
from .server import mcp

log = logging.getLogger(__name__)

PREFIX = app_routes.API_PREFIX + "providers"
NOT_FOUND = {"error": "Not found"}


def _who() -> str | None:
    from . import official

    return official.current()


def _subject(user_token: str) -> str:
    user = store.get_user(user_token)
    return (user.email_hash if user and user.email_hash else None) or user_token


def _flag_on(flag: str, subject: str | None) -> bool:
    if not subject:
        return False
    return analytics.distinct_id(subject) in analytics.internal_ids() or analytics.flag_on(flag, subject)


def visible_to(user_token: str | None) -> list[catalog.Listing]:
    """The providers this person may see. None outside a signed-in request, or for the local server."""
    if not user_token or user_token == "local" or store.get_user(user_token) is None:
        return []
    return catalog.visible(_subject(user_token), _flag_on)


def card(user_token: str, l: catalog.Listing) -> dict[str, Any]:
    """One provider as the Connections card draws it."""
    conn = store.provider_connection(user_token, l.name)
    state = "connect" if conn is None else "live" if conn.status == "connected" else "reconnect"
    return {
        "name": l.name, "label": l.label, "via": l.via, "explainer": l.explainer, "auth": list(l.auth),
        "state": state,
        "words": {"connect": "Not connected", "live": f"Connected to {l.label}", "reconnect": "Reconnect needed"}[state],
        "error": conn.last_error if conn and conn.status != "connected" else None,
        "since": conn.created_at if conn else None,
        "can_connect": catalog.connector(l.name) is not None,
    }


def you_rows(user_token: str | None) -> list[dict[str, Any]]:
    """Rows for the You screen's Connections group (app.you_payload); empty when no provider is visible."""
    rows = []
    for l in visible_to(user_token):
        c = card(user_token, l)  # type: ignore[arg-type]
        rows.append({"l": l.label, "action": f"provider-{l.name}",
                     **({"v": "Live", "tone": "ok", "sub": l.via} if c["state"] == "live" else
                        {"v": "Reconnect", "tone": "red", "sub": c["error"] or "Reconnect needed"} if c["state"] == "reconnect" else
                        {"v": "Connect", "tone": "red", "sub": l.explainer})})
    return rows


def _track(event: str, user_token: str, properties: dict[str, Any]) -> None:
    app_routes._track(event, {k: v for k, v in properties.items() if v is not None}, user_token)


def provider_failed(user_token: str, provider: str, kind: str, words: str | None = None) -> None:
    """For adapters: the provider refused or failed for this person. Marks the card
    Reconnect needed when the grant itself is gone, and reports the kind."""
    if kind in ("auth_revoked", "auth_expired"):
        store.mark_provider_error(user_token, provider, words or "Reconnect needed")
    _track("provider_error", user_token, {"provider": provider, "error_type": kind})


def _gate(name: str | None = None) -> tuple[str, catalog.Listing | None] | None:
    user_token = _who()
    shown = visible_to(user_token)
    if not shown:
        return None
    if name is None:
        return user_token, None  # type: ignore[return-value]
    l = next((x for x in shown if x.name == name), None)
    return (user_token, l) if l else None  # type: ignore[return-value]


def _not_found() -> Response:
    return app_routes._json(dict(NOT_FOUND), 404)


async def _body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


@mcp.custom_route(PREFIX, methods=["GET"])
async def api_providers(request: Request) -> Response:
    gate = _gate()
    if gate is None:
        return _not_found()
    user_token, _ = gate
    return app_routes._json({"providers": [card(user_token, l) for l in visible_to(user_token)]})


@mcp.custom_route(PREFIX + "/{name}/connect", methods=["POST"])
async def api_connect(request: Request) -> Response:
    gate = _gate(request.path_params["name"])
    if gate is None:
        return _not_found()
    user_token, l = gate
    assert l is not None
    body = await _body(request)
    auth = str(body.get("auth") or "")
    credentials = body.get("credentials")
    if auth not in l.auth:
        return app_routes._json({"error": f"{l.label} connects by {' or '.join(a.replace('_', ' ') for a in l.auth)}."}, 400)
    if not isinstance(credentials, dict) or not credentials:
        return app_routes._json({"error": "Nothing to connect with."}, 400)
    connector = catalog.connector(l.name)
    if connector is None:
        return app_routes._json({"error": f"Connecting {l.label} isn't ready yet. Nothing was stored."}, 409)
    try:
        external_id = connector.verify(auth, credentials)
    except catalog.ConnectError as exc:
        _track("provider_error", user_token, {"provider": l.name, "error_type": "refused", "auth": auth})
        return app_routes._json({"error": str(exc) or f"{l.label} didn't accept that."}, 400)
    except Exception:  # noqa: BLE001 - the provider was unreachable or answered oddly
        log.exception("connecting %s failed", l.name)
        _track("provider_error", user_token, {"provider": l.name, "error_type": "unreachable", "auth": auth})
        return app_routes._json({"error": f"{l.label} didn't answer. Try again in a moment."}, 502)
    again = store.provider_connection(user_token, l.name) is not None
    store.save_provider_connection(user_token, l.name, auth, credentials, external_id)
    _track("provider_connected", user_token, {"provider": l.name, "auth": auth, "reconnect": again or None})
    return app_routes._json({"provider": card(user_token, l), "message": f"Connected to {l.label}."})


@mcp.custom_route(PREFIX + "/{name}/disconnect", methods=["POST"])
async def api_disconnect(request: Request) -> Response:
    gate = _gate(request.path_params["name"])
    if gate is None:
        return _not_found()
    user_token, l = gate
    assert l is not None
    conn = store.provider_connection(user_token, l.name)
    connector = catalog.connector(l.name)
    if conn is not None and connector is not None:
        try:
            connector.revoke(conn.credentials)
        except Exception:  # noqa: BLE001 - Daash forgets it anyway; the provider's side is best effort
            log.warning("revoking %s failed", l.name, exc_info=True)
            _track("provider_error", user_token, {"provider": l.name, "error_type": "revoke_failed"})
    gone = store.delete_provider_connection(user_token, l.name)
    if gone:
        _track("provider_disconnected", user_token, {"provider": l.name})
    return app_routes._json({"provider": card(user_token, l),
                             "message": f"Disconnected from {l.label}. Daash deleted what it kept from it."})
