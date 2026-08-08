"""HMAC-signed, single-use task tokens for destructive admin operations.

What this replaces, and why each part matters:

* ``ticketed/backend/main.py:1087-1091``'s ``POST /api/admin/clear`` has **no
  auth dependency at all** — anyone who knows the path can POST
  ``{"target": "both", "confirm": "DESTROY"}`` and wipe every registration and
  payment row. It stays reachable only because ``.github/workflows/clear_data.yml``
  needs *some* way to call it with a plain ``curl``.
* The preferred replacement, per the roadmap's ``admin-task.yml``, is to not
  have an HTTP route at all: OIDC-federate into Azure and run the destructive
  operation as a one-shot container command against the app's own CLI. This
  module is the **documented fallback** for wherever that is not yet wired up
  — an HMAC task token that authorizes exactly one path, one body, one
  300-second window, and one use, so the fallback route is not simply
  ``clear_data.yml`` with extra steps.

Three properties this module's threat model needs that
:func:`eventkit.webhook.verify_signature` (a similar HMAC-over-body-plus-
timestamp scheme) does not provide, because a webhook handler guards exactly
one fixed path and tolerates being called more than once:

* The signed payload binds the **path** too, so a token minted for
  ``/api/admin/tasks/clear`` cannot be replayed against
  ``/api/admin/tasks/reset-fixtures``.
* A verified signature is recorded in a **single-use nonce table**
  (:class:`NonceMixin`) before the task runs, so a captured, still-in-window
  request cannot be replayed a second time — a stale-timestamp check alone
  only rejects a replay after the window has closed.
* Every verification attempt, allowed or denied, writes an **audit row**
  (:class:`AuditLogMixin`) — these routes are destructive enough that "who
  ran this and when" cannot depend on app-trace logs still existing by the
  time someone goes looking.

SQLAlchemy is a top-level import here, matching :mod:`eventkit.backup` and
:mod:`eventkit.realtime`'s posture rather than :mod:`eventkit.auth`'s: there
is no way to declare :class:`NonceMixin`/:class:`AuditLogMixin` at all
without it. FastAPI stays lazily imported, confined to
:func:`make_task_router`, so :func:`sign_task_request`/:func:`verify_task_request`
stay usable from a CLI or a CI job with no web dependency.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import SecretStr
from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from .errors import EventKitError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastapi import APIRouter, HTTPException, Request, status

logger = logging.getLogger("eventkit.admin")

__all__ = [
    "DEFAULT_SIGNATURE_HEADER",
    "DEFAULT_TIMESTAMP_HEADER",
    "TASK_TOKEN_TOLERANCE_S",
    "AdminTaskError",
    "AuditLogMixin",
    "AuditOutcome",
    "InvalidTaskToken",
    "NonceMixin",
    "consume_nonce",
    "make_task_router",
    "record_audit",
    "sign_task_request",
    "verify_task_request",
]

DEFAULT_TIMESTAMP_HEADER = "X-Admin-Task-Timestamp"
DEFAULT_SIGNATURE_HEADER = "X-Admin-Task-Signature"

#: Maximum clock skew for a signed task request, in seconds, in either direction.
TASK_TOKEN_TOLERANCE_S = 300


class AdminTaskError(EventKitError):
    """Base class for every error this module raises deliberately."""


class InvalidTaskToken(AdminTaskError):
    """A task token failed to verify: malformed, tampered, expired, or reused."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class AuditOutcome(StrEnum):
    """What a task-token verification, or the task it guarded, resulted in."""

    allow = "allow"
    deny = "deny"


class NonceMixin:
    """Mixin for one app's single-use nonce table.

    An app declares its own model against its own ``Base``, the pattern
    established by :mod:`eventkit.db`'s ``declarative_base()`` and used by
    :mod:`eventkit.backup`'s ``TableSpec.model``/:mod:`eventkit.realtime`'s
    ``ChangeLogMixin``::

        class AdminTaskNonce(NonceMixin, Base):
            __tablename__ = "admin_task_nonce"

    ``nonce`` stores the request's own hex signature rather than a
    separately generated value: the signature already uniquely identifies
    one path + body + timestamp signed with one secret, so recording it
    directly is both sufficient and one fewer thing to get wrong.
    """

    nonce: Mapped[str] = mapped_column(String(64), primary_key=True)
    consumed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditLogMixin:
    """Mixin for one app's append-only destructive-op audit table.

    Written for every verification attempt :func:`make_task_router` handles,
    not only successful ones — a denied attempt (bad signature, stale
    timestamp, a replayed nonce) is exactly the kind of event an operator
    needs a durable record of.
    """

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    path: Mapped[str] = mapped_column(String(255), nullable=False)
    outcome: Mapped[str] = mapped_column(String(8), nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


def _secret_value(secret: SecretStr | str) -> str:
    return secret.get_secret_value() if isinstance(secret, SecretStr) else secret


def _signed_payload(*, path: str, body: bytes, timestamp: str) -> bytes:
    body_hash = hashlib.sha256(body).hexdigest()
    return f"{path}|{body_hash}|{timestamp}".encode()


def sign_task_request(
    path: str, body: bytes, *, secret: SecretStr | str, ts: int | None = None
) -> tuple[str, str]:
    """Sign one destructive-task request. Returns ``(timestamp, signature)``.

    Called by whatever mints the request — today that is an operator's shell
    or the ``admin-task.yml`` fallback path, not an eventkit CLI verb, since
    minting a token is a one-line ``hmac`` call with no state of its own.
    ``path`` must be exactly the request path :func:`verify_task_request`
    (via :func:`make_task_router`) will see, e.g. ``/api/admin/tasks/clear``.
    """
    timestamp = str(int(time.time()) if ts is None else ts)
    payload = _signed_payload(path=path, body=body, timestamp=timestamp)
    signature = hmac.new(_secret_value(secret).encode(), payload, hashlib.sha256).hexdigest()
    return timestamp, signature


def verify_task_request(
    *,
    path: str,
    body: bytes,
    signature: str | None,
    timestamp: str | None,
    secret: SecretStr | str,
    tolerance_s: int = TASK_TOKEN_TOLERANCE_S,
    now: float | None = None,
) -> None:
    """Verify a signed task request. Raises :class:`InvalidTaskToken` on failure.

    Checks the signature and the timestamp window only — replay protection
    needs a database and is :func:`consume_nonce`'s job, called separately
    once the caller has a ``Session``.
    """
    if not signature:
        raise InvalidTaskToken("no_signature")
    if not timestamp:
        raise InvalidTaskToken("no_timestamp")
    try:
        sent_at = int(timestamp)
    except ValueError as exc:
        raise InvalidTaskToken("bad_timestamp") from exc

    current = time.time() if now is None else now
    if abs(current - sent_at) > tolerance_s:
        raise InvalidTaskToken("stale_timestamp")

    expected = hmac.new(
        _secret_value(secret).encode(),
        _signed_payload(path=path, body=body, timestamp=timestamp),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise InvalidTaskToken("signature_mismatch")


def consume_nonce(session: Session, model: type[DeclarativeBase], nonce: str) -> bool:
    """Record ``nonce`` as used. Returns ``False`` if it was already used.

    Call only after :func:`verify_task_request` has already confirmed
    ``nonce`` (the request's own signature) is genuinely a valid HMAC over
    this path, body and timestamp — this function does not itself check
    that, so recording an attacker-supplied string here would be meaningless.
    Commits immediately: replay protection must hold regardless of whether
    the task the token guards goes on to succeed or fail.
    """
    if session.get(model, nonce) is not None:
        return False
    session.add(model(nonce=nonce, consumed_at=datetime.now(UTC)))
    session.commit()
    return True


def record_audit(
    session: Session,
    model: type[DeclarativeBase],
    *,
    path: str,
    outcome: AuditOutcome | str,
    reason: str,
    detail: Mapping[str, Any] | None = None,
) -> None:
    """Append one row to ``model``'s audit log within ``session``.

    Does not commit; matches :func:`eventkit.realtime.record_change`'s
    convention of leaving that to the caller, since :func:`make_task_router`
    commits the audit row together with whatever else the request touched.
    """
    session.add(
        model(
            occurred_at=datetime.now(UTC),
            path=path,
            outcome=AuditOutcome(outcome).value,
            reason=reason,
            detail=dict(detail) if detail is not None else None,
        )
    )


def _publish_fastapi_names() -> None:
    """Publish ``Request``/``HTTPException``/``status`` into this module's
    globals, lazily. Same reason as :func:`eventkit.auth._publish_fastapi_names`:
    ``from __future__ import annotations`` makes every annotation in this file a
    string, and FastAPI resolves a route's parameter types with
    ``typing.get_type_hints(fn)`` against ``fn.__globals__`` — this module's
    namespace, not a name only imported inside :func:`make_task_router`.
    """
    if "Request" in globals():
        return
    from fastapi import HTTPException, Request, status

    globals()["Request"] = Request
    globals()["HTTPException"] = HTTPException
    globals()["status"] = status


def make_task_router(
    tasks: Mapping[str, Callable[[Session], Mapping[str, Any] | None]],
    *,
    db: Callable[..., Session],
    secret: SecretStr | str,
    nonce_model: type[DeclarativeBase],
    audit_model: type[DeclarativeBase],
    prefix: str = "/api/admin",
    timestamp_header: str = DEFAULT_TIMESTAMP_HEADER,
    signature_header: str = DEFAULT_SIGNATURE_HEADER,
    tolerance_s: int = TASK_TOKEN_TOLERANCE_S,
) -> APIRouter:
    """``POST {prefix}/tasks/{name}`` for each ``name`` in ``tasks``.

    ``tasks`` maps a name to a callable that does the actual destructive
    work against the request's ``Session`` and returns a JSON-safe result
    (or ``None``). The route itself only handles what every destructive op
    needs identically: verifying the task token, enforcing single use, and
    writing an audit row — the same shape as
    :mod:`eventkit.backup`'s ``make_backup_router``, which centralises
    restore's gates the same way instead of leaving each app to reinvent them.

    Unlike :mod:`eventkit.auth`-guarded routes, there is deliberately no
    ``principal`` dependency here: the caller is a CI job or an operator's
    shell holding a shared secret, not an Easy-Auth-authenticated human.
    """
    _publish_fastapi_names()
    from fastapi import APIRouter, Depends

    router = APIRouter(prefix=prefix, tags=["admin"])

    def _deny(session: Session, *, path: str, reason: str) -> None:
        record_audit(session, audit_model, path=path, outcome=AuditOutcome.deny, reason=reason)
        session.commit()

    def _make_handler(name: str, task: Callable[[Session], Mapping[str, Any] | None]):
        async def _handler(
            request: Request, session: Session = Depends(db)
        ) -> dict[str, Any]:
            path = request.url.path
            body = await request.body()
            timestamp = request.headers.get(timestamp_header)
            signature = request.headers.get(signature_header)

            try:
                verify_task_request(
                    path=path,
                    body=body,
                    signature=signature,
                    timestamp=timestamp,
                    secret=secret,
                    tolerance_s=tolerance_s,
                )
            except InvalidTaskToken as exc:
                _deny(session, path=path, reason=exc.reason)
                logger.warning("admin.task outcome=deny task=%s reason=%s", name, exc.reason)
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid or missing task token.",
                ) from exc

            assert signature is not None  # verify_task_request already required it
            if not consume_nonce(session, nonce_model, signature):
                _deny(session, path=path, reason="nonce_reused")
                logger.warning("admin.task outcome=deny task=%s reason=nonce_reused", name)
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="This task token has already been used.",
                )

            try:
                result = task(session)
            except Exception as exc:
                session.rollback()
                record_audit(
                    session,
                    audit_model,
                    path=path,
                    outcome=AuditOutcome.deny,
                    reason="task_error",
                    detail={"error": str(exc)},
                )
                session.commit()
                logger.exception("admin.task outcome=deny task=%s reason=task_error", name)
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="The task failed. See the audit log.",
                ) from exc

            record_audit(session, audit_model, path=path, outcome=AuditOutcome.allow, reason="ok")
            session.commit()
            logger.info("admin.task outcome=allow task=%s", name)
            return {"task": name, "result": result}

        _handler.__name__ = f"admin_task_{name}"
        return _handler

    for name, task in tasks.items():
        router.post(f"/tasks/{name}")(_make_handler(name, task))

    return router
