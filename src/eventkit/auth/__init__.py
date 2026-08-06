"""Azure App Service Easy Auth as a dependency, not a function call.

What this replaces, and why each part matters:

* ``posted/backend/main.py`` calls ``is_admin_authorized(request)`` imperatively
  at the top of **18** handlers. That pattern is a security-bug generator: a new
  handler that forgets the line is silently public. A ``Depends`` registered on
  ``APIRouter(dependencies=[...])`` cannot be forgotten the same way.
* ``ticketed/backend/main.py:204-238`` and ``posted/backend/main.py:218-231``
  authenticate on ``X-MS-CLIENT-PRINCIPAL-NAME`` alone. That header is a plain
  string set by the same reverse-proxy layer that a misconfigured ingress or a
  direct container port can bypass; ``X-MS-CLIENT-PRINCIPAL`` is the base64 JSON
  claims blob Easy Auth also sets, and demanding *both* be present and mutually
  consistent (:attr:`EasyAuth.require_claims_header`) raises the bar from "spoof
  one header" to "spoof a well-formed claims blob too".
* ``ticketed/backend/main.py:106-201`` hand-writes ~90 lines of inline HTML for
  an access-denied page. Replaced by :func:`render_access_denied`, one small
  Jinja template, and a themed ``exception_handler``.

Two hardening rules are load-bearing and covered by tests, not just comments:

* :attr:`EasyAuth.dev_principal` has **no default**. Setting it while
  ``WEBSITE_SITE_NAME`` is present (Azure App Service sets this) raises
  :class:`~eventkit.errors.ConfigError` at construction time — refusing to boot
  is the only guard that cannot be silently ignored in a log nobody reads.
* An **empty allow-list denies everyone**, including a validly authenticated
  Easy Auth principal. There is no "empty means open" fallback anywhere in this
  module.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from pydantic import BaseModel

from ..errors import ConfigError
from ..identity import normalize_email

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastapi import FastAPI, HTTPException, Request, status

    from ..eventprofile.models import EventProfile

logger = logging.getLogger("eventkit.auth")

__all__ = [
    "AllowList",
    "DeniedTheme",
    "EasyAuth",
    "NotAuthorized",
    "Principal",
    "RedirectToLogin",
    "WsTicketError",
    "install",
    "issue_ws_ticket",
    "render_access_denied",
    "verify_ws_ticket",
    "ws_dependency",
]

#: Azure App Service Easy Auth headers. See
#: https://learn.microsoft.com/azure/app-service/configure-authentication-user-identities
HEADER_PRINCIPAL = "X-MS-CLIENT-PRINCIPAL"
HEADER_PRINCIPAL_NAME = "X-MS-CLIENT-PRINCIPAL-NAME"
HEADER_PRINCIPAL_ID = "X-MS-CLIENT-PRINCIPAL-ID"
HEADER_PRINCIPAL_IDP = "X-MS-CLIENT-PRINCIPAL-IDP"

_TEMPLATE_DIR = Path(__file__).parent / "templates"


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller, as much of it as Easy Auth handed us."""

    email: str
    display_name: str | None = None
    provider: str | None = None
    id: str | None = None
    claims: Mapping[str, Any] = field(default_factory=dict)


class AllowList:
    """A set of admin emails and/or ``@domain.tld`` suffixes.

    An empty list :meth:`allows` nothing — deny-all, not open — because the
    predecessor apps' failure mode for a missing setting was silent public
    access, and that is exactly backwards.
    """

    def __init__(self, entries: Iterable[str]) -> None:
        self._entries: frozenset[str] = frozenset(
            e.strip().lower() for e in entries if e and e.strip()
        )

    @classmethod
    def parse(cls, csv: str) -> AllowList:
        """Comma-separated entries, each lowercased and stripped."""
        return cls((csv or "").split(","))

    def allows(self, email: str | None) -> bool:
        if not self._entries:
            return False
        normalized = normalize_email(email)
        if normalized is None:
            return False
        if normalized in self._entries:
            return True
        return any(
            entry.startswith("@") and normalized.endswith(entry) for entry in self._entries
        )

    def __bool__(self) -> bool:
        return bool(self._entries)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"AllowList({sorted(self._entries)!r})"


class RedirectToLogin(Exception):
    """No principal at all, on a browser-facing path. Redirect, don't 401."""

    def __init__(self, post_login_redirect_url: str) -> None:
        self.post_login_redirect_url = post_login_redirect_url
        super().__init__(post_login_redirect_url)


class NotAuthorized(Exception):
    """A real, Easy-Auth-verified principal that the allow-list rejects."""

    def __init__(self, email: str) -> None:
        self.email = email
        super().__init__(email)


class WsTicketError(Exception):
    """A WebSocket ticket failed to verify: malformed, tampered, or expired."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class DeniedTheme(BaseModel):
    """What the access-denied page shows. No secrets — this can render for
    anyone who fails the allow-list, including someone probing the app."""

    app_title: str
    brand_color: str = "#e77500"
    logo_url: str | None = None
    support_contact: str | None = None
    logout_url: str = "/.auth/logout?post_logout_redirect_uri=/"

    @classmethod
    def from_profile(cls, profile: EventProfile) -> DeniedTheme:
        return cls(
            app_title=profile.event.title,
            brand_color=profile.branding.brand_color,
            logo_url=profile.branding.logo_url,
            support_contact=profile.event.contact_email,
        )


_jinja_env: Any = None


def _template_env() -> Any:
    global _jinja_env
    if _jinja_env is None:
        from jinja2 import Environment, FileSystemLoader, select_autoescape

        _jinja_env = Environment(
            loader=FileSystemLoader(_TEMPLATE_DIR),
            autoescape=select_autoescape(["html", "j2"]),
        )
    return _jinja_env


def render_access_denied(email: str, theme: DeniedTheme) -> str:
    """Render ``templates/access_denied.html.j2``. Autoescaped, so an email or a
    ``support_contact`` containing HTML-significant characters cannot inject markup."""
    template = _template_env().get_template("access_denied.html.j2")
    return template.render(email=email, theme=theme)


def _decode_principal_header(raw: str) -> dict[str, str] | None:
    """Decode the base64 JSON ``X-MS-CLIENT-PRINCIPAL`` blob to a flat claims dict.

    Azure's payload shape is ``{"claims": [{"typ": "...", "val": "..."}], ...}``;
    flattened to ``{typ: val}`` (first occurrence wins) since nothing here needs
    to distinguish repeated claim types. Returns ``None`` on any malformed input
    rather than raising — a bad blob is treated the same as an absent one.
    """
    try:
        padded = raw + "=" * (-len(raw) % 4)
        decoded = base64.b64decode(padded).decode("utf-8")
        blob = json.loads(decoded)
    except Exception:
        logger.warning("auth.claims outcome=malformed_blob")
        return None
    if not isinstance(blob, dict):
        return None
    claims: dict[str, str] = {}
    for item in blob.get("claims", []):
        if not isinstance(item, dict):
            continue
        typ, val = item.get("typ"), item.get("val")
        if typ is not None and val is not None and typ not in claims:
            claims[str(typ)] = str(val)
    return claims


def _publish_fastapi_names() -> None:
    """Import FastAPI's ``Request``/``HTTPException``/``status`` into *this
    module's* globals, lazily.

    Not just a lazy import: ``from __future__ import annotations`` makes every
    annotation in this file a string, and FastAPI resolves a dependency
    callable's parameter types with ``typing.get_type_hints(fn)``, which
    evaluates those strings against ``fn.__globals__`` — the defining module's
    namespace, not the enclosing function's locals. A plain
    ``from fastapi import Request`` inside :meth:`EasyAuth.dependency` would
    only ever be a local variable in that closure, invisible to
    ``get_type_hints``, so FastAPI would fail to recognise ``request: Request``
    and try to bind it as a query parameter instead. Every nested function
    defined in this module shares this same globals dict, so publishing here
    once is enough for all of them.
    """
    if "Request" in globals():
        return
    from fastapi import HTTPException, Request, status

    globals()["Request"] = Request
    globals()["HTTPException"] = HTTPException
    globals()["status"] = status


class EasyAuth:
    """Wires Azure App Service Easy Auth headers to a FastAPI ``Depends``.

    Constructing an instance with ``dev_principal`` set while running on Azure
    App Service (``WEBSITE_SITE_NAME`` present) is a startup failure, not a
    warning — see the module docstring.
    """

    def __init__(
        self,
        allow_list: AllowList,
        *,
        dev_principal: str | None = None,
        login_path: str = "/.auth/login/aad",
        logout_path: str = "/.auth/logout",
        page_paths: Collection[str] = ("/",),
        theme: DeniedTheme | None = None,
        require_claims_header: bool = True,
    ) -> None:
        if dev_principal and os.getenv("WEBSITE_SITE_NAME"):
            raise ConfigError(
                "dev_principal is set but WEBSITE_SITE_NAME indicates Azure App "
                "Service. Refusing to start with the auth bypass enabled in a "
                "hosted environment."
            )
        self.allow_list = allow_list
        self.dev_principal = dev_principal
        self.login_path = login_path
        self.logout_path = logout_path
        self.page_paths = frozenset(page_paths)
        self.theme = theme
        self.require_claims_header = require_claims_header

        self._require_dependency: Callable[..., Principal] | None = None
        self._optional_dependency: Callable[..., Principal | None] | None = None

    def _resolve(self, request: Request) -> Principal | None:
        headers = request.headers
        name = headers.get(HEADER_PRINCIPAL_NAME)
        blob = headers.get(HEADER_PRINCIPAL)

        if not name:
            if self.dev_principal:
                return Principal(email=self.dev_principal.strip().lower(), provider="dev")
            return None

        if self.require_claims_header:
            if not blob:
                logger.warning("auth.resolve outcome=deny reason=missing_claims_header")
                return None
            claims = _decode_principal_header(blob)
            if claims is None:
                logger.warning("auth.resolve outcome=deny reason=malformed_claims_header")
                return None
        else:
            claims = _decode_principal_header(blob) if blob else {}

        return Principal(
            email=name.strip().lower(),
            display_name=claims.get("name"),
            provider=headers.get(HEADER_PRINCIPAL_IDP),
            id=headers.get(HEADER_PRINCIPAL_ID),
            claims=claims,
        )

    def _login_redirect_url(self, request: Request) -> str:
        return f"{self.login_path}?post_login_redirect_uri={quote(request.url.path)}"

    def dependency(self) -> Callable[..., Principal]:
        """The ``require`` dependency, built once and cached.

        Cached rather than rebuilt per call: FastAPI's ``dependency_overrides``
        matches by the exact callable object registered with ``Depends()``, and
        tests need to override the same object the app's routes were wired with.
        """
        if self._require_dependency is not None:
            return self._require_dependency

        _publish_fastapi_names()

        def _require(request: Request) -> Principal:
            principal = self._resolve(request)
            if principal is None:
                if request.url.path in self.page_paths:
                    raise RedirectToLogin(self._login_redirect_url(request))
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Not authenticated.",
                )
            if not self.allow_list.allows(principal.email):
                raise NotAuthorized(principal.email)
            return principal

        self._require_dependency = _require
        return _require

    @property
    def require(self) -> Callable[..., Principal]:
        return self.dependency()

    def optional(self) -> Callable[..., Principal | None]:
        """A dependency that never raises: a real, allow-listed principal, or ``None``."""
        if self._optional_dependency is not None:
            return self._optional_dependency

        _publish_fastapi_names()

        def _optional(request: Request) -> Principal | None:
            principal = self._resolve(request)
            if principal is None or not self.allow_list.allows(principal.email):
                return None
            return principal

        self._optional_dependency = _optional
        return _optional


def install(app: FastAPI, auth: EasyAuth) -> None:
    """Register the ``RedirectToLogin`` / ``NotAuthorized`` exception handlers.

    Call once per app, after constructing ``auth``: ``install(app, auth)``.
    """
    from fastapi.responses import HTMLResponse, RedirectResponse

    @app.exception_handler(RedirectToLogin)
    async def _handle_redirect(request: Request, exc: RedirectToLogin) -> RedirectResponse:
        return RedirectResponse(url=exc.post_login_redirect_url, status_code=302)

    @app.exception_handler(NotAuthorized)
    async def _handle_denied(request: Request, exc: NotAuthorized) -> HTMLResponse:
        logger.info("auth.deny email=%s reason=not_in_allow_list", exc.email)
        default_title = getattr(app, "title", None) or "This application"
        theme = auth.theme or DeniedTheme(app_title=default_title)
        return HTMLResponse(render_access_denied(exc.email, theme), status_code=403)


# ---------------------------------------------------------------------------
# WebSocket tickets
# ---------------------------------------------------------------------------
# HMAC-SHA256 over "scope|email|exp", constant-time compare, no server-side
# store — so a ticket issued by one App Service instance verifies on another,
# and survives a restart. ``ticketed`` keeps sockets in a module-level Python
# list today; with two instances a check-in on one never reaches a browser on
# the other, which is a different bug this does not fix, but a stateless
# ticket at least means auth for the socket itself is instance-independent.


def _ws_ticket_payload(*, scope: str, email: str, exp: int) -> str:
    return f"{scope}|{email}|{exp}"


def issue_ws_ticket(
    principal: Principal, *, secret: str, ttl_s: int = 60, scope: str = "checkin"
) -> str:
    """A short-lived, tamper-evident ticket for a WebSocket upgrade request.

    Browsers cannot set the Easy Auth headers on a WebSocket handshake, so the
    page fetches one of these over a normal (authenticated) HTTP request first
    and passes it as a query parameter on the socket URL instead.
    """
    exp = int(time.time()) + ttl_s
    payload = _ws_ticket_payload(scope=scope, email=principal.email, exp=exp)
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    raw = f"{payload}|{signature}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def verify_ws_ticket(ticket: str, *, secret: str, scope: str = "checkin") -> Principal:
    """Verify a ticket from :func:`issue_ws_ticket`. Raises :class:`WsTicketError`
    on any malformed, tampered, wrong-scope, or expired ticket."""
    try:
        padded = ticket + "=" * (-len(ticket) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode()).decode("utf-8")
    except Exception as exc:
        raise WsTicketError("malformed") from exc

    parts = decoded.split("|")
    if len(parts) != 4:
        raise WsTicketError("malformed")
    ticket_scope, email, exp_raw, signature = parts

    expected_payload = _ws_ticket_payload(scope=ticket_scope, email=email, exp=exp_raw)
    expected_signature = hmac.new(
        secret.encode(), expected_payload.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        raise WsTicketError("signature_mismatch")

    if ticket_scope != scope:
        raise WsTicketError("wrong_scope")

    try:
        exp = int(exp_raw)
    except ValueError as exc:
        raise WsTicketError("malformed") from exc
    if exp < int(time.time()):
        raise WsTicketError("expired")

    return Principal(email=email)


def ws_dependency(auth: EasyAuth, *, secret: str, scope: str) -> Callable[..., Principal]:
    """A FastAPI dependency for a WebSocket route: ``ticket`` as a query param.

    Also re-checks the allow-list at connect time, so revoking an admin's access
    takes effect on their next reconnect without waiting for a 60-second ticket
    to expire on an already-open socket.
    """

    def _dependency(ticket: str) -> Principal:
        from fastapi import WebSocketException, status

        try:
            principal = verify_ws_ticket(ticket, secret=secret, scope=scope)
        except WsTicketError as exc:
            logger.warning("auth.ws_ticket outcome=deny reason=%s", exc.reason)
            raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION) from exc
        if not auth.allow_list.allows(principal.email):
            raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)
        return principal

    return _dependency
