"""The sync loop: fetch, aggregate, write, notify — with no notification or
database code baked into it.

Replaces ``ticketed/backend/eventbrite.py``'s ``run_eventbrite_sync``
(lines 62-253), which interleaves HTTP paging, aggregation, SQLAlchemy writes
and direct calls to ``send_reconciliation_alert`` in one ~190-line function.
Splitting those apart is what ``PLAN.md`` §B.7 calls "the port boundary":
:func:`run_sync` here knows how to fetch and aggregate and *when* to notify,
but writes and notifications go through the :class:`SyncPorts` protocol, so a
test drives it with a fake ``ports`` and a mocked HTTP client — zero database,
zero network — while a real app plugs in :class:`SqlAlchemySyncPorts`.

**Events this module actually fires, and why the ones in ``PLAN.md``'s
``SyncEvent`` sketch that are missing are missing** (logged as DEC-005 in
``decisions.md``):

* ``UNMATCHED_PAYMENT`` and ``COMPLETED_PAYMENT`` are exactly the two cases
  the predecessor fires, from the same two conditions (new paid attendee with
  no matching registrant; new paid attendee whose registrant chose
  ``tickets_sold_separately``).
* ``SYNC_FAILED`` is new — this is the sync loop's own fix for the predecessor
  never alerting anyone when a sync attempt fails, which is why
  ``eventkit.notify`` shipped a ``sync_failed`` template.
* ``PENDING_PAYMENT`` and ``EXEMPT_REGISTRATION`` are *not* fired here. They
  fire when a registrant record is first ingested
  (``ticketed/backend/main.py:367-402``, gated by whether
  ``tickets_sold_separately`` is set), not when Eventbrite attendee data is
  synced — a registrant is pending or exempt the moment they submit the
  webform, independent of anything Eventbrite has seen yet.
  :meth:`eventkit.eventprofile.models.Ticketing.is_exempt` already implements
  that check; wiring it up is the webhook/importer module's job.
* ``STATUS_CHANGED`` is in the sketch but ships no ``eventkit.notify``
  template (unlike the other four sketch members, which each have one), and
  the predecessor never alerted on it either. Inventing both the trigger
  condition and the template for a notification nothing has ever sent felt
  like speculative scope for this task rather than a real requirement.
"""

from __future__ import annotations

import datetime as _dt
import logging
from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from .aggregate import aggregate_by_email
from .models import DEFAULT_STATUS_MAP, AggregatedPayment, PaymentStatus

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.orm import Session

    from ..notify import Notifier
    from .client import EventbriteClient

logger = logging.getLogger("eventkit.eventbrite.sync")

__all__ = [
    "SqlAlchemySyncPorts",
    "SyncEvent",
    "SyncPorts",
    "SyncResult",
    "run_sync",
]


class SyncEvent(StrEnum):
    """Event names :func:`run_sync` passes to ``ports.emit`` — see the module
    docstring for why this is not the full set in ``PLAN.md``'s sketch."""

    UNMATCHED_PAYMENT = "unmatched_payment"
    COMPLETED_PAYMENT = "completed_payment"
    SYNC_FAILED = "sync_failed"


@runtime_checkable
class SyncPorts(Protocol):
    """Everything :func:`run_sync` needs from an app, and nothing it does for
    itself. ``load_existing_payments``/``load_registrant_index`` return
    ``{email: row}`` maps of whatever the app's ORM rows are — ``run_sync``
    only reads ``registrant.tickets_sold_separately`` off a registrant row (a
    ``getattr`` with a ``False`` default, so a row without that attribute is
    just never treated as exempt-from-unmatched) and never inspects a payment
    row directly.
    """

    def load_existing_payments(self) -> Mapping[str, Any]: ...

    def load_registrant_index(self) -> Mapping[str, Any]: ...

    def upsert_payment(self, agg: AggregatedPayment) -> tuple[Any, bool]:
        """Write ``agg``, returning ``(row, created)``."""

    def record_sync(self, result: SyncResult) -> None: ...

    async def emit(self, event: SyncEvent, ctx: Mapping[str, Any]) -> None: ...


class SyncResult(BaseModel):
    """What one call to :func:`run_sync` did, for logging and ``SyncLog`` rows alike."""

    model_config = ConfigDict(extra="forbid")

    status: str  # "success" | "failed"
    started_at: _dt.datetime
    finished_at: _dt.datetime
    records_pulled: int = 0
    payments_created: int = 0
    payments_updated: int = 0
    error: str | None = None


def _now() -> _dt.datetime:
    # Naive UTC, matching aggregate.py's fallback_time and the stored columns.
    return _dt.datetime.now(_dt.UTC).replace(tzinfo=None)


async def run_sync(
    client: EventbriteClient,
    ports: SyncPorts,
    *,
    status_map: Mapping[str, PaymentStatus] = DEFAULT_STATUS_MAP,
    now: _dt.datetime | None = None,
) -> SyncResult:
    """Fetch, aggregate, write, notify. Never raises — a failed sync becomes a
    ``SyncResult(status="failed")`` plus a ``SYNC_FAILED`` notification, the
    same "degrade, don't crash the caller" posture ``eventkit.notify`` already
    established, because this is meant to run unattended on a schedule.
    """
    started_at = now or _now()
    records_pulled = 0

    try:
        attendees = await client.fetch_attendees()
        records_pulled = len(attendees)

        aggregated = aggregate_by_email(attendees, status_map=status_map, now=started_at)
        existing_payments = ports.load_existing_payments()
        registrant_index = ports.load_registrant_index()

        created = 0
        updated = 0
        for email, agg in aggregated.items():
            is_new = email not in existing_payments

            _row, was_created = ports.upsert_payment(agg)
            if was_created:
                created += 1
            else:
                updated += 1

            if not (is_new and agg.status == PaymentStatus.PAID):
                continue

            registrant = registrant_index.get(email)
            if registrant is None:
                await ports.emit(
                    SyncEvent.UNMATCHED_PAYMENT,
                    {"email": agg.email, "full_name": agg.full_name, "order_id": agg.order_id},
                )
            elif getattr(registrant, "tickets_sold_separately", False):
                await ports.emit(
                    SyncEvent.COMPLETED_PAYMENT,
                    {
                        "email": agg.email,
                        "full_name": agg.full_name,
                        "order_id": agg.order_id,
                        "serial": getattr(registrant, "serial", None),
                        "sid": getattr(registrant, "sid", None),
                    },
                )

        result = SyncResult(
            status="success",
            started_at=started_at,
            finished_at=_now(),
            records_pulled=records_pulled,
            payments_created=created,
            payments_updated=updated,
        )
        ports.record_sync(result)
        return result

    except Exception as exc:
        logger.exception(
            "eventbrite.sync outcome=failed event_id=%s records_pulled=%d",
            client.event_id,
            records_pulled,
        )
        result = SyncResult(
            status="failed",
            started_at=started_at,
            finished_at=_now(),
            records_pulled=records_pulled,
            error=str(exc),
        )
        ports.record_sync(result)
        await ports.emit(
            SyncEvent.SYNC_FAILED,
            {
                "event_slug": client.event_id,
                "reason": str(exc),
                "attempted_at": started_at.isoformat(),
            },
        )
        return result


#: Maps an :class:`AggregatedPayment` field name to the app's ``Payment``
#: model's column name. Overridable per app in :class:`SqlAlchemySyncPorts`'s
#: ``column_map`` — the predecessor's ``Payment`` model, for instance, calls
#: these ``gross_amount``/``net_amount`` rather than ``gross_cents``/``net_cents``.
_DEFAULT_COLUMN_MAP: Mapping[str, str] = {
    "email": "email",
    "first_name": "first_name",
    "last_name": "last_name",
    "order_id": "order_id",
    "attendee_id": "attendee_id",
    "status": "status",
    "paid_at": "paid_at",
    "gross_cents": "gross_cents",
    "net_cents": "net_cents",
}


class SqlAlchemySyncPorts:
    """The batteries-included :class:`SyncPorts` impl: takes the app's
    ``Payment``/``Registrant``/``SyncLog`` model classes (plus column-name
    overrides where an app's columns are not named like
    :class:`AggregatedPayment`'s fields) instead of asking every app to
    hand-write the same query-and-upsert boilerplate.

    ``sync_log_model`` and ``notifier`` are both optional: an app that has not
    built a ``SyncLog`` table yet, or has not wired up ``eventkit.notify`` yet,
    still gets a working ``SyncPorts`` — ``record_sync``/``emit`` become no-ops
    rather than forcing both to exist before the other can be tried.
    """

    def __init__(
        self,
        session: Session,
        *,
        payment_model: type[Any],
        registrant_model: type[Any],
        sync_log_model: type[Any] | None = None,
        registrant_email_column: str = "email",
        column_map: Mapping[str, str] | None = None,
        notifier: Notifier | None = None,
        sync_log_factory: Callable[[SyncResult], Any] | None = None,
    ) -> None:
        self._session = session
        self._payment_model = payment_model
        self._registrant_model = registrant_model
        self._sync_log_model = sync_log_model
        self._registrant_email_column = registrant_email_column
        self._column_map = {**_DEFAULT_COLUMN_MAP, **(column_map or {})}
        self._notifier = notifier
        self._sync_log_factory = sync_log_factory

    def load_existing_payments(self) -> Mapping[str, Any]:
        from sqlalchemy import select

        rows = self._session.execute(select(self._payment_model)).scalars().all()
        return {getattr(row, self._column_map["email"]): row for row in rows}

    def load_registrant_index(self) -> Mapping[str, Any]:
        from sqlalchemy import select

        rows = self._session.execute(select(self._registrant_model)).scalars().all()
        return {getattr(row, self._registrant_email_column): row for row in rows}

    def upsert_payment(self, agg: AggregatedPayment) -> tuple[Any, bool]:
        from sqlalchemy import select

        cm = self._column_map
        email_column = getattr(self._payment_model, cm["email"])
        existing = self._session.execute(
            select(self._payment_model).where(email_column == agg.email)
        ).scalar_one_or_none()

        created = existing is None
        row = existing if existing is not None else self._payment_model()

        setattr(row, cm["email"], agg.email)
        setattr(row, cm["first_name"], agg.first_name)
        setattr(row, cm["last_name"], agg.last_name)
        setattr(row, cm["order_id"], agg.order_id)
        setattr(row, cm["attendee_id"], agg.attendee_id)
        setattr(row, cm["status"], agg.status.value)
        setattr(row, cm["paid_at"], agg.paid_at)
        setattr(row, cm["gross_cents"], agg.gross_cents)
        setattr(row, cm["net_cents"], agg.net_cents)

        if created:
            self._session.add(row)
        self._session.flush()
        return row, created

    def record_sync(self, result: SyncResult) -> None:
        if result.status == "failed":
            # Discard any adds/flushes this attempt made before failing (mirrors
            # the predecessor's db.rollback() in its except branch) so a failed
            # sync never leaves a partial write committed alongside its own
            # failure record.
            self._session.rollback()

        if self._sync_log_model is not None:
            log_row = (
                self._sync_log_factory(result)
                if self._sync_log_factory is not None
                else self._sync_log_model(
                    synced_at=result.started_at,
                    records_pulled=result.records_pulled,
                    status=result.status,
                    error_message=result.error,
                )
            )
            self._session.add(log_row)

        # Committed unconditionally, sync_log_model or not — otherwise a
        # successful sync's flushed-but-uncommitted upsert_payment() writes
        # would be silently rolled back by the session's eventual close().
        self._session.commit()

    async def emit(self, event: SyncEvent, ctx: Mapping[str, Any]) -> None:
        if self._notifier is None:
            return
        await self._notifier.notify(event, ctx)
