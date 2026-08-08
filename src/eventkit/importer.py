"""Bulk-importing a Drupal webform export into an app's database.

Generalizes ``posted/backend/import_existing.py:14-77``: that script only reads
a ``.tar.gz``/directory/single-JSON-file export, hardcodes ``Presenter`` and
``DrupalWebhookPayload`` (its own copy of parsing, not :func:`eventkit.drupal.
parse_submission`), and commits or does nothing — there is no way to preview
what an import run would do before it does it. :func:`iter_records` adds
``.jsonl`` and ``.csv``; :func:`run_import` takes ``parse``/``upsert`` as
callables instead of importing an app's models, so the exact same
:func:`eventkit.drupal.parse_submission` call the webhook route makes is what
runs here too (``tests/unit/drupal/test_parity.py`` already pins that for the
webhook; nothing about this module's ``parse=`` seam lets a second, drifted
parser sneak back in). ``--dry-run`` is the safety feature the predecessor
never had — see the module docstring's second point below.

Two things worth reading before wiring this up in an app:

* :func:`run_import` never raises for a bad *record* — a record that fails to
  parse, fails validation, or makes ``upsert`` raise becomes
  ``ImportOutcome.INVALID`` plus an ``(index, message)`` entry in
  :attr:`ImportReport.errors`, and the run continues (unless ``fail_fast``).
  Only a source that cannot be read at all (missing path, corrupt archive,
  an unsupported JSON root shape, ``session_factory`` itself raising) is
  fatal — :meth:`ImportReport.exit_code` returns ``2`` for that, distinctly
  from ``1`` for "ran to completion but some records were invalid".
* ``dry_run=True`` runs every record through ``parse``/``accept``/``upsert``
  exactly as normal — so a preview run reports precisely what a real run
  would do — but the session is rolled back instead of committed, batch and
  final alike. ``upsert`` implementations are expected to call
  ``session.flush()`` (not ``commit()``) so per-row identity (e.g. a query
  for an existing row by email within the same run) still works during a
  dry run; :mod:`eventkit.eventbrite.sync`'s ``SqlAlchemySyncPorts`` already
  establishes that split.

Two things this module deliberately does **not** do, both left as the
``upsert`` implementation's job rather than invented here:

* Firing ``pending_payment``/``exempt_registration`` notifications at
  registrant-ingestion time. ``eventkit.eventbrite.sync``'s DEC-005 left this
  exact handoff for whoever builds registrant ingestion: an app's ``upsert``
  callback already receives the parsed submission (whose ``.fields`` carry
  ``tickets_sold_separately`` when the field map declares it) and a live
  ``Session``, and already has both :meth:`eventkit.eventprofile.models.
  Ticketing.is_exempt` and :class:`eventkit.notify.Notifier` available to call
  directly. Adding a parallel ``ports``/``emit`` protocol here (mirroring
  ``eventkit.eventbrite.sync.SyncPorts``) would duplicate a seam ``upsert``
  already is, for a notification decision that needs the app's own
  ``Registrant`` model to make.
* A generic top-level ``eventkit import <path>`` CLI verb. Unlike ``ui``'s
  asset vendoring or ``mirror``'s asset fetching, importing genuinely cannot
  be app-agnostic: it needs a ``Session``, an app-specific ``parse`` bound to
  that app's field map, and an app-specific ``upsert`` bound to that app's
  ORM model. :func:`add_import_arguments` is the reusable piece instead — an
  app's own ``python -m <app>.cli import <path>`` composes it in a handful of
  lines, the same way no app-agnostic ``eventkit backup`` or ``eventkit auth``
  verb exists either.
"""

from __future__ import annotations

import csv
import json
import logging
import tarfile
from collections.abc import Callable, Iterator, Mapping
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from .errors import EventKitError

if TYPE_CHECKING:  # pragma: no cover - typing only
    import argparse

    from sqlalchemy.orm import Session

logger = logging.getLogger("eventkit.importer")

__all__ = [
    "ImportOutcome",
    "ImportReport",
    "ImportSourceError",
    "add_import_arguments",
    "iter_records",
    "run_import",
]


class ImportSourceError(EventKitError):
    """The import source itself could not be read.

    Distinct from a single bad record (which becomes ``ImportOutcome.INVALID``
    and lets the run continue): this is missing path, a corrupt archive, or a
    JSON root that is neither a list nor an object — nothing in the source can
    be salvaged, so :func:`run_import` stops before touching the database and
    :meth:`ImportReport.exit_code` returns ``2``.
    """


class ImportOutcome(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    SKIPPED = "skipped"
    INVALID = "invalid"


class ImportReport(BaseModel):
    """What one :func:`run_import` call did."""

    model_config = ConfigDict(extra="forbid")

    total: int = 0
    counts: dict[ImportOutcome, int] = Field(default_factory=dict)
    #: ``(record index, message)`` — for INVALID outcomes and any per-record
    #: exception ``parse``/``accept``/``upsert`` raised.
    errors: list[tuple[int, str]] = Field(default_factory=list)
    #: Set when the *source* could not be read at all — see ``ImportSourceError``.
    fatal: bool = False
    dry_run: bool = False

    def exit_code(self) -> int:
        """0 ok, 1 had invalid records, 2 fatal (the source could not be read)."""
        if self.fatal:
            return 2
        if self.counts.get(ImportOutcome.INVALID):
            return 1
        return 0

    def render(self) -> str:
        header = f"Import report: {self.total} record(s) processed."
        if self.dry_run:
            header += " (dry run — nothing written)"
        lines = [header]
        for outcome in ImportOutcome:
            lines.append(f"  {outcome.value:<8} {self.counts.get(outcome, 0)}")
        if self.errors:
            lines.append(f"Errors ({len(self.errors)}):")
            lines.extend(f"  [{index}] {message}" for index, message in self.errors)
        if self.fatal:
            lines.append("FATAL: the import source could not be read; nothing was written.")
        return "\n".join(lines)


def _read_directory(path: Path) -> Iterator[dict[str, Any]]:
    for file_path in sorted(path.rglob("*.json")):
        try:
            with file_path.open(encoding="utf-8") as f:
                yield json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("importer: skipping unreadable file %s: %s", file_path, exc)


def _read_tarball(path: Path) -> Iterator[dict[str, Any]]:
    try:
        tar = tarfile.open(path, "r:gz")
    except (OSError, tarfile.TarError) as exc:
        raise ImportSourceError(f"could not open tarball {path}: {exc}") from exc

    with tar:
        members = sorted(
            (m for m in tar.getmembers() if m.isfile() and m.name.endswith(".json")),
            key=lambda m: m.name,
        )
        for member in members:
            extracted = tar.extractfile(member)
            if extracted is None:  # pragma: no cover - isfile() already excludes this
                continue
            try:
                yield json.loads(extracted.read().decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                logger.warning(
                    "importer: skipping unreadable tar member %s: %s", member.name, exc
                )


def _read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning(
                    "importer: skipping unparseable line %d of %s: %s", line_no, path, exc
                )


def _read_csv(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8", newline="") as f:
        yield from csv.DictReader(f)


def _read_json_file(path: Path) -> Iterator[dict[str, Any]]:
    """A single ``.json`` file: a list of records, or a dict keyed by
    submission id/uuid (``posted/backend/import_existing.py:59-67``'s
    heuristic — a numeric or long key means "dict of submissions"), or one
    bare record."""
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise ImportSourceError(f"could not read {path}: {exc}") from exc

    if isinstance(data, list):
        yield from data
        return
    if isinstance(data, dict):
        first_key = next(iter(data), None)
        if first_key is not None and (first_key.isdigit() or len(first_key) > 30):
            yield from data.values()
        else:
            yield data
        return
    raise ImportSourceError(
        f"{path}: unsupported JSON root {type(data).__name__} (expected a list or an object)"
    )


def iter_records(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    """Yield ``(index, record)`` from a Drupal export in any of the shapes this
    module understands, in a stable order.

    ``.tar.gz``/``.tgz`` (JSON members) | a directory (``**/*.json``) |
    ``.jsonl`` | ``.json`` (a list, or a dict of submissions keyed by
    sid/uuid) | ``.csv``. Anything else is assumed to be a single combined
    JSON file, matching the predecessor's fallback.

    A file that cannot itself be read (inside a tarball or directory) is
    logged and skipped — it never became a record, so it does not consume an
    index. A source that cannot be read *at all* raises :class:`ImportSourceError`.
    """
    path = Path(path)
    if not path.exists():
        raise ImportSourceError(f"import source does not exist: {path}")

    if path.is_dir():
        raw = _read_directory(path)
    elif path.name.endswith((".tar.gz", ".tgz")):
        raw = _read_tarball(path)
    elif path.suffix == ".jsonl":
        raw = _read_jsonl(path)
    elif path.suffix == ".csv":
        raw = _read_csv(path)
    else:
        raw = _read_json_file(path)

    yield from enumerate(raw)


def _import_one(
    raw: Mapping[str, Any],
    *,
    parse: Callable[[Mapping[str, Any]], Any],
    upsert: Callable[[Session, Any], ImportOutcome],
    accept: Callable[[Any], bool] | None,
    session: Session,
) -> tuple[ImportOutcome, str | None]:
    try:
        parsed = parse(raw)
    except Exception as exc:  # noqa: BLE001 - a bad record must not abort the run
        return ImportOutcome.INVALID, f"parse failed: {exc}"

    if not getattr(parsed, "is_valid", True):
        missing = getattr(parsed, "missing_required", None)
        detail = f"missing required field(s): {missing}" if missing else "no stable identity"
        return ImportOutcome.INVALID, detail

    if accept is not None:
        try:
            wanted = accept(parsed)
        except Exception as exc:  # noqa: BLE001
            return ImportOutcome.INVALID, f"accept() failed: {exc}"
        if not wanted:
            return ImportOutcome.SKIPPED, None

    try:
        outcome = upsert(session, parsed)
    except Exception as exc:  # noqa: BLE001
        return ImportOutcome.INVALID, f"upsert failed: {exc}"

    return outcome, None


def run_import(
    path: Path,
    *,
    parse: Callable[[Mapping[str, Any]], Any],
    upsert: Callable[[Session, Any], ImportOutcome],
    session_factory: Callable[[], Session],
    accept: Callable[[Any], bool] | None = None,
    dry_run: bool = False,
    limit: int | None = None,
    fail_fast: bool = False,
    batch_size: int = 200,
    progress: Callable[[int, int], None] | None = None,
) -> ImportReport:
    """Read, parse, and upsert every record at ``path``. Never raises.

    ``parse`` is the same function the webhook route calls (typically
    :func:`eventkit.drupal.parse_submission` bound to the app's field map via
    ``functools.partial``); ``upsert`` is the app's own write, returning which
    :class:`ImportOutcome` it was. ``accept`` filters *after* parsing but
    *before* writing (e.g. ``posted``'s "only presenting posters" rule) — a
    rejected record is ``SKIPPED``, not ``INVALID``.

    ``fail_fast`` stops at the first ``INVALID`` record rather than working
    through the rest of the source; whatever was already written in that run
    is still committed (unless ``dry_run``) — fail-fast previews "does this
    source have a problem", it does not throw away otherwise-good rows.
    """
    report = ImportReport(dry_run=dry_run)

    try:
        records = list(iter_records(Path(path)))
    except ImportSourceError as exc:
        report.fatal = True
        report.errors.append((-1, str(exc)))
        return report

    if limit is not None:
        records = records[:limit]
    total = len(records)

    try:
        session = session_factory()
    except Exception as exc:  # noqa: BLE001 - source-level failure, not a record's
        report.fatal = True
        report.errors.append((-1, f"session_factory failed: {exc}"))
        return report

    try:
        for processed, (index, raw) in enumerate(records, start=1):
            outcome, error = _import_one(
                raw, parse=parse, upsert=upsert, accept=accept, session=session
            )
            report.total += 1
            report.counts[outcome] = report.counts.get(outcome, 0) + 1
            if error is not None:
                report.errors.append((index, error))

            if not dry_run and processed % batch_size == 0:
                session.commit()

            if progress is not None:
                progress(processed, total)

            if fail_fast and outcome is ImportOutcome.INVALID:
                break

        if not dry_run:
            session.commit()
    finally:
        if dry_run:
            session.rollback()
        session.close()

    return report


def add_import_arguments(parser: argparse.ArgumentParser) -> None:
    """Add ``path``, ``--dry-run``, ``--limit``, ``--fail-fast`` and ``--quiet``
    to an app's own ``import`` subparser, so every app's importer CLI takes the
    same flags. ``python -m <app>.cli import <path>`` composes this with the
    app's own ``parse``/``upsert``/``session_factory``::

        import_parser = sub.add_parser("import")
        add_import_arguments(import_parser)
        import_parser.set_defaults(func=_cmd_import)

        def _cmd_import(args):
            report = run_import(
                args.path, parse=..., upsert=..., session_factory=...,
                dry_run=args.dry_run, limit=args.limit, fail_fast=args.fail_fast,
            )
            if not args.quiet:
                print(report.render())
            return report.exit_code()
    """
    parser.add_argument(
        "path", type=Path, help="a .tar.gz/.tgz, directory, .jsonl, .json, or .csv export"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="parse and report what would happen; write nothing"
    )
    parser.add_argument("--limit", type=int, default=None, help="stop after N records")
    parser.add_argument(
        "--fail-fast", action="store_true", help="stop at the first invalid record"
    )
    parser.add_argument(
        "--quiet", action="store_true", help="suppress the report (exit code still reflects it)"
    )
