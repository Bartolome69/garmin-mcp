"""OAuth 2.1 for the hosted server, so the credential leaves the URL.

The first hosted design made the connector URL itself the credential. It worked,
and for a handful of friends it was a fair trade, but it has no way to expire or
revoke one client without revoking them all, and the secret travels somewhere it
is easy to leak: browser history, access logs, referrer headers, a screenshot.

Here the URL is public — everyone shares `/mcp` — and the credential is a bearer
token in an Authorization header. The MCP SDK implements the protocol itself
(`/authorize`, `/token`, `/register`, `/revoke` and the metadata documents); this
supplies the storage behind it and the one piece that is ours: the authorization
step is the existing Garmin sign-in.

The flow:

    Claude -> /authorize  ->  we park the request, redirect to our sign-in page
    person signs in to Garmin (and MFA, as before)
    we mint a one-use code and send them back to Claude
    Claude -> /token      ->  access token + refresh token, bound to that person

`subject` on every token is the user_token that owns the Garmin session, which
is what the request binding resolves back to.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from typing import Any
from urllib.parse import urlencode

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from . import store

# Long enough to sign in to Garmin and fetch a code from email, no longer.
CODE_TTL = 600
ACCESS_TTL = 3600
REFRESH_TTL = 60 * 60 * 24 * 30

# Authorization requests waiting for someone to finish signing in. In memory on
# purpose: a restart just means starting the sign-in again, and nothing here
# outlives the attempt.
_PENDING_AUTH: dict[str, dict[str, Any]] = {}


def _sweep() -> None:
    cutoff = time.time() - CODE_TTL
    for key, value in list(_PENDING_AUTH.items()):
        if value["started"] < cutoff:
            _PENDING_AUTH.pop(key, None)


def _own_resource() -> str | None:
    """This server's identity as an OAuth resource, in the SDK's canonical form.

    Tokens are bound to it so one minted for somewhere else cannot be replayed
    here. A client that asks for no resource still gets a token stamped with
    ours, rather than an unbound one that the audience check then refuses.
    """
    base = os.environ.get("GARMIN_MCP_BASE_URL", "").strip().rstrip("/")
    if not base:
        return None
    from mcp.shared.auth_utils import resource_url_from_server_url
    from pydantic import AnyHttpUrl

    return resource_url_from_server_url(AnyHttpUrl(base))


class GarminOAuthProvider(OAuthAuthorizationServerProvider):
    """Storage and the Garmin sign-in step; the SDK does the protocol."""

    # -- clients ----------------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        raw = store.load_oauth_client(client_id)
        return OAuthClientInformationFull.model_validate_json(raw) if raw else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        store.save_oauth_client(client_info.client_id, client_info.model_dump_json())

    # -- authorization ----------------------------------------------------

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Park the request and send the person to sign in to Garmin.

        Returning a URL rather than a code: nothing can be issued until we know
        whose Garmin account this is, and finding that out means a password and
        possibly a code from their email.
        """
        _sweep()
        flow = secrets.token_urlsafe(24)
        _PENDING_AUTH[flow] = {
            "client_id": client.client_id,
            "params": params,
            "started": time.time(),
        }
        return f"/connect?{urlencode({'flow': flow})}"

    def pending(self, flow: str) -> dict[str, Any] | None:
        _sweep()
        return _PENDING_AUTH.get(flow)

    def complete(self, flow: str, subject: str) -> str | None:
        """Sign-in finished: mint a one-use code and hand back the return URL."""
        entry = _PENDING_AUTH.pop(flow, None)
        if entry is None:
            return None
        params: AuthorizationParams = entry["params"]

        code = secrets.token_urlsafe(32)
        record = AuthorizationCode(
            code=code,
            scopes=params.scopes or [],
            expires_at=time.time() + CODE_TTL,
            client_id=entry["client_id"],
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
            subject=subject,
        )
        store.save_oauth_code(
            code, record.model_dump_json(), int(record.expires_at)
        )

        returned = {"code": code}
        if params.state:
            returned["state"] = params.state
        separator = "&" if "?" in str(params.redirect_uri) else "?"
        return f"{params.redirect_uri}{separator}{urlencode(returned)}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        raw = store.take_oauth_code(authorization_code)
        if not raw:
            return None
        record = AuthorizationCode.model_validate_json(raw)
        # A code belongs to the client it was issued to, and to no other.
        if record.client_id != client.client_id:
            return None
        if record.expires_at < time.time():
            return None
        return record

    # -- tokens -----------------------------------------------------------

    def _issue(
        self, *, client_id: str, subject: str, scopes: list[str], resource: str | None
    ) -> OAuthToken:
        access = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(32)
        now = int(time.time())
        store.save_oauth_token(
            access, kind="access", client_id=client_id, subject=subject,
            scopes=" ".join(scopes), resource=resource, expires_at=now + ACCESS_TTL,
        )
        store.save_oauth_token(
            refresh, kind="refresh", client_id=client_id, subject=subject,
            scopes=" ".join(scopes), resource=resource, expires_at=now + REFRESH_TTL,
        )
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=ACCESS_TTL,
            refresh_token=refresh,
            scope=" ".join(scopes) or None,
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        return self._issue(
            client_id=client.client_id,
            subject=authorization_code.subject or "",
            scopes=authorization_code.scopes,
            resource=authorization_code.resource or _own_resource(),
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        row = store.load_oauth_token(refresh_token, "refresh")
        if not row or row["client_id"] != client.client_id:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=row["client_id"],
            scopes=row["scopes"],
            expires_at=row["expires_at"],
            resource=row["resource"],
            subject=row["subject"],
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        # One use: the old refresh token dies with the exchange, so a stolen one
        # is worth a single request and then stops working.
        store.delete_oauth_token(refresh_token.token)
        return self._issue(
            client_id=client.client_id,
            subject=refresh_token.subject or "",
            scopes=scopes or refresh_token.scopes,
            resource=refresh_token.resource or _own_resource(),
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        row = store.load_oauth_token(token, "access")
        if not row:
            return None
        return AccessToken(
            token=token,
            client_id=row["client_id"],
            scopes=row["scopes"],
            expires_at=row["expires_at"],
            resource=row["resource"],
            subject=row["subject"],
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        store.delete_oauth_token(token.token)
