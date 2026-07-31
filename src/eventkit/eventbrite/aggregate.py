"""Aggregating Eventbrite attendees into one payment position per email.

PURE. No I/O, no database, no settings, no clock except an injectable ``now``.

This is a verbatim behavioural extraction of ``ticketed/backend/eventbrite.py:78-160``.
That loop is the most valuable and least testable code in the stack: it decides
who counts as paid, and it ran inside a function that also did HTTP paging,
database upserts, and email notifications, so it had no test coverage at all.

The truth table it implements, stated explicitly because it is not obvious from
the original nested conditionals:

+---------------------+---------------------+-------------------------------------------+
| existing status     | incoming status     | effect                                    |
+=====================+=====================+===========================================+
| (none — first seen) | anything            | record as-is                              |
+---------------------+---------------------+-------------------------------------------+
| not paid            | paid                | **replace** everything, including amounts |
+---------------------+---------------------+-------------------------------------------+
| paid                | paid                | **sum** amounts; latest ``paid_at`` wins  |
|                     |                     | the identity fields                       |
+---------------------+---------------------+-------------------------------------------+
| not paid            | not paid            | **sum** amounts; latest ``paid_at`` wins  |
|                     |                     | the identity fields                       |
+---------------------+---------------------+-------------------------------------------+
| paid                | not paid            | **ignore** entirely                       |
+---------------------+---------------------+-------------------------------------------+

Two consequences worth knowing, both preserved deliberately:

* "Paid beats refunded" means a refunded ticket does not reduce a paid total. A
  purchaser who bought two and refunded one shows the full paid amount. That is
  what the front desk wants (they attended) and what finance does not (the
  refund is invisible here). The reconciliation report, not this function, is
  where that gets reported.
* When a non-paid record is replaced by a paid one, amounts are *replaced* and
  not summed, so any gross accumulated from earlier refunded records is
  discarded rather than added to the paid total.
"""

from __future__ import annotations

import datetime as _dt
import logging
from collections.abc import Iterable, Mapping

from .models import (
    DEFAULT_STATUS_MAP,
    UNKNOWN_STATUS_FALLBACK,
    AggregatedPayment,
    Attendee,
    PaymentStatus,
)

logger = logging.getLogger("eventkit.eventbrite.aggregate")

__all__ = ["aggregate_by_email"]


def aggregate_by_email(
    attendees: Iterable[Attendee],
    *,
    status_map: Mapping[str, PaymentStatus] = DEFAULT_STATUS_MAP,
    now: _dt.datetime | None = None,
) -> dict[str, AggregatedPayment]:
    """Collapse attendee records into one :class:`AggregatedPayment` per email.

    Args:
        attendees: already-parsed records. Anything without an email has been
            dropped by :meth:`Attendee.from_api`.
        status_map: override to suit a vendor whose status vocabulary differs.
        now: the fallback ``paid_at`` for a record with no ``created``
            timestamp. Injectable so tests are deterministic; the predecessor
            used the sync's start time for this.

    Returns:
        ``{email: AggregatedPayment}``. Insertion-ordered by first appearance,
        which keeps sync logs stable across runs for the same input.
    """
    # Naive UTC, matching the stored columns. `utcnow()` is deprecated from 3.12.
    fallback_time = now or _dt.datetime.now(_dt.UTC).replace(tzinfo=None)
    result: dict[str, AggregatedPayment] = {}
    unmapped: set[str] = set()

    for attendee in attendees:
        email = attendee.email.strip().lower()
        if not email:
            continue

        raw = attendee.status_raw.strip().lower()
        status = status_map.get(raw, UNKNOWN_STATUS_FALLBACK)
        if raw and raw not in status_map:
            unmapped.add(attendee.status_raw)

        paid_at = attendee.created or fallback_time
        existing = result.get(email)

        if existing is None:
            result[email] = AggregatedPayment(
                email=email,
                first_name=attendee.first_name,
                last_name=attendee.last_name,
                order_id=attendee.order_id,
                attendee_id=attendee.id,
                status=status,
                paid_at=paid_at,
                gross_cents=attendee.gross_cents,
                net_cents=attendee.net_cents,
                attendee_count=1,
            )
            continue

        existing_is_paid = existing.status == PaymentStatus.PAID
        incoming_is_paid = status == PaymentStatus.PAID

        if incoming_is_paid and not existing_is_paid:
            # A paid record supersedes a non-paid one outright: status, identity
            # and amounts are all replaced. attendee_count resets to 1 because
            # the accumulated amounts it described were just discarded.
            existing.status = PaymentStatus.PAID
            existing.first_name = attendee.first_name
            existing.last_name = attendee.last_name
            existing.order_id = attendee.order_id
            existing.attendee_id = attendee.id
            existing.paid_at = paid_at
            existing.gross_cents = attendee.gross_cents
            existing.net_cents = attendee.net_cents
            existing.attendee_count = 1
        elif incoming_is_paid == existing_is_paid:
            # Same class of record: amounts accumulate, latest wins identity.
            existing.gross_cents += attendee.gross_cents
            existing.net_cents += attendee.net_cents
            existing.attendee_count += 1
            if paid_at > existing.paid_at:
                existing.paid_at = paid_at
                existing.first_name = attendee.first_name
                existing.last_name = attendee.last_name
                existing.order_id = attendee.order_id
                existing.attendee_id = attendee.id
        # else: existing is paid, incoming is not -> ignored entirely, and
        # attendee_count is not incremented, since it counts the records whose
        # amounts are represented in gross_cents.

    if unmapped:
        logger.warning(
            "eventbrite.aggregate unmapped_status=%s treated_as=%s "
            "(add it to status_map if this is wrong)",
            sorted(unmapped),
            UNKNOWN_STATUS_FALLBACK.value,
        )

    return result
