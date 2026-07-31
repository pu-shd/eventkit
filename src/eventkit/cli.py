"""The ``eventkit`` console script.

Only the verbs backed by built modules are registered. An unbuilt verb is absent
rather than stubbed, so ``eventkit azure deploy`` fails with "not built yet in
v0.1" instead of half-provisioning a subscription.

Currently useful, and used by CI:

    eventkit profile validate [PATH]     exit 0/1, human-readable error report
    eventkit profile public   [PATH]     the exact JSON served at /api/event-profile
    eventkit profile checkin-keys [PATH] the legacy -> ISO check-in key mapping
    eventkit fieldmap check   [PATH]     resolve a field map and report unmapped risk
    eventkit version
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from . import __version__

#: Verbs designed but not implemented in v0.1. Named explicitly so the error
#: message tells an operator the truth rather than "invalid choice".
NOT_YET_BUILT = {
    "azure": "the zsh bootstrap toolkit (deploy/resume/teardown/doctor/gate)",
    "db": "Alembic wiring (init/upgrade/stamp/current)",
    "ui": "asset vendoring (vendor/vendor-theme)",
    "mirror": "build-time Drupal asset mirroring",
    "import": "the generalized bulk importer",
}


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


def _cmd_not_built(args: argparse.Namespace) -> int:
    what = NOT_YET_BUILT[args.command]
    print(
        f"`eventkit {args.command}` is not built yet in v{__version__}: {what}.\n"
        f"See the README's 'Not yet built' section.",
        file=sys.stderr,
    )
    return 2


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

    for verb, description in NOT_YET_BUILT.items():
        placeholder = sub.add_parser(verb, help=f"(not built in v0.1) {description}")
        placeholder.add_argument("rest", nargs="*")
        placeholder.set_defaults(func=_cmd_not_built)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
