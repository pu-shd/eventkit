"""Eventbrite integration: typed models, pure aggregation, HTTP client, sync loop.

``models`` and ``aggregate`` are dependency-free (pydantic only). The HTTP
client (``eventkit.eventbrite.client``, needs ``[http]``) and the sync loop
(``eventkit.eventbrite.sync``, needs ``[db]`` only if its
``SqlAlchemySyncPorts`` is used) are not imported here — only
``ticket-reconciler`` needs them.
"""

from __future__ import annotations

from .aggregate import aggregate_by_email
from .models import (
    DEFAULT_STATUS_MAP,
    UNKNOWN_STATUS_FALLBACK,
    AggregatedPayment,
    Attendee,
    PaymentStatus,
    parse_eventbrite_datetime,
)

__all__ = [
    "DEFAULT_STATUS_MAP",
    "UNKNOWN_STATUS_FALLBACK",
    "AggregatedPayment",
    "Attendee",
    "PaymentStatus",
    "aggregate_by_email",
    "parse_eventbrite_datetime",
]
