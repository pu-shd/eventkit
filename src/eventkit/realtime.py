"""Polling-first realtime change feed: ``GET /api/changes?since=<cursor>``.

What this replaces, and why each part matters:

* ``ticketed/backend/main.py:713-772`` iterates a module-global
  ``active_checkin_sockets`` list to push check-in updates to open WebSockets.
  On Azure App Service with more than one instance, a check-in handled by
  instance A never reaches a browser connected to instance B — two front-desk
  iPads silently disagree about who is checked in — and every ``send()`` that
  raises (a client that closed without a clean handshake) is swallowed, so a
  dead socket stays in the list until the process restarts.
* Polling instead makes correctness independent of which instance a browser
  happens to be talking to: every instance answers ``GET /api/changes`` from
  the same database, so there is no cross-instance fan-out to get wrong, no
  sticky-session requirement, and a captive-portal wifi that kills a long-lived
  socket just means the next poll picks up where the last one left off.

:class:`ChangeLogMixin` gives an app an append-only, strictly-increasing
``id`` column to record changes against; that id *is* the cursor a client
passes back as ``since``. :func:`poll_changes` is the pure, DB-only half of
the feed; :func:`make_changes_router` wraps it as the one HTTP route every app
needs. :class:`ChangeBroadcaster` and :func:`make_changes_ws_route` are the
opt-in WebSocket push on top — instance-local by design, since polling is
what has to be correct across N instances, so a push that only reaches
sockets on the same instance is a latency optimization, not a source of
truth. Unlike the code above, a subscriber whose queue is full is dropped
without touching any other subscriber, and a send failure on one socket
never stops the broadcast loop for the rest.

SQLAlchemy is a top-level import here, matching :mod:`eventkit.backup`'s
posture rather than :mod:`eventkit.auth`'s: there is no way to declare
:class:`ChangeLogMixin` at all without it, so there is nothing gained by
deferring it. FastAPI stays lazily imported, confined to the two ``make_*``
functions, so this module does not force the ``web`` extra on a caller that
only wants :func:`record_change`/:func:`poll_changes` from a background job.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel
from sqlalchemy import JSON, DateTime, Integer, String, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastapi import APIRouter, WebSocket

    from .auth import Principal

logger = logging.getLogger("eventkit.realtime")

__all__ = [
    "POLL_INTERVAL_BLURRED_S",
    "POLL_INTERVAL_FOCUSED_S",
    "ChangeBroadcaster",
    "ChangeEntry",
    "ChangeLogMixin",
    "ChangeOp",
    "ChangesPage",
    "make_changes_router",
    "make_changes_ws_route",
    "poll_changes",
    "record_change",
]

#: Recommended client poll interval while the tab holding the change feed is
#: focused. From the phase doc: "three iPads polling every 3s is nothing."
POLL_INTERVAL_FOCUSED_S = 3

#: Recommended client poll interval once that tab is blurred/backgrounded.
POLL_INTERVAL_BLURRED_S = 30


class ChangeOp(StrEnum):
    """What happened to the entity, at the granularity a poller needs."""

    created = "created"
    updated = "updated"
    deleted = "deleted"


class ChangeLogMixin:
    """Mixin for one app's append-only change-log table.

    An app declares its own model against its own ``Base`` (the pattern
    established by :mod:`eventkit.db`'s ``declarative_base()`` and used by
    :mod:`eventkit.backup`'s ``TableSpec.model``)::

        class ChangeLog(ChangeLogMixin, Base):
            __tablename__ = "change_log"

    ``id`` is the cursor: a database-assigned, strictly-increasing integer
    that survives process restarts, unlike an in-memory counter, and is the
    only thing :func:`poll_changes` compares ``since`` against. Rows are
    never updated or deleted once written — the log itself has no need for
    :attr:`ChangeOp.updated`/:attr:`ChangeOp.deleted`; those describe what
    happened to the *entity* being logged, not to the log row.
    """

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    op: Mapped[str] = mapped_column(String(16), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class ChangeEntry(BaseModel):
    """One row of the change log, as sent to a client."""

    cursor: int
    entity: str
    entity_id: str
    op: ChangeOp
    occurred_at: datetime
    payload: dict[str, Any] | None = None

    @classmethod
    def from_row(cls, row: Any) -> ChangeEntry:
        return cls(
            cursor=row.id,
            entity=row.entity,
            entity_id=row.entity_id,
            op=ChangeOp(row.op),
            occurred_at=row.occurred_at,
            payload=row.payload,
        )


class ChangesPage(BaseModel):
    """A page of the change feed, plus the cursor to poll again with.

    ``cursor`` is the id of the last row *in this page*, not the log's true
    current maximum — a client that keeps polling with each response's
    ``cursor`` will drain a large backlog page by page rather than skipping
    straight to the end and losing everything ``limit`` cut off. When
    ``changes`` is empty, ``cursor`` echoes the caller's ``since`` unchanged.
    """

    cursor: int
    changes: list[ChangeEntry]


def record_change(
    session: Session,
    model: type[DeclarativeBase],
    *,
    entity: str,
    entity_id: str | int,
    op: ChangeOp | str,
    payload: Mapping[str, Any] | None = None,
) -> None:
    """Append one row to ``model``'s change log within ``session``.

    Call this in the same transaction as the write it describes — the change
    log is only a correct source of truth for pollers if it commits atomically
    with the change itself, not after. Does not commit; that is ``session``'s
    caller's job, same as every other write in the same request.
    """
    session.add(
        model(
            entity=entity,
            entity_id=str(entity_id),
            op=ChangeOp(op).value,
            occurred_at=datetime.now(UTC),
            payload=dict(payload) if payload is not None else None,
        )
    )


def poll_changes(
    session: Session,
    model: type[DeclarativeBase],
    *,
    since: int,
    limit: int = 200,
) -> ChangesPage:
    """Every change with ``id > since``, oldest first, capped at ``limit``.

    Pure database read — no FastAPI involved — so it is usable from a
    background task or a CLI as well as from :func:`make_changes_router`.
    """
    if since < 0:
        raise ValueError("since must be >= 0")
    if limit < 1:
        raise ValueError("limit must be >= 1")

    rows = list(
        session.execute(
            select(model).where(model.id > since).order_by(model.id).limit(limit)
        ).scalars()
    )
    cursor = rows[-1].id if rows else since
    return ChangesPage(cursor=cursor, changes=[ChangeEntry.from_row(row) for row in rows])


def _publish_fastapi_names() -> None:
    """Publish ``WebSocket``/``Principal`` into this module's globals, lazily.

    Same reason as :func:`eventkit.auth._publish_fastapi_names`: ``from
    __future__ import annotations`` makes every annotation in this file a
    string, and FastAPI resolves a route/dependency callable's parameter
    types with ``typing.get_type_hints(fn)``, which evaluates those strings
    against ``fn.__globals__`` — this module's namespace, not a locally
    imported name inside :func:`make_changes_router` or
    :func:`make_changes_ws_route`. ``Session`` needs no such treatment: it is
    a top-level import in this module already (see the module docstring).
    """
    if "WebSocket" in globals():
        return
    from fastapi import WebSocket

    from .auth import Principal

    globals()["WebSocket"] = WebSocket
    globals()["Principal"] = Principal


def make_changes_router(
    model: type[DeclarativeBase],
    *,
    db: Callable[..., Session],
    principal: Callable[..., Principal],
    prefix: str = "/api",
    default_limit: int = 200,
    max_limit: int = 1000,
) -> APIRouter:
    """``GET {prefix}/changes?since=<cursor>&limit=<n>`` — the default feed.

    Requires ``principal`` on every call, same as every other authenticated
    route: the change feed can carry entity ids and payload fields that are
    not meant to be public, so this is not a candidate for an anonymous
    ``optional()`` dependency.
    """
    _publish_fastapi_names()
    from fastapi import APIRouter, Depends, Query

    router = APIRouter(prefix=prefix, tags=["realtime"])

    @router.get("/changes")
    def get_changes(
        since: int = Query(0, ge=0),
        limit: int = Query(default_limit, ge=1, le=max_limit),
        session: Session = Depends(db),
        principal_: Principal = Depends(principal),
    ) -> ChangesPage:
        return poll_changes(session, model, since=since, limit=limit)

    return router


class ChangeBroadcaster:
    """In-process fan-out of new changes to connected WebSocket clients.

    Deliberately instance-local, not the source of truth — see the module
    docstring. Each subscriber gets its own bounded queue; :meth:`publish`
    drops a subscriber whose queue is full rather than blocking or raising,
    so one slow client cannot stall delivery to every other client the way
    an unbounded fan-out or a synchronous send loop could.
    """

    def __init__(self, *, max_queue: int = 32) -> None:
        self._subscribers: set[asyncio.Queue[ChangeEntry]] = set()
        self._max_queue = max_queue

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[ChangeEntry]]:
        """Register a new subscriber queue for the lifetime of the ``with`` block."""
        queue: asyncio.Queue[ChangeEntry] = asyncio.Queue(maxsize=self._max_queue)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    def publish(self, entry: ChangeEntry) -> None:
        """Fan ``entry`` out to every current subscriber.

        A full queue means that subscriber is not draining fast enough; it is
        dropped (removed from the fan-out set) rather than awaited or
        blocked on, so it cannot slow down or fail delivery to anyone else.
        The dropped subscriber's own poll fallback (or a fresh WS reconnect)
        is what catches it back up, not this method.
        """
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(entry)
            except asyncio.QueueFull:
                self._subscribers.discard(queue)
                logger.warning(
                    "realtime.publish outcome=drop_subscriber reason=queue_full entity=%s",
                    entry.entity,
                )


def make_changes_ws_route(
    broadcaster: ChangeBroadcaster,
    *,
    ws_dependency: Callable[..., Principal],
) -> Callable[..., Any]:
    """Build a WebSocket route function pushing :class:`ChangeEntry` payloads.

    Register it on an app with ``app.websocket(path)(make_changes_ws_route(...))``.
    ``ws_dependency`` is expected to be :func:`eventkit.auth.ws_dependency` —
    a short-lived, tamper-evident ticket, since browsers cannot set Easy Auth
    headers on a WebSocket handshake. This route is opt-in on top of the
    polling feed, never a replacement for it: a client that never opens this
    socket at all is still fully served by ``GET {prefix}/changes``.

    A send failure to one socket (client gone without a clean close) ends
    that one connection's loop and is logged; it does not touch the
    broadcaster's other subscribers or the app's other connections, which is
    the exact bug being replaced (see the module docstring).
    """
    _publish_fastapi_names()
    from fastapi import Depends, WebSocketDisconnect

    async def changes_ws(
        websocket: WebSocket, principal_: Principal = Depends(ws_dependency)
    ) -> None:
        await websocket.accept()
        async with broadcaster.subscribe() as queue:
            try:
                while True:
                    entry = await queue.get()
                    await websocket.send_json(entry.model_dump(mode="json"))
            except WebSocketDisconnect:
                logger.info("realtime.ws outcome=disconnect email=%s", principal_.email)
            except Exception:
                logger.warning(
                    "realtime.ws outcome=send_error email=%s", principal_.email, exc_info=True
                )

    return changes_ws
