"""Eventbrite integration: typed models, pure aggregation, HTTP client.

``models`` and ``aggregate`` are dependency-free (pydantic only). The HTTP client
lives in ``eventkit.eventbrite.client`` and needs the ``[http]`` extra, so it is
not imported here — only ``ticket-reconciler`` needs it.
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
