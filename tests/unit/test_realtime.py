"""Tests for eventkit.realtime: the phase-01 doc's stated priorities — a
monotonic, restart-durable cursor; `since`/`limit` paging that drains a
backlog rather than skipping to the end; the polling route requiring auth
same as everything else; and the opt-in WebSocket push dropping a slow
subscriber without touching any other subscriber or connection (the
`ticketed` module-global-socket-list bug this replaces)."""

from __future__ import annotations

import asyncio
import logging
import time

import pytest
from fastapi import FastAPI, WebSocketDisconnect
from starlette.testclient import TestClient

from eventkit.auth import AllowList, EasyAuth, Principal, install, issue_ws_ticket, ws_dependency
from eventkit.db import Database, declarative_base
from eventkit.realtime import (
    ChangeBroadcaster,
    ChangeEntry,
    ChangeLogMixin,
    ChangeOp,
    make_changes_router,
    make_changes_ws_route,
    poll_changes,
    record_change,
)

Base = declarative_base()


class ChangeLog(ChangeLogMixin, Base):
    __tablename__ = "change_log"


@pytest.fixture
def database():
    db = Database("sqlite:///:memory:")
    Base.metadata.create_all(db.engine)
    try:
        yield db
    finally:
        db.engine.dispose()


class TestRecordAndPollChanges:
    def test_since_zero_returns_everything(self, database):
        with database.session() as session:
            record_change(session, ChangeLog, entity="registrant", entity_id=1, op="created")
            record_change(session, ChangeLog, entity="registrant", entity_id=2, op="created")
            session.commit()

        with database.session() as session:
            page = poll_changes(session, ChangeLog, since=0)

        assert [c.entity_id for c in page.changes] == ["1", "2"]
        assert page.cursor == 2

    def test_cursor_is_monotonic_and_survives_a_fresh_session(self, database):
        with database.session() as session:
            record_change(session, ChangeLog, entity="room", entity_id="r1", op="created")
            session.commit()
        with database.session() as session:
            first = poll_changes(session, ChangeLog, since=0)

        with database.session() as session:
            record_change(session, ChangeLog, entity="room", entity_id="r2", op="created")
            session.commit()
        with database.session() as session:
            second = poll_changes(session, ChangeLog, since=first.cursor)

        assert second.cursor > first.cursor
        assert [c.entity_id for c in second.changes] == ["r2"]

    def test_since_at_current_max_returns_empty_and_echoes_cursor(self, database):
        with database.session() as session:
            record_change(session, ChangeLog, entity="room", entity_id="r1", op="created")
            session.commit()

        with database.session() as session:
            head = poll_changes(session, ChangeLog, since=0).cursor
            page = poll_changes(session, ChangeLog, since=head)

        assert page.changes == []
        assert page.cursor == head

    def test_limit_pages_through_a_backlog_without_skipping_rows(self, database):
        with database.session() as session:
            for i in range(5):
                record_change(session, ChangeLog, entity="room", entity_id=i, op="created")
            session.commit()

        with database.session() as session:
            first_page = poll_changes(session, ChangeLog, since=0, limit=2)
            second_page = poll_changes(session, ChangeLog, since=first_page.cursor, limit=2)
            third_page = poll_changes(session, ChangeLog, since=second_page.cursor, limit=2)

        assert [c.entity_id for c in first_page.changes] == ["0", "1"]
        assert [c.entity_id for c in second_page.changes] == ["2", "3"]
        assert [c.entity_id for c in third_page.changes] == ["4"]
        assert third_page.cursor == first_page.cursor + 3

    def test_payload_round_trips(self, database):
        with database.session() as session:
            record_change(
                session,
                ChangeLog,
                entity="registrant",
                entity_id=7,
                op=ChangeOp.updated,
                payload={"checkin_status": {"2030-06-01": 1}},
            )
            session.commit()

        with database.session() as session:
            page = poll_changes(session, ChangeLog, since=0)

        assert page.changes[0].op is ChangeOp.updated
        assert page.changes[0].payload == {"checkin_status": {"2030-06-01": 1}}

    def test_payload_defaults_to_none(self, database):
        with database.session() as session:
            record_change(session, ChangeLog, entity="room", entity_id=1, op="deleted")
            session.commit()

        with database.session() as session:
            page = poll_changes(session, ChangeLog, since=0)

        assert page.changes[0].payload is None

    def test_invalid_op_raises(self, database):
        with database.session() as session:
            with pytest.raises(ValueError):
                record_change(session, ChangeLog, entity="room", entity_id=1, op="reticulated")

    def test_negative_since_raises(self, database):
        with database.session() as session:
            with pytest.raises(ValueError):
                poll_changes(session, ChangeLog, since=-1)

    def test_non_positive_limit_raises(self, database):
        with database.session() as session:
            with pytest.raises(ValueError):
                poll_changes(session, ChangeLog, since=0, limit=0)


ADMIN = "admin@example.edu"
OUTSIDER = "nobody@example.org"


def build_app(database: Database, *, dev_principal: str | None = ADMIN) -> FastAPI:
    auth = EasyAuth(AllowList([ADMIN]), dev_principal=dev_principal)
    app = FastAPI()
    app.state.database = database
    app.state.auth = auth
    install(app, auth)
    app.include_router(make_changes_router(ChangeLog, db=database.get_db, principal=auth.require))
    return app


class TestChangesRouter:
    def test_requires_authentication(self, database):
        client = TestClient(build_app(database, dev_principal=None), follow_redirects=False)

        response = client.get("/api/changes")

        assert response.status_code == 401

    def test_authenticated_default_poll_returns_everything(self, database):
        with database.session() as session:
            record_change(session, ChangeLog, entity="room", entity_id=1, op="created")
            session.commit()
        client = TestClient(build_app(database))

        response = client.get("/api/changes")

        assert response.status_code == 200
        body = response.json()
        assert body["cursor"] == 1
        assert body["changes"][0]["entity_id"] == "1"

    def test_since_and_limit_query_params_are_honored(self, database):
        with database.session() as session:
            for i in range(3):
                record_change(session, ChangeLog, entity="room", entity_id=i, op="created")
            session.commit()
        client = TestClient(build_app(database))

        response = client.get("/api/changes", params={"since": 1, "limit": 1})

        assert response.status_code == 200
        body = response.json()
        assert [c["entity_id"] for c in body["changes"]] == ["1"]
        assert body["cursor"] == 2

    def test_negative_since_query_param_is_rejected_at_the_http_layer(self, database):
        client = TestClient(build_app(database))

        response = client.get("/api/changes", params={"since": -1})

        assert response.status_code == 422

    def test_outsider_principal_is_denied(self, database):
        client = TestClient(build_app(database), follow_redirects=False)

        response = client.get(
            "/api/changes",
            headers={
                "X-MS-CLIENT-PRINCIPAL-NAME": OUTSIDER,
                "X-MS-CLIENT-PRINCIPAL": "e30=",
            },
        )

        assert response.status_code == 403


class TestChangeBroadcaster:
    async def test_publish_delivers_to_a_subscriber(self):
        broadcaster = ChangeBroadcaster()
        entry = ChangeEntry(
            cursor=1,
            entity="room",
            entity_id="1",
            op=ChangeOp.created,
            occurred_at="2030-01-01T00:00:00Z",
        )

        async with broadcaster.subscribe() as queue:
            broadcaster.publish(entry)
            received = await asyncio.wait_for(queue.get(), timeout=1)

        assert received is entry

    async def test_full_subscriber_is_dropped_without_affecting_others(self):
        broadcaster = ChangeBroadcaster(max_queue=1)
        entry_a = ChangeEntry(
            cursor=1,
            entity="room",
            entity_id="1",
            op=ChangeOp.created,
            occurred_at="2030-01-01T00:00:00Z",
        )
        entry_b = ChangeEntry(
            cursor=2,
            entity="room",
            entity_id="2",
            op=ChangeOp.created,
            occurred_at="2030-01-01T00:00:01Z",
        )

        async with broadcaster.subscribe() as slow, broadcaster.subscribe() as healthy:
            broadcaster.publish(entry_a)  # fills both queues (maxsize=1)
            assert broadcaster.subscriber_count == 2

            # Drain `healthy` so it has room again; leave `slow` full on purpose.
            assert await asyncio.wait_for(healthy.get(), timeout=1) is entry_a

            broadcaster.publish(entry_b)  # `slow` is still full -> dropped
            assert broadcaster.subscriber_count == 1

            # `slow` keeps whatever it already had; it just stops receiving more.
            assert await asyncio.wait_for(slow.get(), timeout=1) is entry_a
            assert slow.empty()
            assert await asyncio.wait_for(healthy.get(), timeout=1) is entry_b


class TestChangesWsRoute:
    SECRET = "ws-secret"  # noqa: S105 - test fixture, not a real credential

    def _ws_app(self, broadcaster: ChangeBroadcaster) -> FastAPI:
        auth = EasyAuth(AllowList([ADMIN]))
        app = FastAPI()
        route = make_changes_ws_route(
            broadcaster, ws_dependency=ws_dependency(auth, secret=self.SECRET, scope="changes")
        )
        app.websocket("/ws/changes")(route)
        return app

    def test_valid_ticket_receives_a_published_change(self):
        broadcaster = ChangeBroadcaster()
        app = self._ws_app(broadcaster)
        client = TestClient(app)
        ticket = issue_ws_ticket(Principal(email=ADMIN), secret=self.SECRET, scope="changes")

        with client.websocket_connect(f"/ws/changes?ticket={ticket}") as ws:
            entry = ChangeEntry(
                cursor=1,
                entity="room",
                entity_id="1",
                op=ChangeOp.created,
                occurred_at="2030-01-01T00:00:00Z",
            )
            deadline = time.monotonic() + 2
            while broadcaster.subscriber_count == 0 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert broadcaster.subscriber_count == 1
            broadcaster.publish(entry)
            received = ws.receive_json()

        assert received["entity_id"] == "1"
        assert received["op"] == "created"

    def test_ticket_for_a_non_allow_listed_email_is_rejected_at_connect(self):
        broadcaster = ChangeBroadcaster()
        app = self._ws_app(broadcaster)
        client = TestClient(app)
        ticket = issue_ws_ticket(Principal(email=OUTSIDER), secret=self.SECRET, scope="changes")

        with pytest.raises(Exception):  # noqa: B017 - starlette raises WebSocketDisconnect
            with client.websocket_connect(f"/ws/changes?ticket={ticket}") as ws:
                ws.receive_json()


class _FakeWebSocket:
    """A stand-in that skips the ASGI transport entirely, so the two send-time
    failure branches in `changes_ws` (client disconnect vs. any other send
    error) can be exercised deterministically instead of racing a real
    TestClient teardown."""

    def __init__(self, *, raise_on_send: Exception) -> None:
        self._raise_on_send = raise_on_send
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, data: dict) -> None:
        raise self._raise_on_send


async def _publish_once_subscribed(broadcaster: ChangeBroadcaster, entry: ChangeEntry) -> None:
    deadline = time.monotonic() + 2
    while broadcaster.subscriber_count == 0 and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert broadcaster.subscriber_count == 1
    broadcaster.publish(entry)


class TestChangesWsSendFailureHandling:
    """`changes_ws` is called directly (bypassing FastAPI's dependency
    injection) so the fake websocket's raised exception is what the loop
    actually sees on `send_json`, matching the module docstring's claim that a
    send failure ends only that one connection -- it must not propagate out of
    `changes_ws` and it must not touch the broadcaster's other subscribers."""

    ENTRY = ChangeEntry(
        cursor=1,
        entity="room",
        entity_id="1",
        op=ChangeOp.created,
        occurred_at="2030-01-01T00:00:00Z",
    )

    async def test_client_disconnect_during_send_is_swallowed(self, caplog):
        caplog.set_level(logging.INFO, logger="eventkit.realtime")
        broadcaster = ChangeBroadcaster()
        route = make_changes_ws_route(broadcaster, ws_dependency=lambda: None)
        fake_ws = _FakeWebSocket(raise_on_send=WebSocketDisconnect())

        task = asyncio.create_task(route(fake_ws, principal_=Principal(email=ADMIN)))
        await _publish_once_subscribed(broadcaster, self.ENTRY)

        await asyncio.wait_for(task, timeout=2)  # returns cleanly, does not raise

        assert fake_ws.accepted
        assert "outcome=disconnect" in caplog.text

    async def test_other_send_errors_are_logged_and_do_not_propagate(self, caplog):
        broadcaster = ChangeBroadcaster()
        route = make_changes_ws_route(broadcaster, ws_dependency=lambda: None)
        fake_ws = _FakeWebSocket(raise_on_send=ConnectionResetError("gone"))

        task = asyncio.create_task(route(fake_ws, principal_=Principal(email=ADMIN)))
        await _publish_once_subscribed(broadcaster, self.ENTRY)

        await asyncio.wait_for(task, timeout=2)  # returns cleanly, does not raise

        assert "outcome=send_error" in caplog.text
