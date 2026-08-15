"""Tests for eventkit.admin: HMAC task-token signing/verification, single-use
nonce enforcement, audit logging for every outcome (allow and deny alike),
and the router that wires all three together
F.9's ``admin-task.yml`` fallback spec (path|sha256(body)|ts, +-300s,
single-use nonce table, audit row)."""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Mapped, mapped_column
from starlette.testclient import TestClient

from eventkit.admin import (
    AuditLogMixin,
    AuditOutcome,
    InvalidTaskToken,
    NonceMixin,
    consume_nonce,
    make_task_router,
    record_audit,
    sign_task_request,
    verify_task_request,
)
from eventkit.db import Database, declarative_base

Base = declarative_base()

SECRET = "s3cr3t-shared-with-the-workflow"


class AdminTaskNonce(NonceMixin, Base):
    __tablename__ = "admin_task_nonce"


class AdminAuditLog(AuditLogMixin, Base):
    __tablename__ = "admin_audit_log"


class Counter(Base):
    __tablename__ = "counters"

    id: Mapped[int] = mapped_column(primary_key=True)
    value: Mapped[int] = mapped_column(default=0)


@pytest.fixture
def database():
    db = Database("sqlite:///:memory:")
    Base.metadata.create_all(db.engine)
    try:
        yield db
    finally:
        db.engine.dispose()


# --------------------------------------------------------------------------
# sign_task_request / verify_task_request
# --------------------------------------------------------------------------
class TestSignAndVerify:
    def test_valid_signature_verifies(self):
        ts, sig = sign_task_request("/api/admin/tasks/clear", b"{}", secret=SECRET, ts=1_000)

        verify_task_request(
            path="/api/admin/tasks/clear",
            body=b"{}",
            signature=sig,
            timestamp=ts,
            secret=SECRET,
            now=1_000,
        )

    def test_signature_does_not_transfer_to_a_different_path(self):
        ts, sig = sign_task_request("/api/admin/tasks/clear", b"{}", secret=SECRET, ts=1_000)

        with pytest.raises(InvalidTaskToken) as exc:
            verify_task_request(
                path="/api/admin/tasks/reset-fixtures",
                body=b"{}",
                signature=sig,
                timestamp=ts,
                secret=SECRET,
                now=1_000,
            )
        assert exc.value.reason == "signature_mismatch"

    def test_signature_does_not_transfer_to_a_different_body(self):
        ts, sig = sign_task_request("/api/admin/tasks/clear", b"{}", secret=SECRET, ts=1_000)

        with pytest.raises(InvalidTaskToken) as exc:
            verify_task_request(
                path="/api/admin/tasks/clear",
                body=b'{"target": "both"}',
                signature=sig,
                timestamp=ts,
                secret=SECRET,
                now=1_000,
            )
        assert exc.value.reason == "signature_mismatch"

    def test_wrong_secret_fails(self):
        ts, sig = sign_task_request("/api/admin/tasks/clear", b"{}", secret=SECRET, ts=1_000)

        with pytest.raises(InvalidTaskToken) as exc:
            verify_task_request(
                path="/api/admin/tasks/clear",
                body=b"{}",
                signature=sig,
                timestamp=ts,
                secret="a-different-secret",
                now=1_000,
            )
        assert exc.value.reason == "signature_mismatch"

    def test_stale_timestamp_beyond_tolerance_fails(self):
        ts, sig = sign_task_request("/api/admin/tasks/clear", b"{}", secret=SECRET, ts=1_000)

        with pytest.raises(InvalidTaskToken) as exc:
            verify_task_request(
                path="/api/admin/tasks/clear",
                body=b"{}",
                signature=sig,
                timestamp=ts,
                secret=SECRET,
                now=1_000 + 301,
            )
        assert exc.value.reason == "stale_timestamp"

    def test_timestamp_ahead_of_tolerance_also_fails(self):
        """The window is +-300s, not just a max-age check."""
        ts, sig = sign_task_request("/api/admin/tasks/clear", b"{}", secret=SECRET, ts=1_000)

        with pytest.raises(InvalidTaskToken) as exc:
            verify_task_request(
                path="/api/admin/tasks/clear",
                body=b"{}",
                signature=sig,
                timestamp=ts,
                secret=SECRET,
                now=1_000 - 301,
            )
        assert exc.value.reason == "stale_timestamp"

    def test_timestamp_at_exactly_the_tolerance_edge_succeeds(self):
        ts, sig = sign_task_request("/api/admin/tasks/clear", b"{}", secret=SECRET, ts=1_000)

        verify_task_request(
            path="/api/admin/tasks/clear",
            body=b"{}",
            signature=sig,
            timestamp=ts,
            secret=SECRET,
            now=1_000 + 300,
        )

    def test_missing_signature_fails(self):
        ts, _ = sign_task_request("/api/admin/tasks/clear", b"{}", secret=SECRET, ts=1_000)

        with pytest.raises(InvalidTaskToken) as exc:
            verify_task_request(
                path="/api/admin/tasks/clear",
                body=b"{}",
                signature=None,
                timestamp=ts,
                secret=SECRET,
                now=1_000,
            )
        assert exc.value.reason == "no_signature"

    def test_missing_timestamp_fails(self):
        _, sig = sign_task_request("/api/admin/tasks/clear", b"{}", secret=SECRET, ts=1_000)

        with pytest.raises(InvalidTaskToken) as exc:
            verify_task_request(
                path="/api/admin/tasks/clear",
                body=b"{}",
                signature=sig,
                timestamp=None,
                secret=SECRET,
                now=1_000,
            )
        assert exc.value.reason == "no_timestamp"

    def test_non_numeric_timestamp_fails(self):
        _, sig = sign_task_request("/api/admin/tasks/clear", b"{}", secret=SECRET, ts=1_000)

        with pytest.raises(InvalidTaskToken) as exc:
            verify_task_request(
                path="/api/admin/tasks/clear",
                body=b"{}",
                signature=sig,
                timestamp="not-a-number",
                secret=SECRET,
                now=1_000,
            )
        assert exc.value.reason == "bad_timestamp"


# --------------------------------------------------------------------------
# consume_nonce
# --------------------------------------------------------------------------
class TestConsumeNonce:
    def test_first_use_returns_true(self, database):
        with database.session() as session:
            assert consume_nonce(session, AdminTaskNonce, "abc123") is True

    def test_second_use_of_the_same_nonce_returns_false(self, database):
        with database.session() as session:
            assert consume_nonce(session, AdminTaskNonce, "abc123") is True
            assert consume_nonce(session, AdminTaskNonce, "abc123") is False

    def test_distinct_nonces_are_independent(self, database):
        with database.session() as session:
            assert consume_nonce(session, AdminTaskNonce, "abc123") is True
            assert consume_nonce(session, AdminTaskNonce, "xyz789") is True


# --------------------------------------------------------------------------
# record_audit
# --------------------------------------------------------------------------
class TestRecordAudit:
    def test_writes_a_row_with_the_given_fields(self, database):
        with database.session() as session:
            record_audit(
                session,
                AdminAuditLog,
                path="/api/admin/tasks/clear",
                outcome=AuditOutcome.allow,
                reason="ok",
                detail={"rows": 3},
            )
            session.commit()

        with database.session() as session:
            row = session.query(AdminAuditLog).one()
            assert row.path == "/api/admin/tasks/clear"
            assert row.outcome == "allow"
            assert row.reason == "ok"
            assert row.detail == {"rows": 3}
            assert isinstance(row.occurred_at, datetime)

    def test_accepts_a_plain_string_outcome(self, database):
        with database.session() as session:
            record_audit(
                session, AdminAuditLog, path="/x", outcome="deny", reason="signature_mismatch"
            )
            session.commit()

        with database.session() as session:
            assert session.query(AdminAuditLog).one().outcome == "deny"


# --------------------------------------------------------------------------
# make_task_router: HTTP-level behaviour via TestClient
# --------------------------------------------------------------------------
def increment(session):
    counter = session.get(Counter, 1)
    counter.value += 1
    session.commit()
    return {"value": counter.value}


def boom(session):
    raise RuntimeError("task exploded")


def build_app(database: Database, *, tasks=None) -> FastAPI:
    app = FastAPI()
    app.state.database = database
    app.include_router(
        make_task_router(
            tasks if tasks is not None else {"increment": increment, "boom": boom},
            db=database.get_db,
            secret=SECRET,
            nonce_model=AdminTaskNonce,
            audit_model=AdminAuditLog,
        )
    )
    return app


@pytest.fixture
def seeded(database):
    with database.session() as session:
        session.add(Counter(id=1, value=0))
        session.commit()
    return database


def sign(path: str, body: bytes = b"") -> dict[str, str]:
    ts, sig = sign_task_request(path, body, secret=SECRET)
    return {"X-Admin-Task-Timestamp": ts, "X-Admin-Task-Signature": sig}


class TestTaskRouter:
    def test_valid_token_runs_the_task_and_returns_its_result(self, seeded):
        app = build_app(seeded)
        client = TestClient(app)
        path = "/api/admin/tasks/increment"

        response = client.post(path, headers=sign(path))

        assert response.status_code == 200
        assert response.json() == {"task": "increment", "result": {"value": 1}}
        with seeded.session() as session:
            assert session.get(Counter, 1).value == 1

    def test_valid_token_writes_an_allow_audit_row(self, seeded):
        app = build_app(seeded)
        client = TestClient(app)
        path = "/api/admin/tasks/increment"

        client.post(path, headers=sign(path))

        with seeded.session() as session:
            row = session.query(AdminAuditLog).one()
            assert row.outcome == "allow"
            assert row.path == path

    def test_missing_token_is_rejected_and_does_not_run_the_task(self, seeded):
        app = build_app(seeded)
        client = TestClient(app)

        response = client.post("/api/admin/tasks/increment")

        assert response.status_code == 401
        with seeded.session() as session:
            assert session.get(Counter, 1).value == 0
            assert session.query(AdminAuditLog).one().outcome == "deny"

    def test_token_minted_for_a_different_task_path_is_rejected(self, seeded):
        app = build_app(seeded)
        client = TestClient(app)

        headers = sign("/api/admin/tasks/boom")
        response = client.post("/api/admin/tasks/increment", headers=headers)

        assert response.status_code == 401
        with seeded.session() as session:
            assert session.get(Counter, 1).value == 0

    def test_replaying_the_same_token_the_second_time_is_rejected(self, seeded):
        app = build_app(seeded)
        client = TestClient(app)
        path = "/api/admin/tasks/increment"
        headers = sign(path)

        first = client.post(path, headers=headers)
        second = client.post(path, headers=headers)

        assert first.status_code == 200
        assert second.status_code == 409
        with seeded.session() as session:
            # Only the first, genuinely-run request incremented the counter.
            assert session.get(Counter, 1).value == 1

    def test_replay_writes_a_nonce_reused_deny_audit_row(self, seeded):
        app = build_app(seeded)
        client = TestClient(app)
        path = "/api/admin/tasks/increment"
        headers = sign(path)

        client.post(path, headers=headers)
        client.post(path, headers=headers)

        with seeded.session() as session:
            reasons = [row.reason for row in session.query(AdminAuditLog).all()]
        assert reasons == ["ok", "nonce_reused"]

    def test_task_exception_returns_500_and_rolls_back(self, seeded):
        app = build_app(seeded)
        client = TestClient(app)
        path = "/api/admin/tasks/boom"

        response = client.post(path, headers=sign(path))

        assert response.status_code == 500
        with seeded.session() as session:
            row = session.query(AdminAuditLog).one()
            assert row.outcome == "deny"
            assert row.reason == "task_error"
            assert "task exploded" in row.detail["error"]

    def test_stale_timestamp_is_rejected_with_401(self, seeded):
        app = build_app(seeded)
        client = TestClient(app)
        path = "/api/admin/tasks/increment"
        ts, sig = sign_task_request(path, b"", secret=SECRET, ts=1_000)

        response = client.post(
            path,
            headers={"X-Admin-Task-Timestamp": ts, "X-Admin-Task-Signature": sig},
        )

        assert response.status_code == 401

    def test_unknown_task_name_is_a_plain_404(self, seeded):
        app = build_app(seeded)
        client = TestClient(app)
        path = "/api/admin/tasks/does-not-exist"

        response = client.post(path, headers=sign(path))

        assert response.status_code == 404
