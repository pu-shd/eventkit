"""The aggregation truth table.

Extracted from ``ticketed/backend/eventbrite.py:78-160``, which had no coverage
because it sat inside a function that also did HTTP paging, database upserts and
email notifications.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from eventkit.eventbrite import (
    DEFAULT_STATUS_MAP,
    AggregatedPayment,
    Attendee,
    PaymentStatus,
    aggregate_by_email,
    parse_eventbrite_datetime,
)

NOW = _dt.datetime(2030, 6, 1, 12, 0, 0)


def attendee(
    email="ada@example.edu",
    status="Attending",
    gross=16000,
    net=15000,
    created=None,
    order_id="order-1",
    attendee_id="att-1",
    first="Ada",
    last="Lovelace",
) -> Attendee:
    return Attendee(
        id=attendee_id,
        order_id=order_id,
        email=email,
        first_name=first,
        last_name=last,
        status_raw=status,
        gross_cents=gross,
        net_cents=net,
        created=created,
    )


class TestAttendeeFromApi:
    def test_parses_a_realistic_record(self):
        parsed = Attendee.from_api(
            {
                "id": "att-1",
                "order_id": "order-1",
                "status": "Attending",
                "created": "2030-05-26T20:00:00Z",
                "profile": {
                    "email": "  Ada@Example.EDU ",
                    "first_name": "Ada",
                    "last_name": "Lovelace",
                },
                "costs": {"gross": {"value": 16000}, "net": {"value": 15000}},
            }
        )
        assert parsed is not None
        assert parsed.email == "ada@example.edu"
        assert parsed.gross_cents == 16000
        assert parsed.net_cents == 15000
        assert parsed.created == _dt.datetime(2030, 5, 26, 20, 0, 0)

    @pytest.mark.parametrize(
        "record",
        [
            {},
            {"profile": {}},
            {"profile": {"email": ""}},
            {"profile": {"email": "   "}},
            {"profile": None},
        ],
    )
    def test_records_without_an_email_are_dropped(self, record):
        # Email is the only join key Eventbrite gives us.
        assert Attendee.from_api(record) is None

    @pytest.mark.parametrize(
        "costs",
        [None, {}, {"gross": None}, {"gross": {}}, {"gross": {"value": None}},
         {"gross": {"value": "not a number"}}, "nonsense"],
    )
    def test_malformed_costs_default_to_zero(self, costs):
        parsed = Attendee.from_api({"profile": {"email": "a@example.edu"}, "costs": costs})
        assert parsed is not None
        assert parsed.gross_cents == 0
        assert parsed.net_cents == 0

    def test_status_mapping(self):
        for raw in ("Attending", "Checked In", "Registered", "Placed"):
            assert attendee(status=raw).status() == PaymentStatus.PAID
        assert attendee(status="Cancelled").status() == PaymentStatus.CANCELLED
        assert attendee(status="Refunded").status() == PaymentStatus.REFUNDED

    def test_status_mapping_is_case_insensitive(self):
        assert attendee(status="attending").status() == PaymentStatus.PAID
        assert attendee(status="ATTENDING").status() == PaymentStatus.PAID

    def test_unknown_status_becomes_refunded(self):
        """Preserves a surprising behaviour of the code being replaced.

        Its status chain ends in a bare ``else: status = "refunded"``, so any
        status Eventbrite invents maps to refunded. Changing it would silently
        reclassify existing rows on the next sync.
        """
        assert attendee(status="Some New Status").status() == PaymentStatus.REFUNDED


class TestParseEventbriteDatetime:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("2030-05-26T20:00:00Z", _dt.datetime(2030, 5, 26, 20, 0, 0)),
            ("2030-05-26T20:00:00", _dt.datetime(2030, 5, 26, 20, 0, 0)),
            (None, None),
            ("", None),
            ("not a date", None),
        ],
    )
    def test_table(self, raw, expected):
        assert parse_eventbrite_datetime(raw) == expected

    def test_offset_is_converted_to_naive_utc(self):
        # An aware datetime here would raise TypeError inside the latest-wins
        # comparison in aggregate_by_email.
        parsed = parse_eventbrite_datetime("2030-05-26T16:00:00-04:00")
        assert parsed == _dt.datetime(2030, 5, 26, 20, 0, 0)
        assert parsed.tzinfo is None

    def test_all_results_are_comparable(self):
        a = parse_eventbrite_datetime("2030-05-26T20:00:00Z")
        b = parse_eventbrite_datetime("2030-05-27T20:00:00-04:00")
        assert b > a  # would raise if one were aware and the other naive


class TestAggregationTruthTable:
    def test_single_attendee(self):
        result = aggregate_by_email([attendee()], now=NOW)
        assert list(result) == ["ada@example.edu"]
        payment = result["ada@example.edu"]
        assert payment.status == PaymentStatus.PAID
        assert payment.gross_cents == 16000
        assert payment.attendee_count == 1

    def test_paid_beats_refunded_regardless_of_order(self):
        refunded = attendee(status="Refunded", gross=16000, net=15000)
        paid = attendee(status="Attending", gross=16000, net=15000)
        for order in ([refunded, paid], [paid, refunded]):
            result = aggregate_by_email(order, now=NOW)
            assert result["ada@example.edu"].status == PaymentStatus.PAID

    def test_paid_replacing_refunded_replaces_amounts_rather_than_summing(self):
        """A documented consequence of the original nested conditionals."""
        result = aggregate_by_email(
            [
                attendee(status="Refunded", gross=16000, net=15000),
                attendee(status="Attending", gross=11000, net=10000),
            ],
            now=NOW,
        )
        payment = result["ada@example.edu"]
        assert payment.gross_cents == 11000
        assert payment.net_cents == 10000
        assert payment.attendee_count == 1

    def test_multiple_paid_records_sum(self):
        """One purchaser buying two tickets.

        This is a 500 today, because ``Payment.email`` carries ``unique=True``.
        Aggregating was the workaround; dropping the constraint is the fix.
        """
        result = aggregate_by_email(
            [
                attendee(gross=16000, net=15000, attendee_id="att-1"),
                attendee(gross=16000, net=15000, attendee_id="att-2"),
            ],
            now=NOW,
        )
        payment = result["ada@example.edu"]
        assert payment.gross_cents == 32000
        assert payment.net_cents == 30000
        assert payment.attendee_count == 2

    def test_multiple_refunded_records_also_sum(self):
        result = aggregate_by_email(
            [
                attendee(status="Refunded", gross=100, net=90),
                attendee(status="Refunded", gross=200, net=180),
            ],
            now=NOW,
        )
        payment = result["ada@example.edu"]
        assert payment.status == PaymentStatus.REFUNDED
        assert payment.gross_cents == 300

    def test_refund_after_paid_is_ignored_entirely(self):
        result = aggregate_by_email(
            [
                attendee(status="Attending", gross=16000, net=15000),
                attendee(status="Refunded", gross=16000, net=15000),
            ],
            now=NOW,
        )
        payment = result["ada@example.edu"]
        assert payment.status == PaymentStatus.PAID
        assert payment.gross_cents == 16000
        # The ignored record does not inflate the count either.
        assert payment.attendee_count == 1

    def test_latest_paid_at_wins_the_identity_fields(self):
        early = attendee(
            created=_dt.datetime(2030, 5, 1), order_id="early", attendee_id="a1",
            first="Old", last="Name",
        )
        late = attendee(
            created=_dt.datetime(2030, 5, 20), order_id="late", attendee_id="a2",
            first="New", last="Name",
        )
        for order in ([early, late], [late, early]):
            payment = aggregate_by_email(order, now=NOW)["ada@example.edu"]
            assert payment.order_id == "late"
            assert payment.attendee_id == "a2"
            assert payment.first_name == "New"
            assert payment.paid_at == _dt.datetime(2030, 5, 20)

    def test_amounts_sum_regardless_of_which_record_wins_identity(self):
        payment = aggregate_by_email(
            [
                attendee(created=_dt.datetime(2030, 5, 20), gross=100, net=90),
                attendee(created=_dt.datetime(2030, 5, 1), gross=200, net=180),
            ],
            now=NOW,
        )["ada@example.edu"]
        assert payment.gross_cents == 300
        assert payment.paid_at == _dt.datetime(2030, 5, 20)

    def test_missing_created_falls_back_to_now(self):
        payment = aggregate_by_email([attendee(created=None)], now=NOW)["ada@example.edu"]
        assert payment.paid_at == NOW

    def test_distinct_emails_stay_distinct(self):
        result = aggregate_by_email(
            [attendee(email="ada@example.edu"), attendee(email="grace@example.edu")],
            now=NOW,
        )
        assert set(result) == {"ada@example.edu", "grace@example.edu"}

    def test_email_case_is_folded(self):
        result = aggregate_by_email(
            [
                Attendee(email="ada@example.edu", status_raw="Attending", gross_cents=100),
                Attendee(email="ADA@EXAMPLE.EDU", status_raw="Attending", gross_cents=100),
            ],
            now=NOW,
        )
        assert list(result) == ["ada@example.edu"]
        assert result["ada@example.edu"].gross_cents == 200

    def test_empty_input(self):
        assert aggregate_by_email([], now=NOW) == {}

    def test_insertion_order_is_preserved(self):
        result = aggregate_by_email(
            [
                attendee(email="zoe@example.edu"),
                attendee(email="ada@example.edu"),
                attendee(email="mia@example.edu"),
            ],
            now=NOW,
        )
        assert list(result) == ["zoe@example.edu", "ada@example.edu", "mia@example.edu"]

    def test_cancelled_is_treated_as_not_paid(self):
        result = aggregate_by_email(
            [
                attendee(status="Cancelled", gross=100, net=90),
                attendee(status="Attending", gross=200, net=180),
            ],
            now=NOW,
        )
        assert result["ada@example.edu"].status == PaymentStatus.PAID
        assert result["ada@example.edu"].gross_cents == 200


class TestPurity:
    def test_is_deterministic(self):
        records = [attendee(), attendee(status="Refunded")]
        assert aggregate_by_email(records, now=NOW) == aggregate_by_email(records, now=NOW)

    def test_does_not_mutate_its_inputs(self):
        records = [attendee(gross=100), attendee(gross=200)]
        before = [r.model_dump() for r in records]
        aggregate_by_email(records, now=NOW)
        assert [r.model_dump() for r in records] == before

    def test_custom_status_map_is_honoured(self):
        custom = dict(DEFAULT_STATUS_MAP)
        custom["attending"] = PaymentStatus.REFUNDED
        result = aggregate_by_email([attendee(status="Attending")], status_map=custom, now=NOW)
        assert result["ada@example.edu"].status == PaymentStatus.REFUNDED

    def test_result_type(self):
        result = aggregate_by_email([attendee()], now=NOW)
        assert isinstance(result["ada@example.edu"], AggregatedPayment)
