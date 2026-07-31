"""Drupal Webform ingest: coercion primitives, field mapping, one parser.

Public surface::

    from eventkit.drupal import FieldMap, parse_submission, resolve_field_map
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

import yaml

from ..errors import FieldMapError
from .coerce import (
    TRUTHY,
    Name,
    coerce_bool,
    coerce_email,
    coerce_int,
    coerce_multivalue,
    coerce_name,
    coerce_select_other,
    coerce_text,
    coerce_url,
    split_full_name,
    unwrap,
)
from .parse import DrupalSubmissionModel, WebformSubmission, parse_submission
from .schema import ELEMENT_TYPE_KINDS, FieldKind, FieldMap, FieldRule, WebformSchema

if TYPE_CHECKING:  # pragma: no cover
    from ..eventprofile.models import EventProfile

logger = logging.getLogger("eventkit.drupal")

__all__ = [
    "ELEMENT_TYPE_KINDS",
    "TRUTHY",
    "DrupalSubmissionModel",
    "FieldKind",
    "FieldMap",
    "FieldRule",
    "Name",
    "WebformSchema",
    "WebformSubmission",
    "coerce_bool",
    "coerce_email",
    "coerce_int",
    "coerce_multivalue",
    "coerce_name",
    "coerce_select_other",
    "coerce_text",
    "coerce_url",
    "field_map_stub",
    "parse_submission",
    "resolve_field_map",
    "split_full_name",
    "unwrap",
]


def field_map_stub(want: Iterable[str]) -> str:
    """Render a copy-pasteable ``field_map`` YAML stub for the given fields.

    Printed in the :class:`FieldMapError` message. An adopter who misconfigures
    the profile gets the fix in the traceback rather than in a docs page they
    have to go find.
    """
    lines = ["drupal:", "  field_map:", "    fields:"]
    for name in want:
        kind = "email" if name == "email" else "name" if name == "name" else "text"
        required = "true" if name in ("email", "name") else "false"
        lines.append(
            f"      {name}: {{ key: {name}, kind: {kind}, required: {required} }}"
        )
    return "\n".join(lines)


def resolve_field_map(
    profile: "EventProfile",
    *,
    want: Iterable[str],
    base_dir: Path | None = None,
    log_once: bool = True,
) -> FieldMap:
    """Resolve the field map for one app, or fail loudly.

    Resolution order, explicit and logged once at startup:

    1. ``profile.drupal.field_map`` — authoritative, no inference, no warnings.
    2. ``profile.drupal.webform_schema`` — load the YAML and
       :meth:`WebformSchema.infer_field_map`, logging every heuristic at
       ``WARNING``.
    3. Neither — raise :class:`FieldMapError` naming the missing logical fields
       and printing a stub.

    There is deliberately no fourth step. The predecessor's embedded CAARMS
    default meant every adopter silently ran someone else's field map.

    Args:
        want: the logical field names this app requires. Passing the app's own
            list is what lets ``nametag-press`` boot without lodging fields.
    """
    wanted = list(want)
    drupal = profile.drupal

    if drupal.field_map is not None:
        field_map = drupal.field_map
        missing = [name for name in wanted if name not in field_map.fields]
        if missing:
            raise FieldMapError(
                "The event profile's drupal.field_map is missing logical field(s) "
                f"required by this application: {', '.join(sorted(missing))}.\n\n"
                f"Add them:\n\n{field_map_stub(missing)}"
            )
        if log_once:
            logger.info(
                "drupal.field_map source=profile fields=%d elements=%d",
                len(field_map.fields),
                len(field_map.element_keys()),
            )
        return field_map

    if drupal.webform_schema is not None:
        path = Path(drupal.webform_schema)
        if base_dir is not None and not path.is_absolute():
            path = Path(base_dir) / path
        if not path.exists():
            raise FieldMapError(
                f"drupal.webform_schema points at {path}, which does not exist. "
                "Ship the webform export alongside the profile, or declare "
                "drupal.field_map explicitly."
            )
        try:
            schema = WebformSchema.from_path(path)
        except (yaml.YAMLError, ValueError) as exc:
            raise FieldMapError(f"Could not parse webform schema at {path}: {exc}") from exc

        field_map, warnings = schema.infer_field_map(want=wanted)
        for warning in warnings:
            logger.warning("drupal.field_map inference: %s", warning)

        missing = [name for name in wanted if name not in field_map.fields]
        if missing:
            raise FieldMapError(
                f"Could not infer logical field(s) {', '.join(sorted(missing))} from "
                f"the webform schema at {path}. Inference is best-effort; declare "
                f"them explicitly:\n\n{field_map_stub(missing)}"
            )
        if log_once:
            logger.info(
                "drupal.field_map source=inferred path=%s fields=%d warnings=%d",
                path,
                len(field_map.fields),
                len(warnings),
            )
        return field_map

    raise FieldMapError(
        "The event profile declares neither drupal.field_map nor "
        "drupal.webform_schema, so there is no way to read submissions.\n\n"
        "There is no built-in default on purpose: the previous implementation "
        "fell back to a hardcoded CAARMS field map, which meant any other event "
        "silently parsed every registration into empty columns.\n\n"
        f"Declare the mapping:\n\n{field_map_stub(want)}"
    )
