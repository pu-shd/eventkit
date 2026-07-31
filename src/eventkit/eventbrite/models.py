"""Typed shapes for the Eventbrite attendee API.

Parsing lives here rather than inline in the sync loop so that the aggregation
step downstream is a pure function over typed values, which is what makes it
table-testable. The loop it replaces
(``ticketed/backend/eventbrite.py:78-160``) interleaved HTTP paging, JSON
digging, status mapping, aggregation, and database writes in one function, so
none of it could be tested without both a network and a database.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "DEFAULT_STATUS_MAP",
    "AggregatedPayment",
    "Attendee",
    "PaymentStatus",
    "parse_eventbrite_datetime",
]


class PaymentStatus(StrEnum):
    PAID = "paid"
    REFUNDED = "refunded"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


#: Eventbrite ``status`` string (lowercased) -> our status.
#:
#: The live code compares case-sensitively against ``"Attending"``,
#: ``"Checked In"``, ``"Registered"``, ``"Placed"``. Lowercasing the lookup is
#: strictly more permissive, so nothing that mapped to ``paid`` stops doing so.
DEFAULT_STATUS_MAP: Final[Mapping[str, PaymentStatus]] = {
    "attending": PaymentStatus.PAID,
    "checked in": PaymentStatus.PAID,
    "checked_in": PaymentStatus.PAID,
    "registered": PaymentStatus.PAID,
    "placed": PaymentStatus.PAID,
    "completed": PaymentStatus.PAID,
    "cancelled": PaymentStatus.CANCELLED,
    "canceled": PaymentStatus.CANCELLED,
    "deleted": PaymentStatus.CANCELLED,
    "refunded": PaymentStatus.REFUNDED,
    "not attending": PaymentStatus.REFUNDED,
    "not_attending": PaymentStatus.REFUNDED,
    "abandoned": PaymentStatus.REFUNDED,
}

#: What an unrecognised Eventbrite status becomes.
#:
#: This preserves a surprising behaviour of the code being replaced: its status
#: chain ends in a bare ``else: status = "refunded"``, so *any* status Eventbrite
#: invents maps to refunded rather than to something neutral. Changing it to
#: ``UNKNOWN`` would be defensible, but it would silently reclassify existing
#: rows on the next sync and change who appears in the reconciliation report, so
#: the extraction keeps today's answer and logs the unrecognised value instead.
UNKNOWN_STATUS_FALLBACK: Final[PaymentStatus] = PaymentStatus.REFUNDED


def parse_eventbrite_datetime(value: Any) -> _dt.datetime | None:
    """Parse an Eventbrite timestamp to a **naive UTC** datetime.

    Naive on purpose. The predecessor produced naive datetimes via
    ``strptime(s.replace("Z", ""), "%Y-%m-%dT%H:%M:%S")`` and the live database
    columns hold naive values. Returning an aware datetime here would raise
    ``TypeError: can't compare offset-naive and offset-aware datetimes`` inside
    the aggregation's latest-wins comparison — a crash on the sync path the first
    time Eventbrite returned an offset.
    """
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = _dt.datetime.strptime(text.replace("Z", ""), "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(_dt.UTC).replace(tzinfo=None)
    return parsed


def _cents(costs: Any, key: str) -> int:
    """Read ``costs[key]["value"]`` as integer cents, defaulting to 0."""
    if not isinstance(costs, Mapping):
        return 0
    entry = costs.get(key)
    if not isinstance(entry, Mapping):
        return 0
    try:
        return int(entry.get("value") or 0)
    except (TypeError, ValueError):
        return 0


class Attendee(BaseModel):
    """One Eventbrite attendee record, flattened."""

    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    order_id: str | None = None
    email: str
    first_name: str | None = None
    last_name: str | None = None
    #: The raw Eventbrite status, preserved for logging an unmapped value.
    status_raw: str = ""
    gross_cents: int = 0
    net_cents: int = 0
    created: _dt.datetime | None = None
    ticket_class_name: str | None = None

    @classmethod
    def from_api(cls, obj: Mapping[str, Any]) -> Attendee | None:
        """Build from one element of the ``attendees`` array.

        Returns ``None`` when there is no email address, because email is the
        only join key Eventbrite gives us and a record without one cannot be
        reconciled against anything. The predecessor did the same with a bare
        ``continue``.
        """
        if not isinstance(obj, Mapping):
            return None
        profile = obj.get("profile")
        profile = profile if isinstance(profile, Mapping) else {}
        email = str(profile.get("email") or "").strip().lower()
        if not email:
            return None

        costs = obj.get("costs")
        return cls(
            id=_as_str(obj.get("id")),
            order_id=_as_str(obj.get("order_id")),
            email=email,
            first_name=_clean(profile.get("first_name")),
            last_name=_clean(profile.get("last_name")),
            status_raw=str(obj.get("status") or ""),
            gross_cents=_cents(costs, "gross"),
            net_cents=_cents(costs, "net"),
            created=parse_eventbrite_datetime(obj.get("created")),
            ticket_class_name=_clean(obj.get("ticket_class_name")),
        )

    def status(
        self, status_map: Mapping[str, PaymentStatus] = DEFAULT_STATUS_MAP
    ) -> PaymentStatus:
        return status_map.get(self.status_raw.strip().lower(), UNKNOWN_STATUS_FALLBACK)


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class AggregatedPayment(BaseModel):
    """One purchaser's aggregated position, keyed by email."""

    model_config = ConfigDict(extra="forbid")

    email: str
    first_name: str | None = None
    last_name: str | None = None
    order_id: str | None = None
    attendee_id: str | None = None
    status: PaymentStatus
    paid_at: _dt.datetime
    gross_cents: int = 0
    net_cents: int = 0
    #: How many Eventbrite attendee records folded into this row. One purchaser
    #: buying two tickets is ``2``. Not tracked by the predecessor; surfaced here
    #: because ``Payment.email`` carried ``unique=True``, so that exact case was
    #: a 500 — and aggregating was the workaround, not the fix.
    attendee_count: int = Field(default=1, ge=1)

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.first_name, self.last_name) if p).strip()
