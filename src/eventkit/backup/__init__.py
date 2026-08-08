"""Whole-database JSON backup and restore, driven by a declared table order.

Replaces the ~55-line hand-written field list each of ``ticketed`` and
``posted`` carries per table (``ticketed/backend/backup.py``,
``posted/backend/backup.py``): both enumerate every column by hand for dump and
restore, so an added column is silently absent from every backup taken until
someone remembers to update the list — the "two hand-written backup formats"
called out in the package docstring. Here the column list comes from
``sqlalchemy.inspect(model).columns`` once, in :func:`dump` and :func:`restore`
alike, so the two can never drift from each other or from the schema.

Two hardening rules are load-bearing and covered by tests, not just comments:

* :func:`restore` validates the **entire** payload — manifest compatibility,
  declared-table membership, and every row's columns — before executing a
  single ``DELETE``. The predecessors validate as they go, so a malformed row
  three tables into a restore leaves the database half-wiped with no way back
  short of the last (manual, easily forgotten) backup.
* :func:`make_backup_router`'s restore endpoint defaults ``enable_restore`` to
  a callable returning ``False``. A destructive endpoint that is reachable
  by construction, with no separate opt-in, is how a fat-fingered upload
  becomes an incident instead of a mistake caught by a confirmation phrase.

FastAPI is imported lazily, inside :func:`make_backup_router` only: ``dump()``
and ``restore()`` are plain SQLAlchemy and have no reason to require the
``web`` extra, matching the posture already established in :mod:`eventkit.auth`.
SQLAlchemy itself is a top-level import — unlike :mod:`eventkit.auth`, there is
no way to state a backup spec at all without it, so there is nothing gained by
deferring it.
"""

from __future__ import annotations

import base64
import json
import shutil
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import delete, func, inspect, select
from sqlalchemy.orm import DeclarativeBase, Session

from .. import __version__
from ..errors import EventKitError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastapi import APIRouter, HTTPException, Response, UploadFile, status

    from ..auth import Principal
    from ..db import Database

__all__ = [
    "BackupError",
    "BackupManifest",
    "BackupSpec",
    "BackupValidationError",
    "ForeignBackupError",
    "TableSpec",
    "dump",
    "make_backup_router",
    "restore",
]


class BackupError(EventKitError):
    """Base class for every error :mod:`eventkit.backup` raises deliberately."""


class BackupValidationError(BackupError):
    """The payload does not match the app's declared backup spec.

    Raised before :func:`restore` touches the database — see the module
    docstring's first hardening rule.
    """


class ForeignBackupError(BackupValidationError):
    """The payload's ``manifest.app_name`` does not match this app's spec."""


class TableSpec(BaseModel):
    """One table's place in a backup: which model, in what dump/restore order."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    model: type[DeclarativeBase]
    key: str
    order: int = 0
    redact: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    seed_if_missing: Callable[[Session], None] | None = None


class BackupSpec(BaseModel):
    """One app's whole-database backup: its tables, in dump/restore order."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    app_name: str
    tables: list[TableSpec]
    app_version: str = "0.0.0"
    filename_prefix: str = "backup"
    required_keys: set[str] = set()


class BackupManifest(BaseModel):
    """Provenance and shape of one backup payload, checked before restore."""

    app_name: str
    eventkit_version: str
    app_version: str
    alembic_revision: str | None
    created_at: datetime
    row_counts: dict[str, int]
    format_version: int = 1


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    return value


def _from_jsonable(value: Any, column: Any) -> Any:
    if value is None:
        return None
    try:
        python_type = column.type.python_type
    except NotImplementedError:
        return value
    if python_type is datetime and isinstance(value, str):
        return datetime.fromisoformat(value)
    if python_type is date and isinstance(value, str):
        return date.fromisoformat(value)
    if python_type is Decimal:
        return Decimal(str(value))
    if python_type is bytes and isinstance(value, str):
        return base64.b64decode(value)
    return value


def _current_alembic_revision(session: Session) -> str | None:
    """The revision stamped in ``session``'s database, or ``None``.

    ``None`` covers both "no migrations adopted yet" and "the alembic_version
    table does not exist" — a backup is still useful in either case, so this
    never raises.
    """
    from alembic.runtime.migration import MigrationContext

    try:
        context = MigrationContext.configure(session.connection())
        return context.get_current_revision()
    except Exception:  # noqa: BLE001 - genuinely best-effort, see docstring
        return None


def dump(
    session: Session,
    spec: BackupSpec,
    *,
    manifest_extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Every table in ``spec``, as a JSON-safe dict keyed by ``manifest``/``tables``.

    Column names come from ``sqlalchemy.inspect(model).columns`` — see the
    module docstring for why that, and not a hand-written field list, is the
    entire point of this function.
    """
    tables_payload: dict[str, list[dict[str, Any]]] = {}
    row_counts: dict[str, int] = {}

    for table in sorted(spec.tables, key=lambda t: t.order):
        mapper = inspect(table.model)
        column_names = [column.name for column in mapper.columns]
        statement = select(table.model)
        primary_key = mapper.primary_key
        if primary_key:
            statement = statement.order_by(*primary_key)

        rows: list[dict[str, Any]] = []
        for obj in session.execute(statement).scalars():
            row = {name: _to_jsonable(getattr(obj, name)) for name in column_names}
            if table.redact is not None:
                row = table.redact(row)
            rows.append(row)

        tables_payload[table.key] = rows
        row_counts[table.key] = len(rows)

    manifest = BackupManifest(
        app_name=spec.app_name,
        eventkit_version=__version__,
        app_version=spec.app_version,
        alembic_revision=_current_alembic_revision(session),
        created_at=datetime.now(UTC),
        row_counts=row_counts,
    )
    manifest_dict = manifest.model_dump(mode="json")
    if manifest_extra:
        manifest_dict.update(dict(manifest_extra))

    return {"manifest": manifest_dict, "tables": tables_payload}


def _row_to_model_kwargs(model: type[DeclarativeBase], row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        column.name: _from_jsonable(row[column.name], column)
        for column in inspect(model).columns
        if column.name in row
    }


def restore(
    session: Session,
    spec: BackupSpec,
    payload: Mapping[str, Any],
    *,
    dry_run: bool = False,
) -> BackupManifest:
    """Restore ``payload`` into ``session``'s database, or just validate it.

    Validates the manifest, table membership and every row's columns for
    *every* declared table before issuing a single ``DELETE`` — see the module
    docstring's first hardening rule. ``dry_run=True`` (used by
    ``{prefix}/db-restore/validate``) stops right after that validation pass
    and never touches the session.

    Tables absent from ``payload["tables"]`` are left untouched, unless their
    key is in ``spec.required_keys``, in which case their absence itself is a
    validation failure. A present-but-empty table is restored to empty: every
    existing row is deleted, and ``seed_if_missing`` (if set) runs afterward so
    a table that must never be truly empty — swag inventory, say — gets its
    defaults back rather than staying empty because an old backup predates it.
    """
    manifest_raw = payload.get("manifest")
    if not isinstance(manifest_raw, Mapping):
        raise BackupValidationError("payload is missing a 'manifest' object.")
    try:
        manifest = BackupManifest.model_validate(manifest_raw)
    except ValidationError as exc:
        raise BackupValidationError(f"payload manifest is invalid: {exc}") from exc

    if manifest.app_name != spec.app_name:
        raise ForeignBackupError(
            f"backup was made for app {manifest.app_name!r}, not {spec.app_name!r}."
        )

    tables_raw = payload.get("tables")
    if not isinstance(tables_raw, Mapping):
        raise BackupValidationError("payload is missing a 'tables' object.")

    tables_by_key = {table.key: table for table in spec.tables}

    missing_required = spec.required_keys - tables_raw.keys()
    if missing_required:
        raise BackupValidationError(
            f"payload is missing required table(s): {sorted(missing_required)}"
        )

    unknown_keys = tables_raw.keys() - tables_by_key.keys()
    if unknown_keys:
        raise BackupValidationError(
            f"payload has table(s) not declared in this app's backup spec: "
            f"{sorted(unknown_keys)}"
        )

    prepared: dict[str, list[dict[str, Any]]] = {}
    for key, table in tables_by_key.items():
        if key not in tables_raw:
            continue
        rows = tables_raw[key]
        if not isinstance(rows, list):
            raise BackupValidationError(f"table {key!r} payload must be a list of rows.")
        column_names = {column.name for column in inspect(table.model).columns}
        prepared_rows: list[dict[str, Any]] = []
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                raise BackupValidationError(f"table {key!r} row {index} is not an object.")
            extra = row.keys() - column_names
            if extra:
                raise BackupValidationError(
                    f"table {key!r} row {index} has unknown column(s): {sorted(extra)}"
                )
            try:
                prepared_rows.append(_row_to_model_kwargs(table.model, row))
            except (TypeError, ValueError) as exc:
                raise BackupValidationError(
                    f"table {key!r} row {index} failed validation: {exc}"
                ) from exc
        prepared[key] = prepared_rows

    if dry_run:
        return manifest

    ordered = sorted((tables_by_key[key] for key in prepared), key=lambda t: t.order)
    context = session.begin_nested() if session.in_transaction() else session.begin()
    with context:
        for table in reversed(ordered):
            session.execute(delete(table.model))
        for table in ordered:
            rows = prepared[table.key]
            for kwargs in rows:
                session.add(table.model(**kwargs))
            session.flush()
            if not rows and table.seed_if_missing is not None:
                table.seed_if_missing(session)

    return manifest


def _publish_fastapi_names() -> None:
    """Publish FastAPI/SQLAlchemy names this module's nested routes annotate with.

    Same reason as :func:`eventkit.auth._publish_fastapi_names`: ``from
    __future__ import annotations`` makes every annotation in this file a
    string, and FastAPI resolves a route function's parameter types with
    ``typing.get_type_hints(fn)`` against ``fn.__globals__`` — this module's
    namespace, not :func:`make_backup_router`'s locals. Every nested function
    defined in this module shares that globals dict, so publishing here once
    is enough for all of them.
    """
    if "UploadFile" in globals():
        return
    from fastapi import HTTPException, Response, UploadFile, status

    from ..auth import Principal

    globals()["UploadFile"] = UploadFile
    globals()["Response"] = Response
    globals()["HTTPException"] = HTTPException
    globals()["status"] = status
    globals()["Session"] = Session
    globals()["Principal"] = Principal


def make_backup_router(
    spec: BackupSpec,
    *,
    db: Callable[..., Session],
    principal: Callable[..., Principal],
    enable_restore: Callable[[], bool],
    database: Database | None = None,
    prefix: str = "/api/admin",
    confirm_phrase: str = "RESTORE",
) -> APIRouter:
    """``GET {prefix}/db-backup``, ``POST {prefix}/db-restore(/validate)``.

    ``database``, if given, unlocks two extra guards beyond what :func:`restore`
    itself checks: the restore endpoint refuses a payload whose
    ``alembic_revision`` differs from the live database's unless the caller
    passes ``?force=1``, and it snapshots the database file (when file-backed
    SQLite) before restoring. Without it, restore still works — schema and
    manifest.app_name are always checked by :func:`restore` — it is just
    unguarded against restoring a backup taken at a different schema version.

    ``enable_restore`` is checked on every restore request, not just once at
    router-construction time, so an app can flip it via a live settings
    reload without restarting.
    """
    _publish_fastapi_names()
    from fastapi import APIRouter, Depends, File, Form, Query

    from ..db.migrate import current_revision

    router = APIRouter(prefix=prefix, tags=["backup"])

    def _filename() -> str:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        return f"{spec.filename_prefix}-{stamp}.json"

    def _load_payload(raw: bytes) -> dict[str, Any]:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"uploaded file is not valid JSON: {exc}",
            ) from exc
        if not isinstance(parsed, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="uploaded file must be a JSON object.",
            )
        return parsed

    def _restore_or_400(session: Session, payload: dict[str, Any], *, dry_run: bool):
        try:
            return restore(session, spec, payload, dry_run=dry_run)
        except ForeignBackupError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except BackupValidationError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @router.get("/db-backup")
    def db_backup(
        session: Session = Depends(db),
        principal_: Principal = Depends(principal),
    ) -> Response:
        payload = dump(session, spec)
        body = json.dumps(payload, indent=2).encode("utf-8")
        return Response(
            content=body,
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="{_filename()}"',
                "Cache-Control": "no-store",
            },
        )

    @router.post("/db-restore/validate")
    async def db_restore_validate(
        file: UploadFile = File(...),
        session: Session = Depends(db),
        principal_: Principal = Depends(principal),
    ) -> dict[str, Any]:
        payload = _load_payload(await file.read())
        manifest = _restore_or_400(session, payload, dry_run=True)
        diff = {
            table.key: {
                "current_rows": session.scalar(
                    select(func.count()).select_from(table.model)
                ),
                "uploaded_rows": manifest.row_counts.get(table.key, 0),
            }
            for table in spec.tables
        }
        return {"manifest": manifest.model_dump(mode="json"), "diff": diff}

    @router.post("/db-restore")
    async def db_restore(
        file: UploadFile = File(...),
        confirm: str = Form(...),
        force: bool = Query(False),
        session: Session = Depends(db),
        principal_: Principal = Depends(principal),
    ) -> dict[str, Any]:
        if not enable_restore():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Restore is not enabled on this deployment.",
            )
        if confirm != confirm_phrase:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"confirmation phrase must be exactly {confirm_phrase!r}.",
            )

        payload = _load_payload(await file.read())

        manifest_raw = payload.get("manifest")
        uploaded_revision = (
            manifest_raw.get("alembic_revision") if isinstance(manifest_raw, dict) else None
        )
        if database is not None and not force:
            live_revision = current_revision(database)
            if uploaded_revision != live_revision:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"backup was taken at alembic revision {uploaded_revision!r}, "
                        f"database is at {live_revision!r}. Pass ?force=1 to restore anyway."
                    ),
                )

        if database is not None:
            sqlite_file = database.sqlite_file()
            if sqlite_file is not None and sqlite_file.exists():
                stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
                snapshot = sqlite_file.with_name(f"{sqlite_file.name}.pre-restore-{stamp}.bak")
                shutil.copy2(sqlite_file, snapshot)

        manifest = _restore_or_400(session, payload, dry_run=False)
        return {"manifest": manifest.model_dump(mode="json")}

    return router
