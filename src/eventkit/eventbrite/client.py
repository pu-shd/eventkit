"""Async HTTP client for the Eventbrite v3 attendees API, plus its test double.

Replaces ``ticketed/backend/eventbrite.py``'s ``EventbriteClient`` (lines
15-58): that version reads its token and event id from module-level
``Settings`` and builds a fresh ``httpx.AsyncClient()`` per call with no way to
inject a transport, so the pagination loop — the only interesting behaviour in
the class — had never once run under a test. Here the credentials are
constructor arguments and the transport is injectable, which is what lets
:class:`EventbriteMock` drive it with ``respx`` instead of a live account.

Deliberately thin: this module does attendee paging only. Aggregating those
attendees into payments is :func:`eventkit.eventbrite.aggregate.aggregate_by_email`
(pure, already built); building a purchase link is
:meth:`eventkit.eventprofile.models.Ticketing.purchase_url` (profile-driven,
already built) — duplicating either one here, as ``PLAN.md``'s sketch
``purchase_url`` would, is scope this module does not need and a second place
for the URL template to drift from the profile's.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import httpx

from ..errors import EventKitError
from .models import Attendee

if TYPE_CHECKING:  # pragma: no cover - typing only
    from respx import MockRouter

logger = logging.getLogger("eventkit.eventbrite.client")

__all__ = ["DEFAULT_BASE_URL", "EventbriteClient", "EventbriteClientError", "EventbriteMock"]

DEFAULT_BASE_URL = "https://www.eventbriteapi.com/v3"


class EventbriteClientError(EventKitError):
    """The Eventbrite API returned something the client cannot recover from."""


class EventbriteClient:
    """Pages through one event's attendees.

    ``transport`` is the ``httpx`` seam ``respx`` needs to intercept requests
    without a real socket — see :class:`EventbriteMock` and the
    ``eventbrite_mock`` pytest fixture built on it.
    """

    def __init__(
        self,
        token: str,
        event_id: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
        max_pages: int = 200,
    ) -> None:
        if not token or not event_id:
            raise EventbriteClientError(
                "EventbriteClient requires both a token and an event_id "
                f"(got token={'set' if token else 'empty'}, event_id={event_id!r})."
            )
        self._token = token
        self._event_id = event_id
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._transport = transport
        self._max_pages = max_pages

    @property
    def event_id(self) -> str:
        return self._event_id

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=self._timeout,
            transport=self._transport,
        )

    async def iter_attendees(self) -> AsyncIterator[Attendee]:
        """Yield every attendee with an email address, across all pages.

        Records with no email are dropped by :meth:`Attendee.from_api` itself
        (see its docstring) — there is no join key to reconcile them against.
        """
        continuation: str | None = None
        pages_fetched = 0

        async with self._client() as client:
            while True:
                if pages_fetched >= self._max_pages:
                    raise EventbriteClientError(
                        f"Eventbrite pagination for event {self._event_id!r} exceeded "
                        f"max_pages={self._max_pages}; refusing to loop further in case "
                        f"the API is returning a continuation token that never ends."
                    )

                params = {"continuation": continuation} if continuation else {}
                response = await client.get(
                    f"/events/{self._event_id}/attendees/", params=params
                )
                pages_fetched += 1

                if response.status_code != 200:
                    raise EventbriteClientError(
                        f"Eventbrite API returned {response.status_code} fetching "
                        f"attendees for event {self._event_id!r}: {response.text[:300]}"
                    )

                data = response.json()
                for raw in data.get("attendees", []) or []:
                    attendee = Attendee.from_api(raw)
                    if attendee is not None:
                        yield attendee

                pagination = data.get("pagination") or {}
                if not pagination.get("has_more_items"):
                    return

                continuation = pagination.get("continuation")
                if not continuation:
                    # A contract violation we cannot page through: stop rather
                    # than re-requesting the same page (no continuation token)
                    # forever.
                    logger.warning(
                        "eventbrite.client outcome=stop_paging event_id=%s "
                        "reason=has_more_items_without_continuation",
                        self._event_id,
                    )
                    return

    async def fetch_attendees(self) -> list[Attendee]:
        """:meth:`iter_attendees`, collected — what ``eventbrite.sync.run_sync`` calls."""
        return [attendee async for attendee in self.iter_attendees()]


class EventbriteMock:
    """A ``respx``-backed double for the Eventbrite v3 attendees endpoint.

    Registers one route against ``respx_mock`` that matches
    ``GET .../events/<any>/attendees/``, so a test never has to hardcode the
    :class:`EventbriteClient` instance's exact ``event_id``. Because the route
    lives on a ``respx`` router entered as a context manager (the
    ``respx_mock``/``eventbrite_mock`` pytest fixtures do this), any request
    that does not match — including a real outbound call that slipped past
    everything else — raises rather than reaching the network; the plugin's
    autouse ``_no_network`` fixture blocks the socket layer underneath that as
    a second line of defence.

    Pagination is modelled as a list of pages, each a list of raw attendee
    dicts (the shape :meth:`Attendee.from_api` parses, i.e. Eventbrite's own
    JSON shape — not the parsed :class:`Attendee`)::

        eventbrite_mock.add_attendees([{"profile": {"email": "a@example.edu"}, ...}])
        eventbrite_mock.set_pages(2)
        eventbrite_mock.add_attendees([{...}], page=1)
    """

    def __init__(self, respx_mock: MockRouter, *, base_url: str = DEFAULT_BASE_URL) -> None:
        self._pages: list[list[dict[str, Any]]] = [[]]
        self._failure: tuple[int, Any] | None = None
        # No trailing `$`: a paginated request's URL carries a `?continuation=`
        # query string after this path, which a `$`-anchored pattern would
        # never match past the first (query-less) page.
        pattern = re.escape(base_url) + r"/events/[^/]+/attendees/"
        respx_mock.get(url__regex=pattern).mock(side_effect=self._respond)

    def add_attendees(self, attendees: list[dict[str, Any]], *, page: int = -1) -> None:
        """Append raw attendee dicts to ``page`` (the last page by default)."""
        self._pages[page].extend(attendees)

    def set_pages(self, n: int) -> None:
        """Pre-declare ``n`` pages so :meth:`add_attendees` can target each by index."""
        while len(self._pages) < n:
            self._pages.append([])

    def fail_with(self, status_code: int, body: Any = None) -> None:
        """Make every subsequent request return ``status_code`` instead of a page."""
        self._failure = (status_code, body)

    def _respond(self, request: httpx.Request) -> httpx.Response:
        if self._failure is not None:
            status_code, body = self._failure
            return httpx.Response(
                status_code, json=body if body is not None else {"error_description": "mocked"}
            )

        continuation = request.url.params.get("continuation")
        page_index = int(continuation) if continuation else 0
        attendees = self._pages[page_index] if 0 <= page_index < len(self._pages) else []
        has_more = page_index + 1 < len(self._pages)
        return httpx.Response(
            200,
            json={
                "attendees": attendees,
                "pagination": {
                    "has_more_items": has_more,
                    "continuation": str(page_index + 1) if has_more else None,
                },
            },
        )
