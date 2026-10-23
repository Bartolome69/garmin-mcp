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
import re
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
    RegistrationError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from . import analytics, store

# Long enough to sign in to Garmin and fetch a code from email, no longer.
CODE_TTL = 600
ACCESS_TTL = 3600
# A year, counted from the last renewal rather than from sign-in: every refresh
# issues a new token with a new year. Connecting is meant to be done once, so a
# break for injury or an off-season must not end it. Only a connection nobody
# has touched in a year lapses, and Disconnect or a fresh sign-in still ends
# one immediately.
REFRESH_TTL = 60 * 60 * 24 * 365
# How long a refresh token still works after it has been exchanged. Claude can
# send the same refresh twice, from two requests that both found the access
# token expired, or retry one whose answer it never received. With no grace
# the second exchange fails and Claude reports the connection as expired,
# which only signing in again fixes. Two minutes covers that and keeps a
# stolen token worth no more than it was.
REFRESH_REUSE_GRACE = 120

# Authorization requests waiting for someone to finish signing in. In memory on
# purpose: a restart just means starting the sign-in again, and nothing here
# outlives the attempt.
_PENDING_AUTH: dict[str, dict[str, Any]] = {}

# Sign-ins that succeeded and are waiting for the person to approve the client
# on the consent page. Separate from _PENDING_AUTH because the Garmin session
# already exists by this point; only the handover to the client is outstanding.
_AWAITING_CONSENT: dict[str, dict[str, Any]] = {}

# Where a code may be sent without comment. Anything else still works, because
# refusing an unrecognised client would break ChatGPT and whatever comes next,
# but the consent page says loudly where it is going.
CLAUDE_CALLBACK = "https://claude.ai/api/mcp/auth_callback"
# ChatGPT sends each person's connection back to its own address: this fixed
# part, then an ID for that one connector (seen Oct 14: .../oauth/FEJSMEKkIk6j).
# Matched exactly apart from the ID, so no other path on chatgpt.com counts.
CHATGPT_CALLBACK = re.compile(r"https://chatgpt\.com/connector/oauth/[A-Za-z0-9_-]{1,64}")
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _extra_redirects() -> set[str]:
    raw = os.environ.get("GARMIN_MCP_ALLOWED_REDIRECTS", "")
    return {u.strip() for u in raw.split(",") if u.strip()}


def strict_redirects() -> bool:
    """Refuse unrecognised redirect targets outright rather than warning.

    Off by default: the set of legitimate MCP clients is still growing, and a
    hard list would lock out ChatGPT before anyone noticed. Worth turning on
    once the clients in use here are known.
    """
    return os.environ.get("GARMIN_MCP_STRICT_REDIRECTS", "").strip() == "1"


def app_for_redirect(uri: str) -> str:
    """Which app a sign-in is for, read from where its code is sent.

    For the reports: whether people use Daash from Claude, ChatGPT or
    something else. The redirect decides where the code goes, so unlike the
    name an app declares about itself at registration, it can't be dressed up.
    """
    from urllib.parse import urlparse

    parsed = urlparse(str(uri))
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "http" and host in LOOPBACK_HOSTS:
        return "Desktop or command line"
    for app, domains in (("Claude", ("claude.ai", "claude.com")), ("ChatGPT", ("chatgpt.com", "openai.com"))):
        if any(host == d or host.endswith("." + d) for d in domains):
            return app
    return "Other"


def app_for_flow(flow: str) -> str | None:
    """The app a sign-in in progress is for, while its request is parked."""
    entry = _PENDING_AUTH.get(flow)
    if not entry:
        return None
    return app_for_redirect(entry["params"].redirect_uri)


_APP_FOR_CLIENT: dict[str, str] = {}


def app_for_client(client_id: str) -> str:
    """The app a registered client is, from the redirects it registered."""
    if client_id not in _APP_FOR_CLIENT:
        raw = store.load_oauth_client(client_id)
        uris: list[str] = []
        if raw:
            try:
                uris = json.loads(raw).get("redirect_uris") or []
            except (ValueError, AttributeError):
                uris = []
        apps = {app_for_redirect(u) for u in uris}
        _APP_FOR_CLIENT[client_id] = apps.pop() if len(apps) == 1 else ("Other" if apps else "Unknown")
    return _APP_FOR_CLIENT[client_id]


def recognised_redirect(uri: str) -> bool:
    """Whether a code going here is business as usual.

    Loopback covers the desktop and CLI clients, which register a callback on
    an arbitrary local port and cannot be listed in advance.
    """
    from urllib.parse import urlparse

    uri = str(uri)
    if uri == CLAUDE_CALLBACK or CHATGPT_CALLBACK.fullmatch(uri) or uri in _extra_redirects():
        return True
    parsed = urlparse(uri)
    return parsed.scheme == "http" and parsed.hostname in LOOPBACK_HOSTS


# The address Daash is listed under in ChatGPT's and Claude's app directories.
# It is the same server as /mcp, for accounts that sign up by approving Daash
# on Garmin's own page: clients find it through its own resource metadata,
# ask for a token for it by name (RFC 8707), and that name is how a sign-in
# is known to have come from a directory.
LISTED_PATH = "/listed/mcp"


def _sweep() -> None:
    cutoff = time.time() - CODE_TTL
    for key, value in list(_PENDING_AUTH.items()):
        if value["started"] < cutoff:
            _PENDING_AUTH.pop(key, None)
    for key, value in list(_AWAITING_CONSENT.items()):
        if value["started"] < cutoff:
            _AWAITING_CONSENT.pop(key, None)


def _issued_for(resource: str | None) -> str | None:
    """The resource a token is stamped with.

    A token asked for by the listed address is stamped with this server's own
    resource: the listed address is the same endpoint, and the SDK accepts a
    token only for exactly the resource it serves. Its name has done its job
    by then, at /authorize.
    """
    if not resource or str(resource).rstrip("/").endswith(LISTED_PATH):
        return _own_resource()
    return resource


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
        if strict_redirects():
            for uri in client_info.redirect_uris or []:
                if not recognised_redirect(str(uri)):
                    raise RegistrationError(
                        error="invalid_redirect_uri",
                        error_description=(
                            f"{uri} is not an allowed redirect for this server."
                        ),
                    )
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
            # Kept for the consent page. Self-declared at registration, so it
            # is shown as a claim rather than trusted, alongside the redirect
            # that actually determines where the code goes.
            "client_name": getattr(client, "client_name", None),
            "params": params,
            "started": time.time(),
            # Asked for by the listed address: Garmin's own sign-in only.
            "listed": str(params.resource or "").rstrip("/").endswith(LISTED_PATH),
        }
        return f"/connect?{urlencode({'flow': flow})}"

    def pending(self, flow: str) -> dict[str, Any] | None:
        _sweep()
        return _PENDING_AUTH.get(flow)

    def listed(self, flow: str) -> bool:
        """Whether a waiting sign-in came from the address in the app directories."""
        entry = self.pending(flow) if flow else None
        return bool(entry and entry.get("listed"))

    def stage(self, flow: str, subject: str) -> tuple[str, dict[str, Any]] | None:
        """Sign-in succeeded; hold the handover until the person approves it.

        Registration is open to anyone, so a client asking for this code is not
        evidence that the person wanted it. Somebody can register a client
        pointing anywhere, send a crafted /authorize link, and collect a code
        from a sign-in that looked entirely normal on our own domain. PKCE does
        not help, because the attacker is the client and holds the verifier.

        The defence is that the person has to see who is asking and where the
        code goes, and say yes.
        """
        _sweep()
        entry = _PENDING_AUTH.get(flow)
        if entry is None:
            return None
        token = secrets.token_urlsafe(32)
        _AWAITING_CONSENT[token] = {
            "flow": flow,
            "subject": subject,
            "started": time.time(),
        }
        return token, entry

    def staged_subject(self, consent_token: str) -> str | None:
        """Whose sign-in a consent token holds, without spending the token."""
        entry = _AWAITING_CONSENT.get(consent_token)
        return entry["subject"] if entry else None

    def staged(self, consent_token: str) -> tuple[str, dict[str, Any]] | None:
        """(subject, the parked request) behind a consent token, without spending it."""
        _sweep()
        entry = _AWAITING_CONSENT.get(consent_token)
        parked = _PENDING_AUTH.get(entry["flow"]) if entry else None
        return (entry["subject"], parked) if entry and parked else None

    def restage(self, consent_token: str, subject: str) -> bool:
        """Point a waiting consent at another account: the one a Garmin approval was just joined to."""
        entry = _AWAITING_CONSENT.get(consent_token)
        if entry is None:
            return False
        entry["subject"] = subject
        return True

    def approve(self, consent_token: str) -> str | None:
        """Consent given: mint the code and hand back the return URL."""
        entry = _AWAITING_CONSENT.pop(consent_token, None)
        if entry is None:
            return None
        return self.complete(entry["flow"], entry["subject"])

    def deny(self, consent_token: str) -> str | None:
        """Consent refused: tell the client so, rather than leaving it hanging.

        The Garmin session stays; refusing a client is not disconnecting.
        """
        entry = _AWAITING_CONSENT.pop(consent_token, None)
        if entry is None:
            return None
        parked = _PENDING_AUTH.pop(entry["flow"], None)
        if parked is None:
            return None
        params: AuthorizationParams = parked["params"]
        returned = {
            "error": "access_denied",
            "error_description": "The person refused this client.",
        }
        if params.state:
            returned["state"] = params.state
        separator = "&" if "?" in str(params.redirect_uri) else "?"
        return f"{params.redirect_uri}{separator}{urlencode(returned)}"

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
            resource=_issued_for(authorization_code.resource),
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        # Looked at first: load_oauth_token deletes an expired row as it reads it.
        seen = store.peek_oauth_token(refresh_token, "refresh")
        row = store.load_oauth_token(refresh_token, "refresh")
        if not row or row["client_id"] != client.client_id:
            _refresh_refused(seen, client.client_id)
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
        # One use, give or take a moment: the old refresh token stops working
        # REFRESH_REUSE_GRACE after the exchange, so a duplicate request from
        # Claude still lands but a stolen one is soon worth nothing.
        store.expire_oauth_token_by(refresh_token.token, int(time.time()) + REFRESH_REUSE_GRACE)
        return self._issue(
            client_id=client.client_id,
            subject=refresh_token.subject or "",
            scopes=scopes or refresh_token.scopes,
            resource=_issued_for(refresh_token.resource),
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


def _refresh_refused(seen: dict | None, client_id: str) -> None:
    """Report a refused refresh: to Claude it is a connection that has expired.

    Nothing else shows it. The person sees "connection expired" in Claude and
    the server only answered 400, so this is how a broken link gets noticed
    before someone has to message about it.
    """
    if not seen:
        reason = "unknown"
    elif seen["client_id"] != client_id:
        reason = "other_client"
    elif seen["expires_at"] is not None and seen["expires_at"] < (seen["created_at"] or 0) + REFRESH_TTL - 60:
        reason = "reused_after_rotation"
    else:
        reason = "expired"
    user = store.get_user(seen["subject"]) if seen and seen.get("subject") else None
    props: dict[str, Any] = {"reason": reason}
    if user:
        props["account"] = user.email_masked
        email = store.email_for(user.user_token)
        if email:
            props["email"] = email
    analytics.capture(
        "connection_refresh_refused",
        (user.email_hash or user.user_token) if user else None,
        props,
    )
