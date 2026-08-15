"""Alembic wiring: init, upgrade, stamp, the filelock, and failure handling.

Uses a throwaway "app" package plus two hand-written revisions (never the
shipped ``alembic_template``) so each test controls exactly what migration code
runs, rather than depending on a real application's schema.
"""

from __future__ import annotations

import textwrap

import pytest
from filelock import FileLock
from sqlalchemy import inspect

from eventkit.db import Database
from eventkit.db.migrate import (
    MigrationError,
    assert_at_head,
    current_revision,
    ensure_columns,
    init_migrations,
    lifespan_migrations,
    stamp,
    upgrade_to_head,
)

_REV1 = textwrap.dedent(
    '''
    revision = "rev1"
    down_revision = None
    branch_labels = None
    depends_on = None

    from alembic import op
    import sqlalchemy as sa


    def upgrade() -> None:
        op.create_table(
            "widgets",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("name", sa.String(64), nullable=False),
        )


    def downgrade() -> None:
        op.drop_table("widgets")
    '''
)

_REV2 = textwrap.dedent(
    '''
    revision = "rev2"
    down_revision = "rev1"
    branch_labels = None
    depends_on = None

    from alembic import op
    import sqlalchemy as sa


    def upgrade() -> None:
        op.add_column("widgets", sa.Column("extra", sa.String(64), nullable=True))


    def downgrade() -> None:
        op.drop_column("widgets", "extra")
    '''
)

_BROKEN_REV = textwrap.dedent(
    '''
    revision = "rev1"
    down_revision = None
    branch_labels = None
    depends_on = None


    def upgrade() -> None:
        raise RuntimeError("simulated migration failure, before any DDL runs")


    def downgrade() -> None:
        pass
    '''
)


@pytest.fixture
def app_package(tmp_path, monkeypatch):
    """A throwaway importable package exposing `target_metadata = None`."""
    package_dir = tmp_path / "throwaway_app"
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("target_metadata = None\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    return "throwaway_app"


@pytest.fixture
def migrations_dir(tmp_path, app_package):
    init_migrations(tmp_path, package=app_package)
    versions = tmp_path / "migrations" / "versions"
    (versions / "rev1_create_widgets.py").write_text(_REV1)
    (versions / "rev2_add_extra.py").write_text(_REV2)
    return tmp_path / "migrations"


@pytest.fixture
def broken_migrations_dir(tmp_path, app_package):
    init_migrations(tmp_path, package=app_package)
    versions = tmp_path / "migrations" / "versions"
    (versions / "rev1_broken.py").write_text(_BROKEN_REV)
    return tmp_path / "migrations"


class TestInitMigrations:
    def test_scaffolds_migrations_and_alembic_ini(self, tmp_path, app_package):
        init_migrations(tmp_path, package=app_package)
        assert (tmp_path / "migrations" / "env.py").exists()
        assert (tmp_path / "migrations" / "script.py.mako").exists()
        assert (tmp_path / "alembic.ini").exists()

    def test_wires_env_py_to_the_given_package(self, tmp_path, app_package):
        init_migrations(tmp_path, package=app_package)
        env_source = (tmp_path / "migrations" / "env.py").read_text()
        assert "__EVENTKIT_PACKAGE__" not in env_source
        assert f"from {app_package} import target_metadata" in env_source

    def test_refuses_to_overwrite_an_existing_migrations_directory(self, tmp_path, app_package):
        init_migrations(tmp_path, package=app_package)
        with pytest.raises(MigrationError):
            init_migrations(tmp_path, package=app_package)


class TestUpgradeToHead:
    def test_empty_database_upgrades_to_head(self, tmp_path, migrations_dir):
        db = Database(f"sqlite:///{tmp_path / 'app.db'}")
        assert current_revision(db) is None

        result = upgrade_to_head(db, migrations_dir=migrations_dir)

        assert result == "rev2"
        assert current_revision(db) == "rev2"
        columns = {c["name"] for c in inspect(db.engine).get_columns("widgets")}
        assert columns == {"id", "name", "extra"}

    def test_is_idempotent_once_at_head(self, tmp_path, migrations_dir):
        db = Database(f"sqlite:///{tmp_path / 'app.db'}")
        first = upgrade_to_head(db, migrations_dir=migrations_dir)
        second = upgrade_to_head(db, migrations_dir=migrations_dir)
        assert first == second == "rev2"

    def test_a_legacy_stamped_database_upgrades_only_the_remaining_revisions(
        self, tmp_path, migrations_dir
    ):
        """Simulates adopting a database the hand-rolled predecessor migrator
        already created: the `widgets` table exists (without `extra`), matching
        rev1, before Alembic has ever touched this file."""
        db_path = tmp_path / "legacy.db"
        db = Database(f"sqlite:///{db_path}")
        with db.engine.begin() as conn:
            conn.exec_driver_sql(
                "CREATE TABLE widgets (id INTEGER PRIMARY KEY, name VARCHAR(64) NOT NULL)"
            )

        stamp(db, "rev1", migrations_dir=migrations_dir)
        assert current_revision(db) == "rev1"

        result = upgrade_to_head(db, migrations_dir=migrations_dir)

        assert result == "rev2"
        columns = {c["name"] for c in inspect(db.engine).get_columns("widgets")}
        assert columns == {"id", "name", "extra"}

    def test_an_unstamped_legacy_database_fails_loudly_instead_of_silently(
        self, tmp_path, migrations_dir
    ):
        """Without the stamp above, upgrading tries to re-run rev1's
        `CREATE TABLE widgets`, which fails because it already exists. This is
        exactly the scenario `stamp()` exists to avoid, made explicit."""
        db_path = tmp_path / "legacy_unstamped.db"
        db = Database(f"sqlite:///{db_path}")
        with db.engine.begin() as conn:
            conn.exec_driver_sql(
                "CREATE TABLE widgets (id INTEGER PRIMARY KEY, name VARCHAR(64) NOT NULL)"
            )

        with pytest.raises(MigrationError, match="Migration to head failed"):
            upgrade_to_head(db, migrations_dir=migrations_dir)

    def test_a_failed_migration_leaves_no_version_row(self, tmp_path, broken_migrations_dir):
        db = Database(f"sqlite:///{tmp_path / 'app.db'}")

        with pytest.raises(MigrationError, match="must not start"):
            upgrade_to_head(db, migrations_dir=broken_migrations_dir)

        assert current_revision(db) is None

    def test_snapshots_the_sqlite_file_before_migrating(self, tmp_path, migrations_dir):
        db_path = tmp_path / "app.db"
        db = Database(f"sqlite:///{db_path}")

        upgrade_to_head(db, migrations_dir=migrations_dir)

        backups = list(tmp_path.glob("app.db.pre-*.bak"))
        assert len(backups) == 1

    def test_backup_first_false_skips_the_snapshot(self, tmp_path, migrations_dir):
        db_path = tmp_path / "app.db"
        db = Database(f"sqlite:///{db_path}")

        upgrade_to_head(db, migrations_dir=migrations_dir, backup_first=False)

        assert list(tmp_path.glob("app.db.pre-*.bak")) == []

    def test_concurrent_upgrade_is_blocked_by_the_filelock(self, tmp_path, migrations_dir):
        db = Database(f"sqlite:///{tmp_path / 'app.db'}")
        holder = FileLock(str(migrations_dir / ".migrate.lock"))
        holder.acquire()
        try:
            with pytest.raises(MigrationError, match="Could not acquire the migration lock"):
                upgrade_to_head(db, migrations_dir=migrations_dir, lock_timeout_s=1)
        finally:
            holder.release()

        # Once released, the same call succeeds.
        assert upgrade_to_head(db, migrations_dir=migrations_dir) == "rev2"

    def test_migration_runs_through_the_databases_own_engine_and_pragmas(
        self, tmp_path, migrations_dir
    ):
        """The migration must see the same pragmas the app would — otherwise a
        SQLite database migrated under WAL and served under TRUNCATE (or vice
        versa) is a real footgun, not a hypothetical one."""
        from eventkit.db import AZURE_FILES_PRAGMAS

        db = Database(
            f"sqlite:///{tmp_path / 'app.db'}",
            sqlite_pragmas=AZURE_FILES_PRAGMAS,
        )
        upgrade_to_head(db, migrations_dir=migrations_dir)
        with db.engine.connect() as conn:
            assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "truncate"


class TestAssertAtHead:
    def test_passes_when_at_head(self, tmp_path, migrations_dir):
        db = Database(f"sqlite:///{tmp_path / 'app.db'}")
        upgrade_to_head(db, migrations_dir=migrations_dir)
        assert_at_head(db, migrations_dir=migrations_dir)  # does not raise

    def test_raises_when_behind(self, tmp_path, migrations_dir):
        db = Database(f"sqlite:///{tmp_path / 'app.db'}")
        with pytest.raises(MigrationError, match="Run upgrade_to_head"):
            assert_at_head(db, migrations_dir=migrations_dir)


class TestLifespanMigrations:
    def test_rejects_an_unknown_mode_at_construction_not_lazily(self, tmp_path, migrations_dir):
        db = Database(f"sqlite:///{tmp_path / 'app.db'}")
        with pytest.raises(MigrationError):
            lifespan_migrations(db, migrations_dir=migrations_dir, mode="bogus")

    async def test_upgrade_mode_migrates_on_startup(self, tmp_path, migrations_dir):
        db = Database(f"sqlite:///{tmp_path / 'app.db'}")
        lifespan = lifespan_migrations(db, migrations_dir=migrations_dir)
        async with lifespan(None):
            pass
        assert current_revision(db) == "rev2"

    async def test_check_mode_raises_if_behind_head(self, tmp_path, migrations_dir):
        db = Database(f"sqlite:///{tmp_path / 'app.db'}")
        lifespan = lifespan_migrations(db, migrations_dir=migrations_dir, mode="check")
        with pytest.raises(MigrationError):
            async with lifespan(None):
                pass

    async def test_off_mode_touches_nothing(self, tmp_path, migrations_dir):
        db = Database(f"sqlite:///{tmp_path / 'app.db'}")
        lifespan = lifespan_migrations(db, migrations_dir=migrations_dir, mode="off")
        async with lifespan(None):
            pass
        assert current_revision(db) is None


class TestEnsureColumns:
    def test_adds_a_missing_column(self, tmp_path):
        db = Database(f"sqlite:///{tmp_path / 'app.db'}")
        with db.engine.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE widgets (id INTEGER PRIMARY KEY)")

        ensure_columns(db.engine, "widgets", {"name": "VARCHAR(64)"})

        columns = {c["name"] for c in inspect(db.engine).get_columns("widgets")}
        assert columns == {"id", "name"}

    def test_is_a_no_op_for_columns_that_already_exist(self, tmp_path):
        db = Database(f"sqlite:///{tmp_path / 'app.db'}")
        with db.engine.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE widgets (id INTEGER PRIMARY KEY)")

        ensure_columns(db.engine, "widgets", {"id": "INTEGER"})  # would fail if re-added

        columns = {c["name"] for c in inspect(db.engine).get_columns("widgets")}
        assert columns == {"id"}
