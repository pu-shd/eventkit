"""Alembic wiring for a single application database.

Retires the hand-rolled migrator in ``ticketed/backend/database.py`` and
``posted/backend/database.py``, which wraps every ``ALTER TABLE`` in a
``try/except`` ending at ``logger.error(...)`` and continues running. If the
``ALTER`` genuinely fails — a locked file on Azure Files, a typo'd type — the
app boots believing the column exists, and the first write raises a 500 in the
webhook path: a dropped registration, with no version row to say what schema
production is actually on. Every function here raises instead.

**Known limitation.** :func:`upgrade_to_head` serialises with a local
``filelock``, which only excludes other processes on the same host. That
matches every current deployment target — SQLite on Azure Files, one instance,
enforced by ``scale-guard`` — but is not a substitute for a Postgres advisory
lock if an app ever runs multiple instances against a shared Postgres. Tracked
as a v0.2 item rather than built speculatively now; see
``.ralph/agent/decisions.md`` DEC-001.
"""

from __future__ import annotations

import functools
import logging
import shutil
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from filelock import FileLock, Timeout

from . import Database

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from contextlib import AbstractAsyncContextManager

    from fastapi import FastAPI
    from sqlalchemy import Engine

logger = logging.getLogger("eventkit.db.migrate")

__all__ = [
    "MigrationError",
    "assert_at_head",
    "current_revision",
    "ensure_columns",
    "init_migrations",
    "lifespan_migrations",
    "stamp",
    "upgrade_to_head",
]

_TEMPLATE_DIR = Path(__file__).parent / "alembic_template"
_INI_TEMPLATE = Path(__file__).parent / "alembic.ini.template"
_PACKAGE_TOKEN = "__EVENTKIT_PACKAGE__"  # noqa: S105 - a template placeholder, not a secret


class MigrationError(Exception):
    """A migration (or the lock guarding it) failed. Never swallowed."""


def init_migrations(app_dir: Path, *, package: str) -> None:
    """``eventkit db init`` — scaffold ``<app_dir>/migrations/`` and ``alembic.ini``.

    ``package`` must expose a top-level ``target_metadata`` attribute, typically
    ``target_metadata = Base.metadata`` next to the app's declarative ``Base``
    (see :func:`eventkit.db.declarative_base`). Refuses to overwrite an existing
    ``migrations/`` directory — re-running init on an app that already has
    migrations would silently discard authored revisions.
    """
    migrations_dir = app_dir / "migrations"
    if migrations_dir.exists():
        raise MigrationError(
            f"{migrations_dir} already exists; init_migrations() does not "
            f"overwrite an existing migrations directory."
        )
    shutil.copytree(_TEMPLATE_DIR, migrations_dir)
    env_py = migrations_dir / "env.py"
    env_py.write_text(env_py.read_text().replace(_PACKAGE_TOKEN, package))

    ini_path = app_dir / "alembic.ini"
    if not ini_path.exists():
        ini_path.write_text(_INI_TEMPLATE.read_text())


def _config_for(db: Database, migrations_dir: Path) -> Any:
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", str(migrations_dir))
    config.attributes["engine"] = db.engine
    return config


def _head_revision(migrations_dir: Path) -> str | None:
    from alembic.script import ScriptDirectory

    script = ScriptDirectory(str(migrations_dir))
    return script.get_current_head()


def current_revision(db: Database) -> str | None:
    """The revision stamped in ``db``'s ``alembic_version`` table, or ``None``."""
    from alembic.runtime.migration import MigrationContext

    with db.engine.connect() as connection:
        migration_context = MigrationContext.configure(connection)
        return migration_context.get_current_revision()


def assert_at_head(db: Database, *, migrations_dir: Path) -> None:
    """Raise unless ``db`` is exactly at ``migrations_dir``'s head revision.

    For a CI ``--check`` step or a readiness probe: confirms the schema a
    process is about to serve against is the one it was built for, without
    mutating anything.
    """
    head = _head_revision(migrations_dir)
    actual = current_revision(db)
    if actual != head:
        raise MigrationError(
            f"{db.url!r} is at revision {actual!r} but migrations/ head is "
            f"{head!r}. Run upgrade_to_head() (or `eventkit db upgrade`) before "
            f"serving traffic."
        )


def stamp(db: Database, revision: str, *, migrations_dir: Path) -> None:
    """Mark ``db`` as being at ``revision`` without running any migration DDL.

    One-time use: adopting an existing database (created by the predecessor
    hand-rolled migrator, or by ``Base.metadata.create_all()``) whose schema
    already matches ``revision``, so Alembic does not try to re-create tables
    that are already there.
    """
    from alembic import command

    command.stamp(_config_for(db, migrations_dir), revision)


def _lock_path_for(migrations_dir: Path) -> Path:
    # One lock per migrations directory rather than "next to the DB file":
    # correct for every current target (SQLite, one instance, enforced by
    # scale-guard) and for a lock-free-Postgres URL there is no DB file to sit
    # next to. See the module docstring's "Known limitation" for the
    # multi-instance-Postgres case this does not cover.
    return migrations_dir / ".migrate.lock"


def upgrade_to_head(
    db: Database,
    *,
    migrations_dir: Path,
    lock_timeout_s: int = 60,
    backup_first: bool = True,
) -> str:
    """Upgrade ``db`` to ``migrations_dir``'s head revision. Returns the new revision.

    Acquires a filelock next to ``migrations_dir`` first, so two processes
    starting at once (an App Service deploy's container overlap, or a slot
    swap) serialise rather than race. If ``backup_first`` and ``db`` is a
    file-backed SQLite database, snapshots the file to
    ``<db-file>.pre-<revision>.bak`` before touching it — restoring it is a
    manual step for a human, not automatic, because an automatic restore after
    a partially-applied migration could just as easily make things worse.

    Raises :class:`MigrationError` on any failure, including a lock timeout.
    Never logs and continues: an app that cannot confirm its schema must not
    start serving traffic against one it does not understand.
    """
    from alembic import command

    lock_path = _lock_path_for(migrations_dir)
    lock = FileLock(str(lock_path), timeout=lock_timeout_s)
    try:
        with lock:
            before = current_revision(db)
            target = _head_revision(migrations_dir)
            if before == target:
                return before or ""

            backup_path: Path | None = None
            sqlite_file = db.sqlite_file()
            if backup_first and sqlite_file is not None and sqlite_file.exists():
                backup_path = sqlite_file.with_name(
                    f"{sqlite_file.name}.pre-{target or 'head'}.bak"
                )
                shutil.copy2(sqlite_file, backup_path)

            config = _config_for(db, migrations_dir)
            try:
                command.upgrade(config, "head")
            except Exception as exc:
                backup_note = f" A pre-migration backup is at {backup_path}." if backup_path else ""
                raise MigrationError(
                    f"Migration to head failed for {db.url!r}, currently at "
                    f"revision {before!r}: {exc}.{backup_note} The application "
                    f"must not start against a database in this state."
                ) from exc

            return current_revision(db) or ""
    except Timeout as exc:
        raise MigrationError(
            f"Could not acquire the migration lock within {lock_timeout_s}s "
            f"({lock_path}). Another process may already be migrating this "
            f"database."
        ) from exc


def ensure_columns(engine: Engine, table: str, columns: Mapping[str, str]) -> None:
    """Add any of ``columns`` missing from ``table`` via a raw ``ALTER TABLE``.

    Documented **hotfix-only escape hatch** — it bypasses Alembic entirely, so
    the schema it produces is not recorded anywhere. Exists so nobody re-invents
    the 240-line version of this at 2am mid-conference; add a proper revision
    afterward. ``table``/``columns`` are developer-supplied identifiers, never
    end-user input — there is no bind-parameter form for a column name, which is
    inherent to DDL, not an oversight.
    """
    import sqlalchemy as sa

    logger.warning(
        "ensure_columns() bypassing Alembic: table=%s columns=%s. This is a "
        "hotfix-only escape hatch — add a proper revision after using it.",
        table,
        sorted(columns),
    )
    inspector = sa.inspect(engine)
    existing = {col["name"] for col in inspector.get_columns(table)}
    with engine.begin() as connection:
        for name, coltype in columns.items():
            if name in existing:
                continue
            connection.execute(
                sa.text(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {coltype}')  # noqa: S608
            )


def lifespan_migrations(
    db: Database,
    *,
    migrations_dir: Path,
    mode: Literal["upgrade", "check", "off"] = "upgrade",
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """A drop-in FastAPI ``lifespan`` factory.

    Replaces the ``Base.metadata.create_all()`` plus hand-rolled
    ``run_migrations()`` that today's apps run at *module import* time — see
    :mod:`eventkit.db`'s module docstring for why that forces
    ``tests/conftest.py`` to set environment variables before importing the
    application module at all. Wire it as::

        app = FastAPI(lifespan=lifespan_migrations(db, migrations_dir=MIGRATIONS_DIR))

    ``mode="upgrade"`` (the default) migrates on every startup — correct for a
    single-instance SQLite deployment. ``mode="check"`` only asserts the schema
    is at head, for a deployment that runs migrations as a separate step.
    ``mode="off"`` does nothing, for tests that manage the schema themselves.
    """
    if mode not in ("upgrade", "check", "off"):
        raise MigrationError(f"unknown lifespan_migrations mode: {mode!r}")

    @asynccontextmanager
    async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
        import anyio

        if mode == "upgrade":
            await anyio.to_thread.run_sync(
                functools.partial(upgrade_to_head, db, migrations_dir=migrations_dir)
            )
        elif mode == "check":
            await anyio.to_thread.run_sync(
                functools.partial(assert_at_head, db, migrations_dir=migrations_dir)
            )
        yield

    return _lifespan
