"""Verifying Drupal Remote Post webhook calls.

What this replaces, and why each part matters:

* ``ticketed/backend/main.py:289`` and ``posted``'s equivalents compare the
  token with ``!=``, which is timing-variable. Replaced with
  :func:`hmac.compare_digest`.
* ``ticketed/backend/main.py:285-286`` logs ``dict(request.headers)`` **and** both
  the received and the expected token at ``INFO``, on every webhook call. Those
  values are in App Service logs and Log Analytics, readable by anyone with
  Reader on the resource group. Replaced with a single structured line carrying
  an outcome, a reason, and a six-hex-character fingerprint.
* ``ticketed/config.py:22`` and ``posted/config.py:23-24`` default the token to
  ``"secret_drupal_token"`` / ``"secret_nametags_token"``. An adopter who forgets
  the app setting deploys a publicly documented token. :func:`assert_strong`
  makes that a startup failure.

Roadmap, stated in the module so it does not get lost: a shared-secret header
with no signature means any Drupal admin, any log containing headers, and any
misconfigured proxy has full write access to the roster — including the ability
to overwrite a paid registrant's email address. :func:`verify_signature` adds
HMAC-over-body with a timestamp; the bare token is accepted for one release with
a deprecation warning, then removed.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from pydantic import SecretStr

from .errors import ConfigError

logger = logging.getLogger("eventkit.webhook")

__all__ = [
    "DEFAULT_HEADER",
    "MIN_TOKEN_LENGTH",
    "WEAK_TOKENS",
    "WebhookTokens",
    "assert_strong",
    "deferred",
    "fingerprint",
    "generate_token",
    "verify_signature",
    "verify_token",
]

DEFAULT_HEADER = "X-Drupal-Webhook-Token"
DEFAULT_SIGNATURE_HEADER = "X-Drupal-Signature"
DEFAULT_TIMESTAMP_HEADER = "X-Drupal-Timestamp"

MIN_TOKEN_LENGTH = 24
#: Maximum clock skew for a signed request, in seconds.
SIGNATURE_TOLERANCE_S = 300

#: Tokens that must never reach production. The first two are the actual
#: committed defaults in the two live repositories, so they are effectively
#: public knowledge and are checked for by name.
WEAK_TOKENS: frozenset[str] = frozenset(
    {
        "secret_drupal_token",
        "secret_nametags_token",
        "changeme",
        "change_me",
        "test",
        "token",
        "secret",
        "password",
        "placeholder",
        "example",
        "todo",
    }
)


def fingerprint(value: str | None) -> str:
    """Six hex characters of ``sha256(value)``, for correlating without leaking.

    Enough to tell "Drupal is sending the old token" from "Drupal is sending
    nothing", which is the only question the logs need to answer. Not enough to
    mount an offline search against a 32-byte random token.
    """
    if not value:
        return "absent"
    return hashlib.sha256(value.encode()).hexdigest()[:6]


def generate_token(n_bytes: int = 32) -> str:
    """A fresh token. Equivalent to ``openssl rand -hex 32``."""
    import secrets

    return secrets.token_hex(n_bytes)


def assert_strong(
    token: SecretStr | str | None, *, name: str, min_len: int = MIN_TOKEN_LENGTH
) -> None:
    """Raise :class:`ConfigError` unless ``token`` is fit for production.

    Called at startup, including on Azure — especially on Azure. Refusing to
    boot is the only check that cannot be ignored; a warning in a log nobody
    reads is how ``"secret_drupal_token"`` reached a public deployment.
    """
    raw = token.get_secret_value() if isinstance(token, SecretStr) else token
    if not raw or not raw.strip():
        raise ConfigError(
            f"{name} is not set. Generate one with `openssl rand -hex 32` and set "
            f"it as an app setting. There is no default on purpose."
        )
    value = raw.strip()
    if value.lower() in WEAK_TOKENS:
        raise ConfigError(
            f"{name} is set to the well-known placeholder {value!r}, which is "
            f"committed in a public repository. Rotate it now: "
            f"`openssl rand -hex 32`."
        )
    if len(value) < min_len:
        raise ConfigError(
            f"{name} is {len(value)} characters; the minimum is {min_len}. "
            f"Generate one with `openssl rand -hex 32`."
        )
    if len(set(value)) < 5:
        raise ConfigError(
            f"{name} has too little variety to be random ({len(set(value))} "
            f"distinct characters). Generate one with `openssl rand -hex 32`."
        )


def verify_token(presented: str | None, expected: SecretStr | str) -> bool:
    """Constant-time comparison of a presented token against the expected one."""
    raw = expected.get_secret_value() if isinstance(expected, SecretStr) else expected
    if not presented or not raw:
        return False
    return hmac.compare_digest(presented.encode(), raw.encode())


def verify_signature(
    *,
    body: bytes,
    signature: str | None,
    timestamp: str | None,
    secret: SecretStr | str,
    tolerance_s: int = SIGNATURE_TOLERANCE_S,
    now: float | None = None,
) -> tuple[bool, str]:
    """Verify ``HMAC-SHA256(secret, timestamp + "." + body)``.

    Returns ``(ok, reason)`` so the caller can log the reason without branching.
    A timestamp outside ``tolerance_s`` fails even with a valid signature, so a
    captured request cannot be replayed indefinitely.
    """
    if not signature:
        return False, "no_signature"
    if not timestamp:
        return False, "no_timestamp"
    try:
        sent_at = float(timestamp)
    except ValueError:
        return False, "bad_timestamp"

    current = time.time() if now is None else now
    if abs(current - sent_at) > tolerance_s:
        return False, "stale_timestamp"

    raw = secret.get_secret_value() if isinstance(secret, SecretStr) else secret
    expected = hmac.new(
        raw.encode(), timestamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    presented = signature.removeprefix("sha256=").strip()
    if not hmac.compare_digest(presented, expected):
        return False, "signature_mismatch"
    return True, "ok"


class WebhookTokens:
    """A named set of webhook tokens, one per Remote Post handler.

    Each application registers its own handler with its own token, so a token
    leaked from one app cannot write to another's database. Naming them here
    keeps the log line useful when an app has more than one handler (``posted``
    has two today: registration and nametags).
    """

    def __init__(
        self,
        tokens: Mapping[str, SecretStr | str],
        *,
        header: str = DEFAULT_HEADER,
        require_signature: bool = False,
    ) -> None:
        self.header = header
        self.require_signature = require_signature
        self._tokens: dict[str, SecretStr] = {
            name: value if isinstance(value, SecretStr) else SecretStr(value)
            for name, value in tokens.items()
        }

    @classmethod
    def from_settings(cls, **named: SecretStr | str) -> WebhookTokens:
        return cls(named)

    def names(self) -> Iterable[str]:
        return self._tokens.keys()

    def assert_all_strong(self, *, min_len: int = MIN_TOKEN_LENGTH) -> None:
        """Validate every configured token. Call from application startup."""
        for name, token in self._tokens.items():
            assert_strong(token, name=f"{name} webhook token", min_len=min_len)

    def check(self, name: str, presented: str | None) -> bool:
        """Verify and log. Returns whether the call is authentic."""
        expected = self._tokens.get(name)
        if expected is None:
            raise KeyError(f"no webhook token registered under {name!r}")
        ok = verify_token(presented, expected)
        logger.info(
            "webhook.verify name=%s outcome=%s reason=%s fp=%s",
            name,
            "allow" if ok else "deny",
            "ok" if ok else ("absent" if not presented else "mismatch"),
            fingerprint(presented),
        )
        return ok

    def dependency(self, name: str) -> Callable[..., str]:
        """A FastAPI dependency verifying the header for handler ``name``.

        Used as ``dependencies=[Depends(tokens.dependency("registration"))]`` on
        the route, so it cannot be forgotten. The pattern being replaced is an
        imperative ``if`` at the top of each handler — ``posted`` has 18 such
        call sites for admin auth, and a new handler that omits the line is
        silently public.

        FastAPI is imported lazily so this module stays importable by
        ``link-forge``, which has no web dependency on the ingest path.
        """
        from fastapi import Header, HTTPException, status

        header_name = self.header

        def _verify(
            presented: str | None = Header(default=None, alias=header_name),
        ) -> str:
            if not self.check(name, presented):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Invalid or missing webhook authentication token.",
                )
            return name

        _verify.__name__ = f"verify_{name}_webhook_token"
        return _verify


def deferred(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap slow post-ingest work so it can never fail the webhook response.

    Five Remote Post handlers on one webform is synchronous coupling: Drupal
    blocks on each one. Every webhook must return 200 in roughly 200 ms and defer
    anything slow — a notification send, an Eventbrite call — to after the
    response.

    This decorator swallows and logs exceptions rather than propagating, because
    a background task that raises after the response has been sent produces an
    unhandled-error log and, worse, can mark the request failed in some ASGI
    servers. The registration is already committed at that point; losing the
    notification is the correct trade.
    """
    import functools
    import inspect

    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def _async_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await fn(*args, **kwargs)
            except Exception:
                logger.exception("deferred task %s failed after response", fn.__name__)
                return None

        return _async_wrapper

    @functools.wraps(fn)
    def _wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except Exception:
            logger.exception("deferred task %s failed after response", fn.__name__)
            return None

    return _wrapper
