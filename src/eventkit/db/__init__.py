"""Engine, session, and pragma management for a single application database.

Replaces the pattern in ``ticketed/backend/database.py`` and
``posted/backend/database.py``: a module-level ``engine = create_engine(...)`` plus
``Base.metadata.create_all(engine)`` executed at *import* time. That forces every
app's ``tests/conftest.py`` to set ``DATABASE_URL`` before importing the
application module at all — the exact env-vars-before-import dance
:mod:`eventkit.testing` exists to delete. :class:`Database` is constructed
explicitly, in ``lifespan`` (see :mod:`eventkit.db.migrate`), from
``get_settings()``.

Import weight: this module needs SQLAlchemy, so it is not in
``tests/unit/test_import_weight.py``'s light-module list. ``eventkit.db`` is only
pulled in by apps that install the ``db`` extra.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool, StaticPool

__all__ = [
    "AZURE_FILES_PRAGMAS",
    "NAMING_CONVENTION",
    "Database",
    "declarative_base",
    "sqlite_url_for",
]

#: Required for Alembic's `render_as_batch` to name (and therefore drop) SQLite
#: constraints. A bare `DeclarativeBase` subclass leaves constraints unnamed, so
#: a batch migration that drops one has nothing to reference.
NAMING_CONVENTION: Mapping[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

#: SQLite pragmas for a database file on an Azure Files (SMB) mount, e.g. the
#: App Service `/home` persistent share. Deliberately not the SQLite defaults:
#:
#: - `journal_mode=TRUNCATE`, never `wal`. WAL needs a shared-memory mmap that
#:   SMB does not provide; on Azure Files that surfaces as an intermittent
#:   `disk I/O error` under concurrent access, not a clean failure.
#: - `synchronous=FULL` because SMB can reorder writes that a local disk would not.
#: - `busy_timeout=15000` because SMB latency makes the 5s SQLite default fire
#:   under ordinary load, not just contention.
#: Pair with a single-connection pool (`Database` defaults to one for SQLite) so
#: one process does not hold multiple SMB file handles open at once.
AZURE_FILES_PRAGMAS: Mapping[str, Any] = {
    "journal_mode": "TRUNCATE",
    "synchronous": "FULL",
    "busy_timeout": 15000,
    "foreign_keys": "ON",
}


def declarative_base(*, naming_convention: bool = True) -> type[DeclarativeBase]:
    """Return a fresh `DeclarativeBase` subclass for one application's models.

    `naming_convention=True` (the default, and the only setting any app should
    ship) attaches :data:`NAMING_CONVENTION` to `metadata`. Without it, Alembic's
    `render_as_batch` mode — required for SQLite ALTER support — cannot generate
    a `DROP CONSTRAINT` because the constraint has no name to reference. Turning
    it off is for tests that assert against the bare SQLAlchemy default only.
    """
    from sqlalchemy import MetaData

    metadata = MetaData(naming_convention=dict(NAMING_CONVENTION) if naming_convention else None)

    class Base(DeclarativeBase):
        pass

    Base.metadata = metadata
    return Base


def sqlite_url_for(app_name: str, *, home: str = "/home") -> str:
    """The production SQLite URL for `app_name` on Azure App Service Linux.

    App Service mounts persistent storage at `/home`; anything written outside
    it is lost on every restart and scale event. Matches the convention already
    live in `posted/deploy/deploy.sh` (`DATABASE_URL=sqlite:////home/posted.db`)
    and generalizes it so a new app does not have to rediscover the four
    slashes (`sqlite://` + an absolute path).
    """
    path = Path(home) / f"{app_name}.db"
    return f"sqlite:///{path}"


def _is_sqlite_url(url: str) -> bool:
    return make_url(url).get_backend_name() == "sqlite"


def _is_memory_sqlite_url(url: str) -> bool:
    made = make_url(url)
    if made.get_backend_name() != "sqlite":
        return False
    database = made.database
    return database is None or database in ("", ":memory:") or "mode=memory" in url


class Database:
    """One application's engine, session factory, and pragma wiring.

    Constructed once, in `lifespan_migrations`'s `lifespan` (see
    :mod:`eventkit.db.migrate`) or equivalent app startup code — never at module
    import time. See the module docstring for why that distinction matters.
    """

    def __init__(
        self,
        url: str,
        *,
        echo: bool = False,
        sqlite_pragmas: Mapping[str, Any] | None = None,
        pool_pre_ping: bool = True,
    ) -> None:
        self.url = url
        self._sqlite_pragmas = dict(sqlite_pragmas) if sqlite_pragmas else {}

        engine_kwargs: dict[str, Any] = {"echo": echo}
        if _is_memory_sqlite_url(url):
            # A pool that hands out a new connection per checkout would give each
            # caller its own private `:memory:` database. StaticPool keeps the one
            # connection alive for the engine's lifetime, which is what an
            # in-process test database needs.
            engine_kwargs["poolclass"] = StaticPool
            engine_kwargs["connect_args"] = {"check_same_thread": False}
        elif _is_sqlite_url(url):
            # One connection at a time, not held open between requests — see
            # AZURE_FILES_PRAGMAS's docstring for why SMB makes this load-bearing
            # rather than a micro-optimisation.
            engine_kwargs["poolclass"] = NullPool
        else:
            engine_kwargs["pool_pre_ping"] = pool_pre_ping

        self.engine: Engine = create_engine(url, **engine_kwargs)

        if self._sqlite_pragmas and self.is_sqlite:
            pragmas = self._sqlite_pragmas

            @event.listens_for(self.engine, "connect")
            def _apply_pragmas(dbapi_connection: Any, _record: Any) -> None:
                cursor = dbapi_connection.cursor()
                try:
                    for name, value in pragmas.items():
                        cursor.execute(f"PRAGMA {name}={value}")
                finally:
                    cursor.close()

        self.session_factory: sessionmaker[Session] = sessionmaker(
            bind=self.engine, autoflush=False, autocommit=False, expire_on_commit=False
        )

    @property
    def is_sqlite(self) -> bool:
        return _is_sqlite_url(self.url)

    def sqlite_file(self) -> Path | None:
        """The on-disk path for a file-backed SQLite database, else `None`.

        `None` for every non-SQLite backend and for `:memory:`/shared-cache URLs,
        neither of which has a stable path to snapshot before a migration.
        """
        if not self.is_sqlite or _is_memory_sqlite_url(self.url):
            return None
        made = make_url(self.url)
        if not made.database:
            return None
        return Path(made.database)

    def get_db(self) -> Iterator[Session]:
        """FastAPI dependency: `Depends(database.get_db)`."""
        db = self.session_factory()
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Context manager for scripts and background tasks outside a request."""
        db = self.session_factory()
        try:
            yield db
        finally:
            db.close()
