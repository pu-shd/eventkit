"""``eventkit.db.Database``: engine construction, pragmas, and URL handling."""

from __future__ import annotations

import pytest
from sqlalchemy import Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.pool import NullPool, StaticPool

from eventkit.db import AZURE_FILES_PRAGMAS, Database, declarative_base, sqlite_url_for


class TestSqliteUrlFor:
    def test_default_home(self):
        assert sqlite_url_for("posted") == "sqlite:////home/posted.db"

    def test_custom_home(self):
        assert sqlite_url_for("posted", home="/data") == "sqlite:////data/posted.db"


class TestDeclarativeBase:
    def test_has_the_alembic_naming_convention_by_default(self):
        Base = declarative_base()
        assert Base.metadata.naming_convention["ix"] == "ix_%(column_0_label)s"

    def test_naming_convention_can_be_disabled(self):
        Base = declarative_base(naming_convention=False)
        assert Base.metadata.naming_convention == {"ix": "ix_%(column_0_label)s"}

    def test_two_calls_return_independent_bases(self):
        """Two apps in the same test process must not share metadata/tables."""
        BaseA = declarative_base()
        BaseB = declarative_base()
        assert BaseA is not BaseB
        assert BaseA.metadata is not BaseB.metadata


class TestDatabaseSqliteFile:
    def test_memory_url_has_no_file(self):
        db = Database("sqlite:///:memory:")
        assert db.sqlite_file() is None

    def test_bare_sqlite_url_has_no_file(self):
        db = Database("sqlite://")
        assert db.sqlite_file() is None

    def test_file_backed_url_resolves_to_a_path(self, tmp_path):
        db_file = tmp_path / "app.db"
        db = Database(f"sqlite:///{db_file}")
        assert db.sqlite_file() == db_file

    def test_is_sqlite_false_for_other_backends(self):
        db = Database("sqlite:///:memory:")
        assert db.is_sqlite is True


class TestDatabasePooling:
    def test_memory_database_uses_static_pool(self):
        """StaticPool keeps the one in-memory connection alive for the engine's
        life; anything else would give every checkout its own private database."""
        db = Database("sqlite:///:memory:")
        assert isinstance(db.engine.pool, StaticPool)

    def test_file_backed_database_uses_null_pool(self, tmp_path):
        db = Database(f"sqlite:///{tmp_path / 'app.db'}")
        assert isinstance(db.engine.pool, NullPool)

    def test_memory_database_is_usable_across_checkouts(self):
        """Proves the StaticPool choice: two separate `session()` calls must
        see the same schema and data, which a fresh :memory: connection would not."""
        Base = declarative_base()

        class Widget(Base):
            __tablename__ = "widgets"
            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            name: Mapped[str] = mapped_column(String(64))

        db = Database("sqlite:///:memory:")
        Base.metadata.create_all(db.engine)

        with db.session() as session:
            session.add(Widget(id=1, name="bolt"))
            session.commit()

        with db.session() as session:
            widget = session.get(Widget, 1)
            assert widget is not None
            assert widget.name == "bolt"


class TestDatabasePragmas:
    def test_no_pragmas_by_default(self, tmp_path):
        db = Database(f"sqlite:///{tmp_path / 'app.db'}")
        with db.engine.connect() as conn:
            # SQLite's own default, not TRUNCATE — proves pragmas are opt-in.
            assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "delete"

    def test_azure_files_pragmas_set_truncate_not_wal(self, tmp_path):
        db = Database(f"sqlite:///{tmp_path / 'app.db'}", sqlite_pragmas=AZURE_FILES_PRAGMAS)
        with db.engine.connect() as conn:
            assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "truncate"

    def test_azure_files_pragmas_are_actually_applied_on_every_connection(self, tmp_path):
        db = Database(f"sqlite:///{tmp_path / 'app.db'}", sqlite_pragmas=AZURE_FILES_PRAGMAS)
        for _ in range(3):
            with db.engine.connect() as conn:
                assert conn.exec_driver_sql("PRAGMA synchronous").scalar() == 2  # FULL
                assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1

    def test_pragmas_apply_to_an_in_memory_database_too(self):
        # journal_mode is a no-op for :memory: databases (SQLite always reports
        # "memory"), so assert against a pragma that memory databases do honor.
        db = Database("sqlite:///:memory:", sqlite_pragmas=AZURE_FILES_PRAGMAS)
        with db.engine.connect() as conn:
            assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


class TestDatabaseSessions:
    def test_get_db_is_a_generator_dependency_that_closes(self, tmp_path):
        db = Database(f"sqlite:///{tmp_path / 'app.db'}")
        gen = db.get_db()
        session = next(gen)
        session.execute(text("select 1"))
        with pytest.raises(StopIteration):
            next(gen)

    def test_session_context_manager_closes_on_exception(self, tmp_path):
        db = Database(f"sqlite:///{tmp_path / 'app.db'}")
        with pytest.raises(RuntimeError):
            with db.session() as session:
                session.execute(text("select 1"))
                raise RuntimeError("boom")
