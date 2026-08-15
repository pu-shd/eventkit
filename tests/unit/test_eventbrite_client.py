"""Tests for eventkit.eventbrite.client: pagination across the continuation
token, non-200 responses raising a typed error, the max_pages runaway guard,
and the EventbriteMock test double itself (asserted here so a bug in the
double doesn't silently make every consumer's test pass for the wrong
reason)."""

from __future__ import annotations

import httpx
import pytest

from eventkit.eventbrite.client import DEFAULT_BASE_URL, EventbriteClient, EventbriteClientError


def make_client(**overrides) -> EventbriteClient:
    defaults = {"token": "t0k3n", "event_id": "999"}
    defaults.update(overrides)
    return EventbriteClient(**defaults)


class TestConstruction:
    def test_requires_token(self):
        with pytest.raises(EventbriteClientError):
            EventbriteClient("", "999")

    def test_requires_event_id(self):
        with pytest.raises(EventbriteClientError):
            EventbriteClient("t0k3n", "")

    def test_event_id_is_exposed(self):
        assert make_client(event_id="abc").event_id == "abc"


class TestFetchAttendeesViaEventbriteMock:
    """Exercises the client through the fixture apps will actually use."""

    async def test_single_page(self, eventbrite_mock):
        eventbrite_mock.add_attendees(
            [
                {"profile": {"email": "ada@example.edu"}, "status": "Attending"},
                {"profile": {"email": "bea@example.edu"}, "status": "Checked In"},
            ]
        )
        client = make_client()
        attendees = await client.fetch_attendees()
        assert [a.email for a in attendees] == ["ada@example.edu", "bea@example.edu"]

    async def test_records_without_email_are_dropped(self, eventbrite_mock):
        eventbrite_mock.add_attendees(
            [
                {"profile": {"email": "ada@example.edu"}, "status": "Attending"},
                {"profile": {}, "status": "Attending"},
            ]
        )
        attendees = await make_client().fetch_attendees()
        assert [a.email for a in attendees] == ["ada@example.edu"]

    async def test_pages_through_a_backlog(self, eventbrite_mock):
        eventbrite_mock.set_pages(3)
        eventbrite_mock.add_attendees([{"profile": {"email": "a@example.edu"}}], page=0)
        eventbrite_mock.add_attendees([{"profile": {"email": "b@example.edu"}}], page=1)
        eventbrite_mock.add_attendees([{"profile": {"email": "c@example.edu"}}], page=2)

        attendees = await make_client().fetch_attendees()
        assert [a.email for a in attendees] == ["a@example.edu", "b@example.edu", "c@example.edu"]

    async def test_non_200_response_raises_client_error(self, eventbrite_mock):
        eventbrite_mock.fail_with(429, {"error_description": "rate limited"})
        with pytest.raises(EventbriteClientError, match="429"):
            await make_client().fetch_attendees()

    async def test_max_pages_guard_stops_a_runaway_continuation(self, eventbrite_mock):
        eventbrite_mock.set_pages(5)
        for i in range(5):
            eventbrite_mock.add_attendees([{"profile": {"email": f"p{i}@example.edu"}}], page=i)

        with pytest.raises(EventbriteClientError, match="max_pages"):
            await make_client(max_pages=2).fetch_attendees()

    async def test_a_request_outside_the_attendees_route_is_never_reached(self, eventbrite_mock):
        """The whole point of the fixture: only the attendees route it registers
        is mocked, so anything else — including a real Eventbrite endpoint the
        client never calls — raises rather than reaching the network."""
        with pytest.raises(Exception):  # noqa: B017 - respx's own assertion type
            async with httpx.AsyncClient() as raw:
                await raw.get(f"{DEFAULT_BASE_URL}/users/me/")


class TestInjectableTransport:
    """The second testability seam: a raw ``httpx.MockTransport`` handler,
    independent of ``respx``/``eventbrite_mock`` — this is what lets the client
    itself be tested without either extra."""

    async def test_has_more_items_without_continuation_stops_paging(self):
        """A contract violation (the API claims more pages exist but gives no
        way to fetch them) must stop, not loop forever re-requesting the page
        it already has."""
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(
                200,
                json={
                    "attendees": [{"profile": {"email": "solo@example.edu"}}],
                    "pagination": {"has_more_items": True, "continuation": None},
                },
            )

        client = make_client(transport=httpx.MockTransport(handler))
        attendees = await client.fetch_attendees()
        assert [a.email for a in attendees] == ["solo@example.edu"]
        assert calls == 1
