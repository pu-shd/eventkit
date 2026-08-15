"""Tests for eventkit.importer: iter_records across every source shape it
generalizes posted/backend/import_existing.py:14-77 into (tarball, directory,
single JSON list/dict-of-submissions/bare record, .jsonl, .csv, each's error
paths), plus run_import's never-raises posture (INVALID + errors vs. the
fatal-and-stop ImportSourceError/session_factory case), dry-run's rollback,
limit, fail-fast, batching, and the progress callback — all against a fake
session so the module's own contract is exercised with zero database, plus
one real SQLAlchemy round trip proving a dry run truly writes nothing."""

from __future__ import annotations

import csv
import io
import json
import logging
import tarfile
from argparse import ArgumentParser

import pytest
from sqlalchemy import String, select
from sqlalchemy.orm import Mapped, mapped_column

from eventkit.db import Database, declarative_base
from eventkit.importer import (
    ImportOutcome,
    ImportReport,
    ImportSourceError,
    add_import_arguments,
    iter_records,
    run_import,
)

Base = declarative_base()


class Widget(Base):
    __tablename__ = "widgets"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(200))


class FakeSession:
    """Stands in for a SQLAlchemy Session: run_import only ever calls
    commit()/rollback()/close() on it directly, and passes it through
    untouched to `upsert`."""

    def __init__(self):
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class Parsed:
    """A minimal stand-in for WebformSubmission: `.is_valid`/`.missing_required`
    are exactly what run_import reads off the parse() result."""

    def __init__(self, raw):
        self.raw = raw
        self.email = raw.get("email")
        self.missing_required = [] if self.email else ["email"]

    @property
    def is_valid(self):
        return not self.missing_required


def parse(raw):
    return Parsed(raw)


def make_upsert(store):
    def upsert(session, parsed):
        created = parsed.email not in store
        store[parsed.email] = parsed.raw
        return ImportOutcome.CREATED if created else ImportOutcome.UPDATED

    return upsert


# --------------------------------------------------------------------------
# iter_records
# --------------------------------------------------------------------------
class TestIterRecords:
    def test_reads_a_single_json_list(self, tmp_path):
        path = tmp_path / "export.json"
        path.write_text(json.dumps([{"email": "a@example.edu"}, {"email": "b@example.edu"}]))

        records = list(iter_records(path))

        assert records == [(0, {"email": "a@example.edu"}), (1, {"email": "b@example.edu"})]

    def test_reads_a_dict_of_submissions_keyed_by_sid(self, tmp_path):
        path = tmp_path / "export.json"
        path.write_text(
            json.dumps({"101": {"email": "a@example.edu"}, "102": {"email": "b@example.edu"}})
        )

        records = list(iter_records(path))

        assert [rec for _, rec in records] == [
            {"email": "a@example.edu"},
            {"email": "b@example.edu"},
        ]

    def test_reads_a_dict_of_submissions_keyed_by_long_uuid(self, tmp_path):
        path = tmp_path / "export.json"
        long_key = "a" * 32
        path.write_text(json.dumps({long_key: {"email": "a@example.edu"}}))

        records = list(iter_records(path))

        assert records == [(0, {"email": "a@example.edu"})]

    def test_reads_a_single_bare_record(self, tmp_path):
        path = tmp_path / "export.json"
        path.write_text(json.dumps({"email": "a@example.edu"}))

        records = list(iter_records(path))

        assert records == [(0, {"email": "a@example.edu"})]

    def test_raises_on_unsupported_json_root(self, tmp_path):
        path = tmp_path / "export.json"
        path.write_text(json.dumps("just a string"))

        with pytest.raises(ImportSourceError, match="unsupported JSON root"):
            list(iter_records(path))

    def test_raises_on_missing_path(self, tmp_path):
        with pytest.raises(ImportSourceError, match="does not exist"):
            list(iter_records(tmp_path / "nope.json"))

    def test_raises_on_a_corrupt_single_json_file(self, tmp_path):
        path = tmp_path / "export.json"
        path.write_text("{not json")

        with pytest.raises(ImportSourceError, match="could not read"):
            list(iter_records(path))

    def test_reads_a_directory_of_json_files_in_sorted_order(self, tmp_path):
        (tmp_path / "b.json").write_text(json.dumps({"email": "b@example.edu"}))
        (tmp_path / "a.json").write_text(json.dumps({"email": "a@example.edu"}))
        nested = tmp_path / "nested"
        nested.mkdir()
        (nested / "c.json").write_text(json.dumps({"email": "c@example.edu"}))

        records = [rec for _, rec in iter_records(tmp_path)]

        assert records == [
            {"email": "a@example.edu"},
            {"email": "b@example.edu"},
            {"email": "c@example.edu"},
        ]

    def test_skips_unreadable_file_in_directory_and_logs(self, tmp_path, caplog):
        (tmp_path / "good.json").write_text(json.dumps({"email": "a@example.edu"}))
        (tmp_path / "bad.json").write_text("{not json")

        with caplog.at_level(logging.WARNING, logger="eventkit.importer"):
            records = [rec for _, rec in iter_records(tmp_path)]

        assert records == [{"email": "a@example.edu"}]
        assert "bad.json" in caplog.text

    def test_reads_a_tarball(self, tmp_path):
        archive = tmp_path / "export.tar.gz"
        members = [
            ("b.json", {"email": "b@example.edu"}),
            ("a.json", {"email": "a@example.edu"}),
        ]
        with tarfile.open(archive, "w:gz") as tar:
            for name, rec in members:
                data = json.dumps(rec).encode("utf-8")
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, fileobj=io.BytesIO(data))

        records = [rec for _, rec in iter_records(archive)]

        assert records == [{"email": "a@example.edu"}, {"email": "b@example.edu"}]

    def test_reads_a_tgz_extension_the_same_way(self, tmp_path):
        archive = tmp_path / "export.tgz"
        with tarfile.open(archive, "w:gz") as tar:
            data = json.dumps({"email": "a@example.edu"}).encode("utf-8")
            info = tarfile.TarInfo("a.json")
            info.size = len(data)
            tar.addfile(info, fileobj=io.BytesIO(data))

        records = [rec for _, rec in iter_records(archive)]

        assert records == [{"email": "a@example.edu"}]

    def test_raises_on_a_corrupt_tarball(self, tmp_path):
        archive = tmp_path / "export.tar.gz"
        archive.write_bytes(b"not actually a gzip file")

        with pytest.raises(ImportSourceError, match="could not open tarball"):
            list(iter_records(archive))

    def test_skips_unreadable_member_in_tarball_and_logs(self, tmp_path, caplog):
        archive = tmp_path / "export.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            good = json.dumps({"email": "a@example.edu"}).encode("utf-8")
            info = tarfile.TarInfo("good.json")
            info.size = len(good)
            tar.addfile(info, fileobj=io.BytesIO(good))

            bad = b"{not json"
            info = tarfile.TarInfo("bad.json")
            info.size = len(bad)
            tar.addfile(info, fileobj=io.BytesIO(bad))

        with caplog.at_level(logging.WARNING, logger="eventkit.importer"):
            records = [rec for _, rec in iter_records(archive)]

        assert records == [{"email": "a@example.edu"}]
        assert "bad.json" in caplog.text

    def test_reads_jsonl_and_skips_bad_lines(self, tmp_path, caplog):
        path = tmp_path / "export.jsonl"
        path.write_text(
            json.dumps({"email": "a@example.edu"}) + "\n"
            "not json at all\n"
            "\n"
            + json.dumps({"email": "b@example.edu"}) + "\n"
        )

        with caplog.at_level(logging.WARNING, logger="eventkit.importer"):
            records = [rec for _, rec in iter_records(path)]

        assert records == [{"email": "a@example.edu"}, {"email": "b@example.edu"}]
        assert "line 2" in caplog.text

    def test_reads_csv(self, tmp_path):
        path = tmp_path / "export.csv"
        with path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["email", "first_name"])
            writer.writeheader()
            writer.writerow({"email": "a@example.edu", "first_name": "Ada"})
            writer.writerow({"email": "b@example.edu", "first_name": "Bea"})

        records = [rec for _, rec in iter_records(path)]

        assert records == [
            {"email": "a@example.edu", "first_name": "Ada"},
            {"email": "b@example.edu", "first_name": "Bea"},
        ]

    def test_falls_back_to_json_for_an_unrecognized_extension(self, tmp_path):
        path = tmp_path / "export.dat"
        path.write_text(json.dumps([{"email": "a@example.edu"}]))

        records = [rec for _, rec in iter_records(path)]

        assert records == [{"email": "a@example.edu"}]


# --------------------------------------------------------------------------
# run_import
# --------------------------------------------------------------------------
class TestRunImport:
    def _write_records(self, tmp_path, records):
        path = tmp_path / "export.json"
        path.write_text(json.dumps(records))
        return path

    def test_counts_created_updated_and_invalid(self, tmp_path):
        path = self._write_records(
            tmp_path,
            [
                {"email": "a@example.edu"},
                {"email": "a@example.edu"},  # same email -> updated
                {"no_email": "oops"},  # invalid
            ],
        )
        store = {}
        session = FakeSession()

        report = run_import(
            path, parse=parse, upsert=make_upsert(store), session_factory=lambda: session
        )

        assert report.total == 3
        assert report.counts[ImportOutcome.CREATED] == 1
        assert report.counts[ImportOutcome.UPDATED] == 1
        assert report.counts[ImportOutcome.INVALID] == 1
        assert report.errors == [(2, "missing required field(s): ['email']")]
        assert report.exit_code() == 1
        assert session.commits == 1
        assert session.closed is True

    def test_all_valid_records_exit_zero(self, tmp_path):
        path = self._write_records(tmp_path, [{"email": "a@example.edu"}])
        report = run_import(
            path, parse=parse, upsert=make_upsert({}), session_factory=FakeSession
        )
        assert report.exit_code() == 0

    def test_accept_rejects_become_skipped_not_invalid(self, tmp_path):
        path = self._write_records(
            tmp_path, [{"email": "a@example.edu", "presenting_poster": False}]
        )

        def accept(parsed):
            return bool(parsed.raw.get("presenting_poster"))

        report = run_import(
            path,
            parse=parse,
            upsert=make_upsert({}),
            session_factory=FakeSession,
            accept=accept,
        )

        assert report.counts[ImportOutcome.SKIPPED] == 1
        assert report.counts.get(ImportOutcome.INVALID, 0) == 0
        assert report.exit_code() == 0

    def test_accept_true_still_reaches_upsert(self, tmp_path):
        path = self._write_records(
            tmp_path, [{"email": "a@example.edu", "presenting_poster": True}]
        )

        report = run_import(
            path,
            parse=parse,
            upsert=make_upsert({}),
            session_factory=FakeSession,
            accept=lambda parsed: bool(parsed.raw.get("presenting_poster")),
        )

        assert report.counts[ImportOutcome.CREATED] == 1

    def test_accept_exception_becomes_invalid_and_run_continues(self, tmp_path):
        path = self._write_records(tmp_path, [{"email": "a@example.edu"}])

        def flaky_accept(parsed):
            raise RuntimeError("accept blew up")

        report = run_import(
            path,
            parse=parse,
            upsert=make_upsert({}),
            session_factory=FakeSession,
            accept=flaky_accept,
        )

        assert report.counts[ImportOutcome.INVALID] == 1
        assert "accept() failed: accept blew up" in report.errors[0][1]

    def test_parse_exception_becomes_invalid_and_run_continues(self, tmp_path):
        path = self._write_records(
            tmp_path, [{"email": "boom"}, {"email": "a@example.edu"}]
        )

        def flaky_parse(raw):
            if raw["email"] == "boom":
                raise ValueError("nope")
            return parse(raw)

        report = run_import(
            path, parse=flaky_parse, upsert=make_upsert({}), session_factory=FakeSession
        )

        assert report.total == 2
        assert report.counts[ImportOutcome.INVALID] == 1
        assert report.counts[ImportOutcome.CREATED] == 1
        assert report.errors == [(0, "parse failed: nope")]

    def test_upsert_exception_becomes_invalid_and_run_continues(self, tmp_path):
        path = self._write_records(
            tmp_path, [{"email": "boom"}, {"email": "a@example.edu"}]
        )

        def flaky_upsert(session, parsed):
            if parsed.email == "boom":
                raise RuntimeError("db said no")
            return ImportOutcome.CREATED

        report = run_import(
            path, parse=parse, upsert=flaky_upsert, session_factory=FakeSession
        )

        assert report.counts[ImportOutcome.INVALID] == 1
        assert report.counts[ImportOutcome.CREATED] == 1
        assert "db said no" in report.errors[0][1]

    def test_dry_run_never_commits_and_rolls_back(self, tmp_path):
        path = self._write_records(tmp_path, [{"email": "a@example.edu"}])
        session = FakeSession()

        report = run_import(
            path,
            parse=parse,
            upsert=make_upsert({}),
            session_factory=lambda: session,
            dry_run=True,
        )

        assert report.dry_run is True
        assert session.commits == 0
        assert session.rollbacks == 1
        assert "dry run" in report.render().lower()

    def test_limit_stops_after_n_records(self, tmp_path):
        path = self._write_records(
            tmp_path, [{"email": f"{i}@example.edu"} for i in range(5)]
        )

        report = run_import(
            path, parse=parse, upsert=make_upsert({}), session_factory=FakeSession, limit=2
        )

        assert report.total == 2

    def test_fail_fast_stops_at_first_invalid_but_still_commits(self, tmp_path):
        path = self._write_records(
            tmp_path,
            [
                {"email": "a@example.edu"},
                {"no_email": "oops"},
                {"email": "b@example.edu"},
            ],
        )
        session = FakeSession()

        report = run_import(
            path,
            parse=parse,
            upsert=make_upsert({}),
            session_factory=lambda: session,
            fail_fast=True,
        )

        assert report.total == 2
        assert report.counts[ImportOutcome.CREATED] == 1
        assert report.counts[ImportOutcome.INVALID] == 1
        assert report.counts.get(ImportOutcome.SKIPPED, 0) == 0
        assert session.commits == 1

    def test_batches_commits_by_batch_size(self, tmp_path):
        path = self._write_records(
            tmp_path, [{"email": f"{i}@example.edu"} for i in range(5)]
        )
        session = FakeSession()

        run_import(
            path,
            parse=parse,
            upsert=make_upsert({}),
            session_factory=lambda: session,
            batch_size=2,
        )

        # Two mid-loop commits (after record 2 and record 4) plus the final commit.
        assert session.commits == 3

    def test_progress_callback_receives_processed_and_total(self, tmp_path):
        path = self._write_records(
            tmp_path, [{"email": f"{i}@example.edu"} for i in range(3)]
        )
        seen = []

        run_import(
            path,
            parse=parse,
            upsert=make_upsert({}),
            session_factory=FakeSession,
            progress=lambda processed, total: seen.append((processed, total)),
        )

        assert seen == [(1, 3), (2, 3), (3, 3)]

    def test_progress_total_reflects_limit(self, tmp_path):
        path = self._write_records(
            tmp_path, [{"email": f"{i}@example.edu"} for i in range(5)]
        )
        seen = []

        run_import(
            path,
            parse=parse,
            upsert=make_upsert({}),
            session_factory=FakeSession,
            limit=2,
            progress=lambda processed, total: seen.append((processed, total)),
        )

        assert seen == [(1, 2), (2, 2)]

    def test_unreadable_source_is_fatal_and_session_factory_never_called(self, tmp_path):
        path = tmp_path / "missing.json"
        calls = []

        report = run_import(
            path,
            parse=parse,
            upsert=make_upsert({}),
            session_factory=lambda: calls.append(1) or FakeSession(),
        )

        assert report.fatal is True
        assert report.exit_code() == 2
        assert calls == []
        assert report.errors[0][0] == -1

    def test_session_factory_failure_is_fatal(self, tmp_path):
        path = self._write_records(tmp_path, [{"email": "a@example.edu"}])

        def broken_session_factory():
            raise RuntimeError("no database configured")

        report = run_import(
            path, parse=parse, upsert=make_upsert({}), session_factory=broken_session_factory
        )

        assert report.fatal is True
        assert report.exit_code() == 2
        assert "no database configured" in report.errors[0][1]

    def test_dry_run_writes_nothing_against_a_real_database(self, tmp_path):
        """The real thing dry_run's rollback promise is for: a real Session,
        a real upsert() that adds+flushes, and confirmation that nothing
        survives after the run — a fresh session sees an empty table."""
        db = Database(f"sqlite:///{tmp_path / 'test.db'}")
        Base.metadata.create_all(db.engine)

        def upsert(session, parsed):
            session.add(Widget(email=parsed.email))
            session.flush()
            return ImportOutcome.CREATED

        path = self._write_records(tmp_path, [{"email": "a@example.edu"}])
        session = db.session_factory()

        run_import(
            path, parse=parse, upsert=upsert, session_factory=lambda: session, dry_run=True
        )

        with db.session_factory() as fresh:
            assert fresh.execute(select(Widget)).scalars().all() == []

    def test_committed_run_persists_against_a_real_database(self, tmp_path):
        db = Database(f"sqlite:///{tmp_path / 'test.db'}")
        Base.metadata.create_all(db.engine)

        def upsert(session, parsed):
            session.add(Widget(email=parsed.email))
            session.flush()
            return ImportOutcome.CREATED

        path = self._write_records(tmp_path, [{"email": "a@example.edu"}])

        run_import(path, parse=parse, upsert=upsert, session_factory=db.session_factory)

        with db.session_factory() as fresh:
            assert len(fresh.execute(select(Widget)).scalars().all()) == 1


# --------------------------------------------------------------------------
# ImportReport
# --------------------------------------------------------------------------
class TestImportReport:
    def test_exit_code_zero_when_clean(self):
        assert ImportReport(total=3, counts={ImportOutcome.CREATED: 3}).exit_code() == 0

    def test_exit_code_one_when_invalid(self):
        report = ImportReport(total=2, counts={ImportOutcome.INVALID: 1})
        assert report.exit_code() == 1

    def test_exit_code_two_when_fatal_even_with_no_invalid(self):
        report = ImportReport(fatal=True)
        assert report.exit_code() == 2

    def test_render_lists_every_outcome_and_errors(self):
        report = ImportReport(
            total=2,
            counts={ImportOutcome.CREATED: 1, ImportOutcome.INVALID: 1},
            errors=[(1, "missing required field(s): ['email']")],
        )
        rendered = report.render()
        assert "created" in rendered
        assert "invalid" in rendered
        assert "[1] missing required field(s)" in rendered

    def test_render_flags_a_fatal_run(self):
        report = ImportReport(fatal=True, errors=[(-1, "import source does not exist: x")])
        assert "FATAL" in report.render()


# --------------------------------------------------------------------------
# add_import_arguments
# --------------------------------------------------------------------------
class TestAddImportArguments:
    def _parser(self):
        parser = ArgumentParser()
        add_import_arguments(parser)
        return parser

    def test_defaults(self, tmp_path):
        args = self._parser().parse_args([str(tmp_path / "export.json")])
        assert args.dry_run is False
        assert args.limit is None
        assert args.fail_fast is False
        assert args.quiet is False

    def test_flags(self, tmp_path):
        target = tmp_path / "export.json"
        args = self._parser().parse_args(
            [str(target), "--dry-run", "--limit", "5", "--fail-fast", "--quiet"]
        )
        assert args.path == target
        assert args.dry_run is True
        assert args.limit == 5
        assert args.fail_fast is True
        assert args.quiet is True
