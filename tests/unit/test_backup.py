"""Tests for eventkit.backup: the priorities the testing plan
calls out by name — round trip through TestClient, restore rejecting a
foreign manifest.app_name, and the whole payload being validated before the
first DELETE — plus the router hardening this module adds on top
(enable_restore gating, the confirm phrase, the revision-force gate, and the
auto-snapshot before a real restore)."""

from __future__ import annotations

import io
import json
from datetime import datetime

import pytest
from fastapi import FastAPI
from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from starlette.testclient import TestClient

from eventkit.auth import AllowList, EasyAuth
from eventkit.backup import (
    BackupSpec,
    BackupValidationError,
    ForeignBackupError,
    TableSpec,
    dump,
    make_backup_router,
    restore,
)
from eventkit.db import Database, declarative_base

Base = declarative_base()


class Widget(Base):
    __tablename__ = "widgets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    secret: Mapped[str] = mapped_column(String(100))


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    widget_id: Mapped[int] = mapped_column(ForeignKey("widgets.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    placed_at: Mapped[datetime] = mapped_column()


class Inventory(Base):
    __tablename__ = "inventory"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(50))


def _redact_secret(row: dict) -> dict:
    return {**row, "secret": "REDACTED"}


def _seed_default_sku(session) -> None:
    session.add(Inventory(sku="DEFAULT-SKU"))


def make_spec(**overrides) -> BackupSpec:
    defaults = {
        "app_name": "widgetapp",
        "tables": [
            TableSpec(model=Widget, key="widgets", order=0, redact=_redact_secret),
            TableSpec(model=Order, key="orders", order=1),
            TableSpec(
                model=Inventory, key="inventory", order=2, seed_if_missing=_seed_default_sku
            ),
        ],
        "required_keys": {"widgets"},
    }
    defaults.update(overrides)
    return BackupSpec(**defaults)


@pytest.fixture
def database():
    db = Database("sqlite:///:memory:")
    Base.metadata.create_all(db.engine)
    try:
        yield db
    finally:
        db.engine.dispose()


@pytest.fixture
def seeded(database):
    with database.session() as session:
        session.add_all(
            [
                Widget(id=1, name="Sprocket", secret="s3cr3t"),
                Widget(id=2, name="Cog", secret="0th3r"),
                Order(id=1, widget_id=1, quantity=3, placed_at=datetime(2030, 6, 1, 12, 0, 0)),
            ]
        )
        session.commit()
    return database


class TestDump:
    def test_manifest_fields(self, seeded):
        with seeded.session() as session:
            payload = dump(session, make_spec())

        manifest = payload["manifest"]
        assert manifest["app_name"] == "widgetapp"
        assert manifest["app_version"] == "0.0.0"
        assert manifest["alembic_revision"] is None
        assert manifest["row_counts"] == {"widgets": 2, "orders": 1, "inventory": 0}

    def test_applies_redact(self, seeded):
        with seeded.session() as session:
            payload = dump(session, make_spec())

        assert all(row["secret"] == "REDACTED" for row in payload["tables"]["widgets"])

    def test_datetime_columns_are_json_safe(self, seeded):
        with seeded.session() as session:
            payload = dump(session, make_spec())

        # Must survive a real json.dumps, since the HTTP endpoint does exactly this.
        encoded = json.dumps(payload)
        decoded = json.loads(encoded)
        assert decoded["tables"]["orders"][0]["placed_at"] == "2030-06-01T12:00:00"

    def test_manifest_extra_is_merged(self, seeded):
        with seeded.session() as session:
            payload = dump(session, make_spec(), manifest_extra={"note": "pre-deploy"})

        assert payload["manifest"]["note"] == "pre-deploy"


class TestRestoreRoundTrip:
    def test_round_trip_reproduces_row_counts(self, seeded):
        spec = make_spec()
        with seeded.session() as session:
            payload = dump(session, spec)

        with seeded.session() as session:
            session.query(Order).delete()
            session.query(Widget).delete()
            session.commit()

        with seeded.session() as session:
            manifest = restore(session, spec, payload)
            session.commit()

        assert manifest.row_counts == {"widgets": 2, "orders": 1, "inventory": 0}
        with seeded.session() as session:
            assert session.query(Widget).count() == 2
            assert session.query(Order).count() == 1

    def test_round_trip_preserves_redacted_secret_not_the_original(self, seeded):
        """Restoring a redacted dump writes back the redacted value: redact()
        is a one-way transform for what leaves the database, not a
        reversible encoding."""
        spec = make_spec()
        with seeded.session() as session:
            payload = dump(session, spec)

        with seeded.session() as session:
            restore(session, spec, payload)
            session.commit()

        with seeded.session() as session:
            widget = session.get(Widget, 1)
            assert widget.secret == "REDACTED"

    def test_seed_if_missing_runs_when_restored_table_is_empty(self, seeded):
        spec = make_spec()
        with seeded.session() as session:
            payload = dump(session, spec)  # inventory is empty in this payload

        with seeded.session() as session:
            restore(session, spec, payload)
            session.commit()

        with seeded.session() as session:
            skus = [row.sku for row in session.query(Inventory).all()]
        assert skus == ["DEFAULT-SKU"]

    def test_seed_if_missing_does_not_run_when_restored_table_has_rows(self, seeded):
        spec = make_spec()
        with seeded.session() as session:
            session.add(Inventory(id=1, sku="REAL-SKU"))
            session.commit()
            payload = dump(session, spec)

        with seeded.session() as session:
            restore(session, spec, payload)
            session.commit()

        with seeded.session() as session:
            skus = [row.sku for row in session.query(Inventory).all()]
        assert skus == ["REAL-SKU"]


class TestRestoreValidation:
    def test_rejects_foreign_app_name(self, seeded):
        spec = make_spec()
        with seeded.session() as session:
            payload = dump(session, spec)
        payload["manifest"]["app_name"] = "some-other-app"

        with seeded.session() as session, pytest.raises(ForeignBackupError):
            restore(session, spec, payload)

    def test_missing_manifest_raises(self, seeded):
        with seeded.session() as session, pytest.raises(BackupValidationError):
            restore(session, make_spec(), {"tables": {}})

    def test_missing_required_table_raises(self, seeded):
        spec = make_spec()
        with seeded.session() as session:
            payload = dump(session, spec)
        del payload["tables"]["widgets"]

        with seeded.session() as session, pytest.raises(BackupValidationError, match="widgets"):
            restore(session, spec, payload)

    def test_unknown_table_key_raises(self, seeded):
        spec = make_spec()
        with seeded.session() as session:
            payload = dump(session, spec)
        payload["tables"]["extra_table"] = []

        with seeded.session() as session, pytest.raises(BackupValidationError, match="extra_table"):
            restore(session, spec, payload)

    def test_optional_table_absent_from_payload_is_left_untouched(self, seeded):
        """`orders` is not in `required_keys`; an old backup missing it entirely
        must not wipe the live `orders` table."""
        spec = make_spec()
        with seeded.session() as session:
            payload = dump(session, spec)
        del payload["tables"]["orders"]

        with seeded.session() as session:
            restore(session, spec, payload)
            session.commit()

        with seeded.session() as session:
            assert session.query(Order).count() == 1

    def test_whole_payload_validated_before_first_delete(self, seeded):
        """A malformed row in `orders` (validated second) must not leave
        `widgets` (validated and would be deleted first) half-wiped."""
        spec = make_spec()
        with seeded.session() as session:
            payload = dump(session, spec)
        payload["tables"]["orders"][0]["bogus_column"] = "not a real column"

        with (
            seeded.session() as session,
            pytest.raises(BackupValidationError, match="bogus_column"),
        ):
            restore(session, spec, payload)

        with seeded.session() as session:
            assert session.query(Widget).count() == 2
            assert session.query(Order).count() == 1

    def test_dry_run_validates_without_mutating(self, seeded):
        spec = make_spec()
        with seeded.session() as session:
            payload = dump(session, spec)
        payload["tables"]["widgets"] = []

        with seeded.session() as session:
            manifest = restore(session, spec, payload, dry_run=True)

        assert manifest.row_counts["widgets"] == 2  # from the original dump, untouched
        with seeded.session() as session:
            assert session.query(Widget).count() == 2  # dry run did not delete anything

    def test_row_type_mismatch_raises_before_delete(self, seeded):
        spec = make_spec()
        with seeded.session() as session:
            payload = dump(session, spec)
        payload["tables"]["orders"][0]["placed_at"] = "not-a-real-timestamp"

        with seeded.session() as session, pytest.raises(BackupValidationError):
            restore(session, spec, payload)

        with seeded.session() as session:
            assert session.query(Widget).count() == 2


# --------------------------------------------------------------------------
# Router: HTTP-level behaviour via TestClient
# --------------------------------------------------------------------------
ADMIN = "admin@example.edu"


def build_app(database: Database, *, enable_restore: bool = True) -> FastAPI:
    auth = EasyAuth(AllowList([ADMIN]), dev_principal=ADMIN)
    spec = make_spec()
    app = FastAPI()
    app.state.database = database
    app.state.auth = auth
    app.include_router(
        make_backup_router(
            spec,
            db=database.get_db,
            principal=auth.require,
            enable_restore=lambda: enable_restore,
            database=database,
        )
    )
    return app


class TestBackupRouter:
    def test_get_db_backup_returns_attachment_with_expected_headers(self, seeded):
        app = build_app(seeded)
        client = TestClient(app)

        response = client.get("/api/admin/db-backup")

        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert "attachment" in response.headers["content-disposition"]
        payload = response.json()
        assert payload["manifest"]["app_name"] == "widgetapp"
        assert payload["manifest"]["row_counts"]["widgets"] == 2

    def test_round_trip_through_test_client(self, seeded):
        app = build_app(seeded)
        client = TestClient(app)

        backup_bytes = client.get("/api/admin/db-backup").content

        with seeded.session() as session:
            session.query(Order).delete()
            session.query(Widget).delete()
            session.commit()

        response = client.post(
            "/api/admin/db-restore?force=1",
            files={"file": ("backup.json", io.BytesIO(backup_bytes), "application/json")},
            data={"confirm": "RESTORE"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["manifest"]["row_counts"]["widgets"] == 2
        with seeded.session() as session:
            assert session.query(Widget).count() == 2
            assert session.query(Order).count() == 1

    def test_restore_disabled_by_default_is_forbidden(self, seeded):
        app = build_app(seeded, enable_restore=False)
        client = TestClient(app)
        backup_bytes = client.get("/api/admin/db-backup").content

        response = client.post(
            "/api/admin/db-restore?force=1",
            files={"file": ("backup.json", io.BytesIO(backup_bytes), "application/json")},
            data={"confirm": "RESTORE"},
        )

        assert response.status_code == 403

    def test_restore_requires_the_confirm_phrase(self, seeded):
        app = build_app(seeded)
        client = TestClient(app)
        backup_bytes = client.get("/api/admin/db-backup").content

        response = client.post(
            "/api/admin/db-restore?force=1",
            files={"file": ("backup.json", io.BytesIO(backup_bytes), "application/json")},
            data={"confirm": "please"},
        )

        assert response.status_code == 400
        with seeded.session() as session:
            assert session.query(Widget).count() == 2  # nothing was touched

    def test_restore_rejects_invalid_json(self, seeded):
        app = build_app(seeded)
        client = TestClient(app)

        response = client.post(
            "/api/admin/db-restore?force=1",
            files={"file": ("backup.json", io.BytesIO(b"not json"), "application/json")},
            data={"confirm": "RESTORE"},
        )

        assert response.status_code == 400

    def test_restore_rejects_a_json_array(self, seeded):
        app = build_app(seeded)
        client = TestClient(app)

        response = client.post(
            "/api/admin/db-restore?force=1",
            files={"file": ("backup.json", io.BytesIO(b"[1, 2, 3]"), "application/json")},
            data={"confirm": "RESTORE"},
        )

        assert response.status_code == 400

    def test_restore_rejects_foreign_app_name(self, seeded):
        app = build_app(seeded)
        client = TestClient(app)
        payload = json.loads(client.get("/api/admin/db-backup").content)
        payload["manifest"]["app_name"] = "some-other-app"

        response = client.post(
            "/api/admin/db-restore?force=1",
            files={
                "file": (
                    "backup.json",
                    io.BytesIO(json.dumps(payload).encode()),
                    "application/json",
                )
            },
            data={"confirm": "RESTORE"},
        )

        assert response.status_code == 409

    def test_restore_without_force_is_blocked_by_revision_mismatch(self, seeded):
        """The live database has never adopted Alembic (revision is `None`);
        a manifest claiming a real revision must be rejected unless forced."""
        app = build_app(seeded)
        client = TestClient(app)
        payload = json.loads(client.get("/api/admin/db-backup").content)
        payload["manifest"]["alembic_revision"] = "0001_initial"

        response = client.post(
            "/api/admin/db-restore",
            files={
                "file": (
                    "backup.json",
                    io.BytesIO(json.dumps(payload).encode()),
                    "application/json",
                )
            },
            data={"confirm": "RESTORE"},
        )

        assert response.status_code == 409
        with seeded.session() as session:
            assert session.query(Widget).count() == 2  # rejected before touching data

    def test_restore_with_force_bypasses_revision_mismatch(self, seeded):
        app = build_app(seeded)
        client = TestClient(app)
        payload = json.loads(client.get("/api/admin/db-backup").content)
        payload["manifest"]["alembic_revision"] = "0001_initial"

        response = client.post(
            "/api/admin/db-restore?force=1",
            files={
                "file": (
                    "backup.json",
                    io.BytesIO(json.dumps(payload).encode()),
                    "application/json",
                )
            },
            data={"confirm": "RESTORE"},
        )

        assert response.status_code == 200, response.text

    def test_restore_snapshots_the_sqlite_file_first(self, tmp_path):
        db_path = tmp_path / "widgetapp.db"
        database = Database(f"sqlite:///{db_path}")
        Base.metadata.create_all(database.engine)
        with database.session() as session:
            session.add(Widget(id=1, name="Sprocket", secret="s3cr3t"))
            session.commit()

        app = build_app(database)
        client = TestClient(app)
        backup_bytes = client.get("/api/admin/db-backup").content

        response = client.post(
            "/api/admin/db-restore?force=1",
            files={"file": ("backup.json", io.BytesIO(backup_bytes), "application/json")},
            data={"confirm": "RESTORE"},
        )

        assert response.status_code == 200, response.text
        snapshots = list(tmp_path.glob("widgetapp.db.pre-restore-*.bak"))
        assert len(snapshots) == 1
        database.engine.dispose()

    def test_validate_endpoint_is_a_dry_run_with_a_diff(self, seeded):
        app = build_app(seeded)
        client = TestClient(app)
        backup_bytes = client.get("/api/admin/db-backup").content

        with seeded.session() as session:
            session.add(Widget(id=3, name="Extra", secret="x"))
            session.commit()

        response = client.post(
            "/api/admin/db-restore/validate",
            files={"file": ("backup.json", io.BytesIO(backup_bytes), "application/json")},
        )

        assert response.status_code == 200, response.text
        diff = response.json()["diff"]
        assert diff["widgets"] == {"current_rows": 3, "uploaded_rows": 2}
        with seeded.session() as session:
            assert session.query(Widget).count() == 3  # validate never mutates

    def test_outsider_principal_is_denied(self, seeded):
        """A caller presenting Easy Auth headers is routed through the real
        header check, not the dev bypass -- the bypass used by every other
        test in this class only stands in for *absent* headers."""
        app = build_app(seeded)
        client = TestClient(app, follow_redirects=False)

        response = client.get(
            "/api/admin/db-backup",
            headers={"X-MS-CLIENT-PRINCIPAL-NAME": "outsider@example.edu"},
        )

        assert response.status_code == 401
