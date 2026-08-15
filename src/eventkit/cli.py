"""The ``eventkit`` console script.

Every verb here is backed by a built module. Nothing is stubbed: a verb that
half-provisions a subscription and then reports success is worse than a verb
that does not exist.

    eventkit profile validate [PATH]     exit 0/1, human-readable error report
    eventkit profile public   [PATH]     the exact JSON served at /api/event-profile
    eventkit profile checkin-keys [PATH] the legacy -> ISO check-in key mapping
    eventkit fieldmap check   [PATH]     resolve a field map and report unmapped risk
    eventkit db init/upgrade/stamp/current   see `eventkit db --help`
    eventkit ui vendor/vendor-theme          see `eventkit ui --help`
    eventkit mirror run                      see `eventkit mirror --help`
    eventkit azure deploy/resume/update/…    see `eventkit azure --help`
    eventkit version
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from . import __version__

def _load(path: str | None):
    from .eventprofile.load import load_profile

    return load_profile(path)


def _cmd_profile_validate(args: argparse.Namespace) -> int:
    from .errors import EventProfileError

    try:
        profile = _load(args.path)
    except EventProfileError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(
        f"OK  {profile.event.title}  slug={profile.event.slug}  "
        f"theme={profile.branding.theme}  checkin_days={len(profile.schedule.checkin_days)}"
    )
    if args.require:
        try:
            profile.validate_for_app(args.app or "this app", require=args.require)
        except EventProfileError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"OK  required keys present for {args.app or 'this app'}")
    return 0


def _cmd_profile_public(args: argparse.Namespace) -> int:
    from .errors import EventProfileError
    from .eventprofile.public import to_public_dict

    try:
        profile = _load(args.path)
    except EventProfileError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(to_public_dict(profile), indent=2, sort_keys=True))
    return 0


def _cmd_profile_checkin_keys(args: argparse.Namespace) -> int:
    from .errors import EventProfileError
    from .eventprofile.checkin import legacy_key_aliases

    try:
        profile = _load(args.path)
    except EventProfileError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    aliases = legacy_key_aliases(profile.schedule)
    width = max((len(k) for k in aliases), default=0)
    for legacy, canonical in sorted(aliases.items()):
        marker = "  " if legacy == canonical else "->"
        print(f"  {legacy:<{width}} {marker} {canonical}")
    return 0


def _cmd_fieldmap_check(args: argparse.Namespace) -> int:
    from .drupal import resolve_field_map
    from .errors import EventProfileError

    try:
        profile = _load(args.path)
        field_map = resolve_field_map(profile, want=args.want or [])
    except EventProfileError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"OK  {len(field_map.fields)} logical field(s) mapped")
    for name in sorted(field_map.fields):
        rule = field_map.fields[name]
        required = " required" if rule.required else ""
        print(f"  {name:<36} <- {', '.join(rule.keys)}  ({rule.kind}{required})")
    return 0


def _cmd_db_init(args: argparse.Namespace) -> int:
    from pathlib import Path

    from .db.migrate import MigrationError, init_migrations

    try:
        init_migrations(Path(args.app_dir), package=args.package)
    except MigrationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"OK  migrations/ and alembic.ini written under {args.app_dir}")
    return 0


def _cmd_db_upgrade(args: argparse.Namespace) -> int:
    from pathlib import Path

    from .db import AZURE_FILES_PRAGMAS, Database
    from .db.migrate import MigrationError, upgrade_to_head

    pragmas = AZURE_FILES_PRAGMAS if args.azure_files_pragmas else None
    db = Database(args.url, sqlite_pragmas=pragmas)
    try:
        revision = upgrade_to_head(
            db,
            migrations_dir=Path(args.migrations_dir),
            lock_timeout_s=args.lock_timeout,
            backup_first=not args.no_backup,
        )
    except MigrationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"OK  now at revision {revision or '(base)'}")
    return 0


def _cmd_db_stamp(args: argparse.Namespace) -> int:
    from pathlib import Path

    from .db import Database
    from .db.migrate import MigrationError, stamp

    db = Database(args.url)
    try:
        stamp(db, args.revision, migrations_dir=Path(args.migrations_dir))
    except MigrationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"OK  stamped at {args.revision}")
    return 0


def _cmd_db_current(args: argparse.Namespace) -> int:
    from .db import Database
    from .db.migrate import current_revision

    db = Database(args.url)
    print(current_revision(db) or "(base)")
    return 0


def _cmd_ui_vendor(args: argparse.Namespace) -> int:
    from pathlib import Path

    from .ui import ThemeNotFoundError, vendor

    try:
        manifest = vendor(Path(args.dest), theme=args.theme, hashed=args.hashed)
    except ThemeNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"OK  {len(manifest.entries)} file(s) vendored to {args.dest} (theme={args.theme})")
    return 0


def _cmd_ui_vendor_theme(args: argparse.Namespace) -> int:
    from pathlib import Path

    from .errors import EventProfileError
    from .ui import render_theme_vars

    try:
        profile = _load(args.path)
    except EventProfileError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    css = render_theme_vars(profile)
    if args.out:
        Path(args.out).write_text(css)
        print(f"OK  wrote {args.out}")
    else:
        print(css, end="")
    return 0


def _cmd_mirror_run(args: argparse.Namespace) -> int:
    from pathlib import Path

    import yaml

    from .mirror import MirrorSpec, bypass_header_from_env, mirror

    spec = MirrorSpec.model_validate(yaml.safe_load(Path(args.spec).read_text(encoding="utf-8")))
    bypass = bypass_header_from_env()
    if bypass is not None:
        spec = spec.model_copy(update={"bypass_header": bypass})

    report = mirror(spec, Path(args.dest), force=args.force)
    if not args.quiet:
        print(report.render())
    return report.exit_code()


def _cmd_azure(args: argparse.Namespace) -> int:
    """Hand over to the zsh toolkit.

    ``execve`` rather than ``subprocess``: the toolkit spends most of its time in
    an interactive wait, reading single keypresses and drawing a spinner, so it
    needs the terminal and the signal handling to be genuinely its own.
    """
    from .azure import exec_toolkit

    return exec_toolkit(list(args.rest))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eventkit", description="Sherrerd event-management stack toolkit."
    )
    parser.add_argument("--version", action="version", version=f"eventkit {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    profile = sub.add_parser("profile", help="event profile operations")
    profile_sub = profile.add_subparsers(dest="subcommand", required=True)

    validate = profile_sub.add_parser("validate", help="validate an event profile")
    validate.add_argument("path", nargs="?", default=None)
    validate.add_argument("--app", default=None, help="app name for the error message")
    validate.add_argument(
        "--require",
        nargs="*",
        default=None,
        metavar="DOTTED.PATH",
        help="dotted paths that must be present and non-empty, e.g. swag.options",
    )
    validate.set_defaults(func=_cmd_profile_validate)

    public = profile_sub.add_parser("public", help="print the public JSON projection")
    public.add_argument("path", nargs="?", default=None)
    public.set_defaults(func=_cmd_profile_public)

    keys = profile_sub.add_parser(
        "checkin-keys", help="print the legacy -> ISO check-in key mapping"
    )
    keys.add_argument("path", nargs="?", default=None)
    keys.set_defaults(func=_cmd_profile_checkin_keys)

    fieldmap = sub.add_parser("fieldmap", help="Drupal field map operations")
    fieldmap_sub = fieldmap.add_subparsers(dest="subcommand", required=True)
    check = fieldmap_sub.add_parser("check", help="resolve and print a field map")
    check.add_argument("path", nargs="?", default=None)
    check.add_argument("--want", nargs="*", default=None, metavar="LOGICAL_FIELD")
    check.set_defaults(func=_cmd_fieldmap_check)

    db = sub.add_parser("db", help="Alembic migration operations")
    db_sub = db.add_subparsers(dest="subcommand", required=True)

    db_init = db_sub.add_parser("init", help="scaffold migrations/ and alembic.ini")
    db_init.add_argument("--app-dir", default=".", help="application root (default: cwd)")
    db_init.add_argument(
        "--package", required=True, help="app package exposing a top-level target_metadata"
    )
    db_init.set_defaults(func=_cmd_db_init)

    db_upgrade = db_sub.add_parser("upgrade", help="upgrade a database to the migrations head")
    db_upgrade.add_argument("--url", required=True, help="SQLAlchemy database URL")
    db_upgrade.add_argument("--migrations-dir", required=True)
    db_upgrade.add_argument(
        "--azure-files-pragmas",
        action="store_true",
        help="apply AZURE_FILES_PRAGMAS (TRUNCATE journal, FULL sync) for an SMB-mounted database",
    )
    db_upgrade.add_argument("--lock-timeout", type=int, default=60, dest="lock_timeout")
    db_upgrade.add_argument(
        "--no-backup", action="store_true", help="skip the pre-migration SQLite file snapshot"
    )
    db_upgrade.set_defaults(func=_cmd_db_upgrade)

    db_stamp = db_sub.add_parser(
        "stamp", help="mark a database at a revision without running any migration"
    )
    db_stamp.add_argument("--url", required=True)
    db_stamp.add_argument("--migrations-dir", required=True)
    db_stamp.add_argument("revision")
    db_stamp.set_defaults(func=_cmd_db_stamp)

    db_current = db_sub.add_parser("current", help="print the database's current revision")
    db_current.add_argument("--url", required=True)
    db_current.set_defaults(func=_cmd_db_current)

    ui = sub.add_parser("ui", help="UI kit asset vendoring")
    ui_sub = ui.add_subparsers(dest="subcommand", required=True)

    ui_vendor = ui_sub.add_parser(
        "vendor", help="copy the UI kit's shared assets and one theme into a directory"
    )
    ui_vendor.add_argument("--dest", required=True, help="destination directory")
    ui_vendor.add_argument("--theme", required=True, help="theme id, e.g. neutral, princeton-orfe")
    ui_vendor.add_argument(
        "--hashed",
        action="store_true",
        help="rename each file with a content hash, for immutable caching",
    )
    ui_vendor.set_defaults(func=_cmd_ui_vendor)

    ui_vendor_theme = ui_sub.add_parser(
        "vendor-theme",
        help="render the per-event :root{--color-brand-*} CSS block for a profile",
    )
    ui_vendor_theme.add_argument("path", nargs="?", default=None, help="event-profile.yaml path")
    ui_vendor_theme.add_argument("--out", default=None, help="write to this file instead of stdout")
    ui_vendor_theme.set_defaults(func=_cmd_ui_vendor_theme)

    mirror = sub.add_parser("mirror", help="build-time asset mirroring")
    mirror_sub = mirror.add_subparsers(dest="subcommand", required=True)

    mirror_run = mirror_sub.add_parser(
        "run", help="fetch a mirror spec's assets into a destination directory"
    )
    mirror_run.add_argument("--spec", required=True, help="path to a mirror-spec YAML file")
    mirror_run.add_argument("--dest", required=True, help="destination directory")
    mirror_run.add_argument(
        "--force", action="store_true", help="re-fetch every asset, even if already present"
    )
    mirror_run.add_argument(
        "--quiet", action="store_true", help="suppress the report (exit code still reflects it)"
    )
    mirror_run.set_defaults(func=_cmd_mirror_run)

    azure = sub.add_parser(
        "azure",
        help="provision and maintain an event's apps on Azure",
        add_help=False,  # the zsh toolkit owns --help
    )
    azure.add_argument("rest", nargs=argparse.REMAINDER)
    azure.set_defaults(func=_cmd_azure)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
