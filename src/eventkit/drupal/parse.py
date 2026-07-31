"""The one parser. Used by both the webhook and the bulk importer.

``tests/unit/drupal/test_parity.py`` asserts that property by construction: both
entry points call :func:`parse_submission` on the same payload and must produce
identical output. ``posted/backend/import_existing.py:92`` already had it by
convention; convention is not enough when there are five apps.
"""

from __future__ import annotations

from typing import Any, ClassVar, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..identity import IdentityError, person_key
from .coerce import (
    Name,
    coerce_bool,
    coerce_email,
    coerce_int,
    coerce_multivalue,
    coerce_name,
    coerce_select_other,
    coerce_text,
    coerce_url,
    unwrap,
)
from .schema import FieldKind, FieldMap, FieldRule

__all__ = [
    "DrupalSubmissionModel",
    "WebformSubmission",
    "parse_submission",
]

#: Logical names that map onto dedicated attributes of :class:`WebformSubmission`
#: rather than living in the generic ``fields`` bag.
_PROMOTED = frozenset({"email", "name", "first_name", "last_name", "sid", "serial", "uuid"})

# Drupal submission metadata. Unlike element keys, these names are fixed by the
# ``[webform_submission:*]`` tokens rather than chosen by the form author, so
# defaulting them is safe — the objection to the old embedded default schema was
# that it guessed at *adopter-named element* keys.
_SID_KEYS = ("sid", "submission_id")
_SERIAL_KEYS = ("serial", "serial_number")
_UUID_KEYS = ("uuid", "submission_uuid")

_COERCERS: Mapping[FieldKind, Any] = {
    "text": coerce_text,
    "email": coerce_email,
    "bool": coerce_bool,
    "int": coerce_int,
    "select": coerce_select_other,
    "select_other": coerce_select_other,
    "multiselect": coerce_multivalue,
    "url": coerce_url,
}


def _lookup(rule: FieldRule, root: Mapping[str, Any], data: Mapping[str, Any]) -> Any:
    """Find the first present value for ``rule`` across its fallback keys.

    Checks the data block before the root, because element values live in the
    data block and a same-named root key would be submission metadata.

    "Present" means the key exists and is not ``None``; an explicit empty string
    counts as present so that clearing a field in Drupal clears it here too.
    """
    for key in rule.keys:
        if key in data and data[key] is not None:
            return data[key]
        if key in root and root[key] is not None:
            return root[key]
    return None


def _first_present(
    keys: tuple[str, ...], root: Mapping[str, Any], data: Mapping[str, Any]
) -> Any:
    """Root-first metadata lookup, matching the predecessor parsers."""
    for key in keys:
        if root.get(key) is not None:
            return root[key]
    for key in keys:
        if data.get(key) is not None:
            return data[key]
    return None


class WebformSubmission(BaseModel):
    """Canonical, app-agnostic form of one Drupal webform submission."""

    model_config = ConfigDict(extra="forbid")

    sid: int | None = None
    serial: int | None = None
    #: The Drupal submission uuid. Carry it — it is the identity fix.
    uuid: str | None = None
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    #: Logical field name -> coerced value, for everything not promoted above.
    fields: dict[str, Any] = Field(default_factory=dict)

    #: Element keys present in the payload that no rule consumed. Surfaced at
    #: ``GET /api/webhook/status`` so a live element rename in Drupal shows up as
    #: a warning within one submission instead of as silently dropped data.
    unmapped_keys: list[str] = Field(default_factory=list)
    #: Logical names declared ``required`` whose value resolved to nothing.
    missing_required: list[str] = Field(default_factory=list)

    #: The untouched payload, for replay. Excluded from dumps and from repr so it
    #: cannot leak into a log line or a public JSON response by accident.
    raw: dict[str, Any] = Field(default_factory=dict, repr=False, exclude=True)

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.first_name, self.last_name) if p).strip()

    @property
    def name(self) -> Name:
        return Name(self.first_name, self.last_name)

    def get(self, logical_name: str, default: Any = None) -> Any:
        """Read a logical field, promoted or otherwise."""
        if logical_name in _PROMOTED:
            if logical_name == "name":
                return self.full_name or default
            value = getattr(self, logical_name, None)
            return default if value is None else value
        value = self.fields.get(logical_name)
        return default if value is None else value

    @property
    def person_key(self) -> str | None:
        """The stable cross-app key, or ``None`` if this record cannot be keyed.

        Returns ``None`` rather than raising so that a caller can reject the
        submission with a 400 and a clear message instead of a 500.
        """
        try:
            return person_key(uuid=self.uuid, email=self.email)
        except IdentityError:
            return None

    @property
    def is_valid(self) -> bool:
        return self.person_key is not None and not self.missing_required


def parse_submission(
    payload: Mapping[str, Any],
    field_map: FieldMap,
    *,
    track_unmapped: bool = True,
) -> WebformSubmission:
    """Parse a Remote Post payload into a :class:`WebformSubmission`.

    This is the only function that turns a Drupal payload into application data.
    Both the webhook route and the bulk importer must call it, so that a fix to
    composite-email handling cannot land in one path and not the other — which
    is exactly how ``posted`` ended up understanding ``select_other`` on the
    nametags webhook but not on the poster webhook.
    """
    root, data = unwrap(payload)

    promoted: dict[str, Any] = {}
    fields: dict[str, Any] = {}
    missing_required: list[str] = []
    consumed: set[str] = set()
    # Composite-name results are applied after the loop so that precedence does
    # not depend on the order of keys in the YAML mapping. Documented rule:
    # explicit `first_name`/`last_name` rules win, and a `name` composite fills
    # whatever they leave empty.
    #
    # `posted`'s parser had the opposite precedence for dict composites — the
    # composite overwrote already-extracted individual fields — but no shipped
    # field map declares both, so there is no observable behaviour change, and
    # letting an explicit mapping lose to an inferred one is the wrong default.
    name_candidates: list[Name] = []

    for logical, rule in field_map.fields.items():
        raw_value = _lookup(rule, root, data)
        for key in rule.keys:
            if key in data or key in root:
                consumed.add(key)

        if raw_value is None and rule.default is not None:
            raw_value = rule.default

        if rule.kind == "name":
            parsed = coerce_name(raw_value)
            name_candidates.append(parsed)
            if rule.required and not (parsed.first or parsed.last):
                missing_required.append(logical)
            continue

        coercer = _COERCERS.get(rule.kind, coerce_text)
        value = coercer(raw_value)

        if rule.required:
            empty = value is None or (rule.kind == "multiselect" and not value)
            # A required checkbox meaning "must be ticked" is not a thing Drupal
            # expresses this way, so False is a legitimate value, not a miss.
            if empty:
                missing_required.append(logical)

        if logical in _PROMOTED:
            if logical in ("first_name", "last_name"):
                if value is not None:
                    promoted[logical] = value
            else:
                promoted[logical] = value
        else:
            fields[logical] = value

    # Apply composite-name results to whatever explicit rules left empty.
    for candidate in name_candidates:
        if promoted.get("first_name") is None and candidate.first is not None:
            promoted["first_name"] = candidate.first
        if promoted.get("last_name") is None and candidate.last is not None:
            promoted["last_name"] = candidate.last

    # Submission metadata: use an explicit rule if the map declares one,
    # otherwise fall back to Drupal's fixed token names.
    sid = promoted.get("sid")
    if sid is None:
        sid = coerce_int(_first_present(_SID_KEYS, root, data))
    serial = promoted.get("serial")
    if serial is None:
        serial = coerce_int(_first_present(_SERIAL_KEYS, root, data))
    submission_uuid = promoted.get("uuid")
    if submission_uuid is None:
        submission_uuid = coerce_text(_first_present(_UUID_KEYS, root, data))

    unmapped: list[str] = []
    if track_unmapped:
        metadata_keys = set(_SID_KEYS) | set(_SERIAL_KEYS) | set(_UUID_KEYS) | {
            "data",
            "webform_id",
            "token",
        }
        # Keys beginning with "#" are Drupal element properties rather than
        # values; keys beginning with "_" are reserved for annotations (the
        # shipped golden fixtures carry a "_comment"). Neither is a renamed
        # element, so neither should raise an operator's attention.
        unmapped = sorted(
            key
            for key in data
            if key not in consumed
            and key not in metadata_keys
            and not key.startswith("#")
            and not key.startswith("_")
        )

    return WebformSubmission(
        sid=coerce_int(sid),
        serial=coerce_int(serial),
        uuid=coerce_text(submission_uuid),
        email=promoted.get("email"),
        first_name=promoted.get("first_name"),
        last_name=promoted.get("last_name"),
        fields=fields,
        unmapped_keys=unmapped,
        missing_required=sorted(set(missing_required)),
        raw=dict(payload) if isinstance(payload, Mapping) else {},
    )


class DrupalSubmissionModel(BaseModel):
    """Optional base for apps that want a typed payload model.

    Subclass it, declare typed fields, and set ``__field_map__`` (or leave it
    ``None`` to use the ambient profile's map). The ``mode="before"`` validator
    is inherited, so each app stops hand-rolling one.

    Prefer :func:`parse_submission` directly for new code; this exists to make
    the in-place adoption inside ``ticketed`` and ``posted`` a small diff.
    """

    model_config = ConfigDict(extra="ignore")

    __field_map__: ClassVar[FieldMap | None] = None

    @classmethod
    def _resolve_field_map(cls) -> FieldMap:
        if cls.__field_map__ is not None:
            return cls.__field_map__
        from ..eventprofile.load import get_profile

        field_map = get_profile().drupal.field_map
        if field_map is None:  # pragma: no cover - guarded at profile load
            raise ValueError(
                f"{cls.__name__} has no __field_map__ and the active event profile "
                f"declares no drupal.field_map"
            )
        return field_map

    @model_validator(mode="before")
    @classmethod
    def _parse(cls, values: Any) -> Any:
        if not isinstance(values, Mapping):
            return values
        # Already-parsed input (e.g. round-tripping a model) passes through.
        if isinstance(values, dict) and values.get("__parsed__") is True:
            return values
        submission = parse_submission(values, cls._resolve_field_map())
        merged: dict[str, Any] = {
            "email_address": submission.email,
            "first_name": submission.first_name,
            "last_name": submission.last_name,
            "sid": submission.sid,
            "serial": submission.serial,
            "uuid": submission.uuid,
        }
        merged.update(submission.fields)
        return {k: v for k, v in merged.items() if k in cls.model_fields or True}
