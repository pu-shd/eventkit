"""Tests for eventkit.eventbrite.sync: a fake SyncPorts plus a fake client,
asserting emitted events with zero database and zero network — the phase-01
doc's stated priority for this module — plus a real SqlAlchemySyncPorts round
trip against a throwaway declarative Base."""

from __future__ import annotations

import datetime as _dt
from types import SimpleNamespace

import pytest
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from eventkit.db import Database, declarative_base
from eventkit.eventbrite.models import AggregatedPayment, Attendee, PaymentStatus
from eventkit.eventbrite.sync import SqlAlchemySyncPorts, SyncEvent, SyncResult, run_sync

FROZEN_NOW = _dt.datetime(2030, 6, 1, 12, 0, 0)


class FakeClient:
    """Stands in for EventbriteClient: same ``event_id`` + ``fetch_attendees()``
    surface run_sync actually uses, no HTTP at all."""

    def __init__(self, attendees=None, *, event_id="42", error: Exception | None = None):
        self._attendees = attendees or []
        self._event_id = event_id
        self._error = error

    @property
    def event_id(self) -> str:
        return self._event_id

    async def fetch_attendees(self):
        if self._error is not None:
            raise self._error
        return self._attendees


class FakePorts:
    def __init__(self, *, payments=None, registrants=None):
        self.payments = dict(payments or {})
        self.registrants = dict(registrants or {})
        self.results: list[SyncResult] = []
        self.emitted: list[tuple[SyncEvent, dict]] = []

    def load_existing_payments(self):
        return dict(self.payments)

    def load_registrant_index(self):
        return self.registrants

    def upsert_payment(self, agg: AggregatedPayment):
        created = agg.email not in self.payments
        self.payments[agg.email] = agg
        return agg, created

    def record_sync(self, result: SyncResult) -> None:
        self.results.append(result)

    async def emit(self, event: SyncEvent, ctx) -> None:
        self.emitted.append((event, dict(ctx)))


def attendee(email: str, *, status: str = "Attending", **kwargs) -> Attendee:
    raw = {"profile": {"email": email}, "status": status, **kwargs}
    parsed = Attendee.from_api(raw)
    assert parsed is not None
    return parsed


def registrant(*, tickets_sold_separately: bool, serial=None, sid=None) -> SimpleNamespace:
    return SimpleNamespace(
        tickets_sold_separately=tickets_sold_separately, serial=serial, sid=sid
    )


class TestUnmatchedAndCompletedPayment:
    async def test_unmatched_payment_fires_for_new_paid_attendee_with_no_registrant(self):
        client = FakeClient([attendee("ada@example.edu")])
        ports = FakePorts()

        result = await run_sync(client, ports, now=FROZEN_NOW)

        assert result.status == "success"
        assert ports.emitted == [
            (
                SyncEvent.UNMATCHED_PAYMENT,
                {"email": "ada@example.edu", "full_name": "", "order_id": None},
            )
        ]

    async def test_completed_payment_fires_when_registrant_sold_tickets_separately(self):
        client = FakeClient([attendee("ada@example.edu")])
        ports = FakePorts(
            registrants={"ada@example.edu": registrant(tickets_sold_separately=True, sid=7)}
        )

        await run_sync(client, ports, now=FROZEN_NOW)

        assert len(ports.emitted) == 1
        event, ctx = ports.emitted[0]
        assert event == SyncEvent.COMPLETED_PAYMENT
        assert ctx["email"] == "ada@example.edu"
        assert ctx["sid"] == 7

    async def test_no_event_when_registrant_is_exempt(self):
        """tickets_sold_separately=False means the registrant is exempt from
        buying an Eventbrite ticket at all — matching the predecessor, which
        only ever fires completed_payment through that same flag."""
        client = FakeClient([attendee("ada@example.edu")])
        ports = FakePorts(
            registrants={"ada@example.edu": registrant(tickets_sold_separately=False)}
        )

        await run_sync(client, ports, now=FROZEN_NOW)

        assert ports.emitted == []

    async def test_no_event_for_non_paid_attendee(self):
        client = FakeClient([attendee("ada@example.edu", status="Refunded")])
        ports = FakePorts()

        await run_sync(client, ports, now=FROZEN_NOW)

        assert ports.emitted == []

    async def test_no_event_for_an_attendee_that_already_had_a_payment_row(self):
        existing = AggregatedPayment(
            email="ada@example.edu", status=PaymentStatus.PAID, paid_at=FROZEN_NOW
        )
        client = FakeClient([attendee("ada@example.edu")])
        ports = FakePorts(payments={"ada@example.edu": existing})

        await run_sync(client, ports, now=FROZEN_NOW)

        assert ports.emitted == []

    async def test_duplicate_attendee_rows_aggregate_before_a_single_upsert(self):
        client = FakeClient(
            [attendee("ada@example.edu", costs={"gross": {"value": 100}}),
             attendee("ada@example.edu", costs={"gross": {"value": 200}})]
        )
        ports = FakePorts()

        result = await run_sync(client, ports, now=FROZEN_NOW)

        assert result.payments_created == 1
        assert ports.payments["ada@example.edu"].gross_cents == 300


class TestSyncResultAccounting:
    async def test_records_pulled_and_created_vs_updated_counts(self):
        client = FakeClient([attendee("a@example.edu"), attendee("b@example.edu")])
        ports = FakePorts(
            payments={
                "b@example.edu": AggregatedPayment(
                    email="b@example.edu", status=PaymentStatus.PAID, paid_at=FROZEN_NOW
                )
            }
        )

        result = await run_sync(client, ports, now=FROZEN_NOW)

        assert result.records_pulled == 2
        assert result.payments_created == 1
        assert result.payments_updated == 1
        assert ports.results == [result]

    async def test_started_at_uses_the_injected_clock(self):
        client = FakeClient([])
        ports = FakePorts()

        result = await run_sync(client, ports, now=FROZEN_NOW)

        assert result.started_at == FROZEN_NOW
        # finished_at always uses the real wall clock (only started_at takes
        # the injected `now`), so it cannot be compared against a fixed
        # FROZEN_NOW that is not "now" — assert only its type here.
        assert isinstance(result.finished_at, _dt.datetime)


class TestSyncFailure:
    async def test_client_error_produces_a_failed_result_and_sync_failed_event(self):
        client = FakeClient(error=RuntimeError("boom"), event_id="evt-1")
        ports = FakePorts()

        result = await run_sync(client, ports, now=FROZEN_NOW)

        assert result.status == "failed"
        assert result.error == "boom"
        assert ports.results == [result]
        assert ports.emitted == [
            (
                SyncEvent.SYNC_FAILED,
                {
                    "event_slug": "evt-1",
                    "reason": "boom",
                    "attempted_at": FROZEN_NOW.isoformat(),
                },
            )
        ]

    async def test_records_pulled_reflects_what_was_fetched_before_the_failure(self):
        class FlakyPorts(FakePorts):
            def upsert_payment(self, agg):
                raise RuntimeError("db is down")

        client = FakeClient([attendee("a@example.edu"), attendee("b@example.edu")])
        ports = FlakyPorts()

        result = await run_sync(client, ports, now=FROZEN_NOW)

        assert result.status == "failed"
        assert result.records_pulled == 2
        assert result.payments_created == 0


# --------------------------------------------------------------------------
# SqlAlchemySyncPorts: a real round trip against a throwaway declarative Base
# --------------------------------------------------------------------------
Base = declarative_base()


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True)
    first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    order_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    attendee_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    paid_at: Mapped[_dt.datetime] = mapped_column()
    # Deliberately not named gross_cents/net_cents, to exercise column_map.
    gross_amount: Mapped[int] = mapped_column(default=0)
    net_amount: Mapped[int] = mapped_column(default=0)


class Registrant(Base):
    __tablename__ = "registrants"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True)
    tickets_sold_separately: Mapped[bool] = mapped_column(default=False)


class SyncLog(Base):
    __tablename__ = "sync_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    synced_at: Mapped[_dt.datetime] = mapped_column()
    records_pulled: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(20))
    error_message: Mapped[str | None] = mapped_column(nullable=True)


@pytest.fixture
def database():
    db = Database("sqlite:///:memory:")
    Base.metadata.create_all(db.engine)
    try:
        yield db
    finally:
        db.engine.dispose()


def make_ports(session, **overrides) -> SqlAlchemySyncPorts:
    defaults = {
        "payment_model": Payment,
        "registrant_model": Registrant,
        "sync_log_model": SyncLog,
        "column_map": {"gross_cents": "gross_amount", "net_cents": "net_amount"},
    }
    defaults.update(overrides)
    return SqlAlchemySyncPorts(session, **defaults)


class TestSqlAlchemySyncPortsRoundTrip:
    async def test_full_sync_creates_a_payment_and_a_sync_log_row(self, database):
        with database.session() as session:
            session.add(Registrant(email="ada@example.edu", tickets_sold_separately=True))
            session.commit()

            ports = make_ports(session)
            client = FakeClient([attendee("ada@example.edu", costs={"gross": {"value": 500}})])

            result = await run_sync(client, ports, now=FROZEN_NOW)
            assert result.status == "success"

        with database.session() as session:
            payment = session.query(Payment).filter_by(email="ada@example.edu").one()
            assert payment.status == "paid"
            assert payment.gross_amount == 500

            log_row = session.query(SyncLog).one()
            assert log_row.status == "success"
            assert log_row.records_pulled == 1

    async def test_second_sync_updates_rather_than_duplicates(self, database):
        with database.session() as session:
            ports = make_ports(session)
            client = FakeClient([attendee("ada@example.edu", costs={"gross": {"value": 100}})])
            await run_sync(client, ports, now=FROZEN_NOW)

            client_again = FakeClient(
                [attendee("ada@example.edu", costs={"gross": {"value": 100}})]
            )
            result = await run_sync(client_again, ports, now=FROZEN_NOW)

        assert result.payments_created == 0
        assert result.payments_updated == 1
        with database.session() as session:
            assert session.query(Payment).count() == 1

    async def test_failed_sync_rolls_back_partial_writes_but_still_logs(self, database):
        class BoomPorts(SqlAlchemySyncPorts):
            def upsert_payment(self, agg):
                super().upsert_payment(agg)
                raise RuntimeError("simulated write failure")

        with database.session() as session:
            ports = BoomPorts(
                session,
                payment_model=Payment,
                registrant_model=Registrant,
                sync_log_model=SyncLog,
                column_map={"gross_cents": "gross_amount", "net_cents": "net_amount"},
            )
            client = FakeClient([attendee("ada@example.edu")])

            result = await run_sync(client, ports, now=FROZEN_NOW)
            assert result.status == "failed"

        with database.session() as session:
            # The half-written Payment row from the failed attempt must not
            # have survived the rollback in record_sync.
            assert session.query(Payment).count() == 0
            log_row = session.query(SyncLog).one()
            assert log_row.status == "failed"

    async def test_no_sync_log_model_means_record_sync_is_a_no_op(self, database):
        with database.session() as session:
            ports = SqlAlchemySyncPorts(
                session,
                payment_model=Payment,
                registrant_model=Registrant,
                column_map={"gross_cents": "gross_amount", "net_cents": "net_amount"},
            )
            client = FakeClient([attendee("ada@example.edu")])
            result = await run_sync(client, ports, now=FROZEN_NOW)
            assert result.status == "success"

        with database.session() as session:
            assert session.query(SyncLog).count() == 0
            assert session.query(Payment).count() == 1

    async def test_emit_without_a_notifier_is_a_no_op(self, database):
        with database.session() as session:
            ports = make_ports(session)
            # No registrant at all -> would-be UNMATCHED_PAYMENT, but with no
            # notifier configured this must not raise.
            client = FakeClient([attendee("ada@example.edu")])
            result = await run_sync(client, ports, now=FROZEN_NOW)
            assert result.status == "success"

    async def test_emit_delegates_to_a_configured_notifier(self, database, mail_outbox):
        from eventkit.notify import Notifier, NotifyPolicy
        from eventkit.notify.render import Renderer

        with database.session() as session:
            notifier = Notifier(
                mail_outbox,
                Renderer(),
                NotifyPolicy(enabled={"unmatched_payment": True}, default_recipients=["a@b.edu"]),
                from_email="noreply@example.edu",
            )
            ports = make_ports(session, notifier=notifier)
            client = FakeClient([attendee("ada@example.edu")])

            await run_sync(client, ports, now=FROZEN_NOW)

        assert len(mail_outbox.outbox) == 1
        assert mail_outbox.outbox[0].to == ["a@b.edu"]
