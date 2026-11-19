"""OAuth 2.1 for hosted PageMint, with GitHub as the sign-in.

PageMint is the OAuth authorization server that claude.ai talks to (dynamic
client registration, PKCE, refresh, revocation — handled by the MCP SDK's
framework). To find out who the person is, PageMint sends them to GitHub,
then issues its own short-lived tokens. GitHub tokens are used once and never
stored; PageMint's tokens are stored only as hashes.
"""

from __future__ import annotations

import html
import logging
import secrets
import time
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from pagemint.errors import PageMintError
from pagemint.hosted import github
from pagemint.hosted.store import HostedStore

log = logging.getLogger("pagemint.hosted")

SCOPE = "pagemint"
ACCESS_TTL = 3600
REFRESH_TTL = 30 * 86400
CODE_TTL = 300
LOGIN_TTL = 600
CALLBACK_PATH = "/oauth/github/callback"
# Only these apps may receive sign-in codes: Claude's own callback pages, and the person's own computer
# (loopback, used by local MCP clients). Anything else could be used to steal a signed-in user's access.
KNOWN_REDIRECT_URIS = {"https://claude.ai/api/mcp/auth_callback", "https://claude.com/api/mcp/auth_callback"}
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
MAX_PENDING = 10_000
MAX_CLIENTS = 10_000
PURGE_EVERY = 600  # seconds


def redirect_allowed(uri: str, extra: set[str] | None = None) -> bool:
    if uri in KNOWN_REDIRECT_URIS or (extra and uri in extra):
        return True
    try:
        p = urlsplit(uri)
    except ValueError:
        return False
    return p.scheme == "http" and (p.hostname or "") in LOOPBACK_HOSTS


def auth_settings(settings) -> AuthSettings:
    base = settings.public_url.rstrip("/")
    return AuthSettings(
        issuer_url=base,
        resource_server_url=f"{base}/mcp",
        client_registration_options=ClientRegistrationOptions(enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]),
        revocation_options=RevocationOptions(enabled=True),
        required_scopes=[SCOPE],
        validate_token_resource=False,  # one resource only: every token is for this server's /mcp
    )


def page(status: int, title: str, message: str) -> HTMLResponse:
    body = (
        "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
        f"<title>PageMint – {html.escape(title)}</title></head>"
        "<body style='font-family:system-ui;max-width:36rem;margin:3rem auto;padding:0 1rem'>"
        f"<h1>{html.escape(title)}</h1><p>{html.escape(message)}</p></body></html>"
    )
    return HTMLResponse(body, status_code=status)


class PageMintOAuthProvider:
    def __init__(self, store: HostedStore, settings, client):
        self.store = store
        self.settings = settings
        self.client = client
        self._last_purge = time.time()

    def maybe_purge(self) -> None:
        """Delete expired codes, logins and tokens every few minutes."""
        if time.time() - self._last_purge >= PURGE_EVERY:
            self._last_purge = time.time()
            self.store.purge_expired()

    def _user_allowed(self, user_id: str | None) -> bool:
        """Re-checked on every token use: blocked users and users removed from the list lose access."""
        user = self.store.get_user(user_id) if user_id else None
        if not user or user.get("blocked"):
            return False
        allowed = (self.settings.allowed_users or "").strip()
        if allowed == "*":
            return True
        return user["login"].lower() in {x.strip().lower() for x in allowed.split(",") if x.strip()}

    @property
    def _base(self) -> str:
        return self.settings.public_url.rstrip("/")

    # ---- clients ---------------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        info = self.store.get_client(client_id)
        return OAuthClientInformationFull.model_validate(info) if info else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self.maybe_purge()
        uris = [str(u) for u in (client_info.redirect_uris or [])]
        extra = {x.strip() for x in (getattr(self.settings, "allowed_redirect_uris", "") or "").split(",") if x.strip()}
        bad = [u for u in uris if not redirect_allowed(u, extra)]
        if not uris or bad:
            raise RegistrationError(
                error="invalid_redirect_uri",
                error_description="Only Claude's callback addresses and this computer's own (loopback) addresses "
                                  "can be used with PageMint.",
            )
        if self.store.count("clients") >= MAX_CLIENTS:
            raise RegistrationError(error="invalid_client_metadata",
                                    error_description="PageMint cannot register more apps right now.")
        self.store.save_client(client_info.client_id, client_info.model_dump(mode="json", exclude_none=True))

    # ---- authorize: send the person to GitHub ------------------------------

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        self.maybe_purge()
        if self.store.count("pending") >= MAX_PENDING:
            self.store.purge_expired()
            if self.store.count("pending") >= MAX_PENDING:
                raise AuthorizeError(error="temporarily_unavailable",
                                     error_description="Too many sign-ins in progress; try again in a few minutes.")
        state = secrets.token_urlsafe(32)
        self.store.put_pending(state, {"client_id": client.client_id, "params": params.model_dump(mode="json")},
                               expires_at=time.time() + LOGIN_TTL)
        query = urlencode({
            "client_id": self.settings.github_client_id,
            "redirect_uri": self._base + CALLBACK_PATH,
            "scope": github.SCOPE,
            "state": state,
            "allow_signup": "true",
        })
        return f"{github.AUTHORIZE_URL}?{query}"

    def _refusal(self, login: str, created_at: str | None) -> str | None:
        allowed = (self.settings.allowed_users or "").strip()
        if allowed != "*":
            names = {x.strip().lower() for x in allowed.split(",") if x.strip()}
            if login.lower() not in names:
                return "PageMint is invite-only for now. Ask the owner to add your GitHub username."
            return None
        min_days = int(self.settings.min_github_account_days or 0)
        if min_days and created_at:
            try:
                created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except ValueError:
                created = None
            if created is None or (datetime.now(timezone.utc) - created).days < min_days:
                return f"Your GitHub account must be at least {min_days} days old to use PageMint."
        return None

    async def github_callback(self, request: Request) -> Response:
        q = request.query_params
        pending = self.store.take_pending(q.get("state") or "")
        if pending is None:
            return page(400, "Sign-in expired", "This sign-in link has expired or was already used. "
                                                "Start again from Claude.")
        if q.get("error"):
            return page(403, "Sign-in cancelled", "GitHub sign-in was cancelled, so PageMint was not connected.")
        try:
            gh_token = await github.exchange_code(self.client, self.settings, q.get("code") or "",
                                                  self._base + CALLBACK_PATH)
            user = await github.fetch_user(self.client, gh_token)
        except PageMintError as e:
            log.warning("GitHub sign-in failed: %s", e.message)
            return page(502, "GitHub sign-in failed", e.message + " Please try again.")
        existing = self.store.get_user_by_github_id(user["id"])
        if existing and existing.get("blocked"):
            return page(403, "Not available", "This account has been blocked from PageMint.")
        refusal = self._refusal(user["login"], user.get("created_at"))
        if refusal:
            log.info("sign-in refused for GitHub user id %s", user["id"])
            return page(403, "Not available", refusal)
        user_id = self.store.upsert_user(user["id"], user["login"])
        params = pending["params"]
        code = secrets.token_urlsafe(32)
        expires = time.time() + CODE_TTL
        self.store.put_code(code, {
            "client_id": pending["client_id"],
            "scopes": params.get("scopes") or [SCOPE],
            "expires_at": expires,
            "code_challenge": params["code_challenge"],
            "redirect_uri": params["redirect_uri"],
            "redirect_uri_provided_explicitly": params["redirect_uri_provided_explicitly"],
            "resource": params.get("resource"),
            "subject": user_id,
        }, expires_at=expires)
        return RedirectResponse(construct_redirect_uri(params["redirect_uri"], code=code, state=params.get("state")),
                                status_code=302)

    # ---- codes and tokens --------------------------------------------------

    async def load_authorization_code(self, client: OAuthClientInformationFull, authorization_code: str):
        data = self.store.peek_code(authorization_code)
        if not data or data["client_id"] != client.client_id:
            return None
        return AuthorizationCode(code=authorization_code, **data)

    def _issue(self, client_id: str, scopes: list[str], resource: str | None, subject: str) -> OAuthToken:
        now = int(time.time())
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        base = {"client_id": client_id, "scopes": scopes, "resource": resource, "subject": subject}
        self.store.put_token(access, "access", {**base, "expires_at": now + ACCESS_TTL}, expires_at=now + ACCESS_TTL)
        self.store.put_token(refresh, "refresh", {**base, "expires_at": now + REFRESH_TTL}, expires_at=now + REFRESH_TTL)
        return OAuthToken(access_token=access, expires_in=ACCESS_TTL, scope=" ".join(scopes), refresh_token=refresh)

    async def exchange_authorization_code(self, client: OAuthClientInformationFull, authorization_code) -> OAuthToken:
        data = self.store.take_code(authorization_code.code)  # single use
        if not data or data["client_id"] != client.client_id:
            raise TokenError(error="invalid_grant", error_description="authorization code is invalid or used")
        return self._issue(client.client_id, data["scopes"], data.get("resource"), data["subject"])

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str):
        data = self.store.get_token(refresh_token, "refresh")
        if not data or data["client_id"] != client.client_id or not self._user_allowed(data.get("subject")):
            return None
        return RefreshToken(token=refresh_token, **data)

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token, scopes: list[str]) -> OAuthToken:
        if self.store.get_token(refresh_token.token, "refresh") is None or not self._user_allowed(refresh_token.subject):
            raise TokenError(error="invalid_grant", error_description="refresh token is invalid")
        self.store.revoke_token(refresh_token.token)  # rotation: each refresh token works once
        return self._issue(client.client_id, scopes or refresh_token.scopes, refresh_token.resource, refresh_token.subject)

    async def load_access_token(self, token: str):
        data = self.store.get_token(token, "access")
        if not data or not self._user_allowed(data.get("subject")):
            return None
        return AccessToken(token=token, **data)

    async def revoke_token(self, token) -> None:
        self.store.revoke_token(token.token)
