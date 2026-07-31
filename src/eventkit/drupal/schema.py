"""Configurable mapping from webform element keys to logical field names.

This replaces ``ticketed/backend/schema_parser.py`` (248 lines), and in
particular replaces its embedded ``DEFAULT_SCHEMA_YAML``.

The old mechanism was: ``load_schema()`` looks for ``webform-schema.yml`` in the
repo root, then in the CWD, and falls back to a 55-line CAARMS schema baked into
the source. Because no ``webform-schema.yml`` ever shipped in the image, the
CAARMS fallback *always* won. Any adopter deploying that code silently ran the
CAARMS field map against their own webform, and the failure mode is not an
error: registrations parse, fields come back ``None``, rows are written with
missing data, and nobody finds out until the front desk.

So there is no embedded default here. Resolution is explicit, logged once at
startup, and absent configuration is a startup failure — see
``eventkit.drupal.resolve_field_map``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "FieldKind",
    "FieldMap",
    "FieldRule",
    "WebformSchema",
]

FieldKind = Literal[
    "text",
    "email",
    "name",
    "bool",
    "int",
    "select",
    "select_other",
    "multiselect",
    "url",
]

#: Drupal element ``#type`` -> the coercion kind it should use.
ELEMENT_TYPE_KINDS: Mapping[str, FieldKind] = {
    "email": "email",
    "email_confirm": "email",
    "webform_email_confirm": "email",
    "webform_name": "name",
    "checkbox": "bool",
    "checkboxes": "multiselect",
    "webform_checkboxes_other": "multiselect",
    "number": "int",
    "select": "select",
    "radios": "select",
    "webform_select_other": "select_other",
    "webform_radios_other": "select_other",
    "url": "url",
    "textfield": "text",
    "textarea": "text",
    "webform_markup": "text",
    "hidden": "text",
    "value": "text",
}

#: Substrings used when guessing which element supplies a logical field. Only
#: consulted when an exact key match fails, and every hit produces a warning.
_INFERENCE_HINTS: Mapping[str, tuple[str, ...]] = {
    "email": ("email", "mail"),
    "name": ("name",),
    "first_name": ("first", "given"),
    "last_name": ("last", "family", "surname"),
    "uuid": ("uuid",),
    "sid": ("sid", "submission_id"),
    "serial": ("serial",),
}


class FieldRule(BaseModel):
    """How to obtain one logical field from a submission."""

    model_config = ConfigDict(extra="forbid")

    #: Element key, or several tried in order. A list is how you survive a
    #: rename: keep the old key as a fallback for one event cycle.
    key: str | list[str]
    kind: FieldKind = "text"
    required: bool = False
    default: Any = None

    @field_validator("key")
    @classmethod
    def _non_empty(cls, value: str | list[str]) -> str | list[str]:
        if isinstance(value, list):
            if not value or any(not str(k).strip() for k in value):
                raise ValueError("key list must be non-empty and contain no blank keys")
        elif not value.strip():
            raise ValueError("key must not be blank")
        return value

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(self.key) if isinstance(self.key, list) else (self.key,)


class FieldMap(BaseModel):
    """The complete element-key-to-logical-field mapping for one webform."""

    model_config = ConfigDict(extra="forbid")

    fields: dict[str, FieldRule] = Field(default_factory=dict)

    def logical_keys(self) -> set[str]:
        return set(self.fields)

    def element_keys(self) -> set[str]:
        """Every webform element key this map reads, across all fallbacks."""
        return {k for rule in self.fields.values() for k in rule.keys}

    def required_keys(self) -> set[str]:
        return {name for name, rule in self.fields.items() if rule.required}

    def rule(self, logical_name: str) -> FieldRule | None:
        return self.fields.get(logical_name)

    def merged_with(self, other: FieldMap) -> FieldMap:
        """Return a copy where ``other``'s rules win. Used for per-app overlays."""
        merged = dict(self.fields)
        merged.update(other.fields)
        return FieldMap(fields=merged)

    @classmethod
    def from_pairs(cls, pairs: Mapping[str, str]) -> FieldMap:
        """Convenience for the common ``{logical: element_key}`` shape."""
        return cls(fields={name: FieldRule(key=key) for name, key in pairs.items()})


def _flatten_elements(
    raw: Mapping[str, Any],
    *,
    parent: str | None = None,
    out: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Flatten a Drupal element tree into ``{element_key: properties}``.

    Container elements — ``fieldset``, ``details``, ``container``, ``webform_flexbox``
    — nest their children in the YAML export, but **submission data is flat**: a
    fieldset is presentational and its children post as top-level keys. So the
    schema has to be read flat to match the payloads it is used to interpret.

    This is not hypothetical. The CAARMS registration export nests
    ``faculty_adviser_name``, ``poster_title`` and ``poster_presentation_abstract``
    under a ``poster_presentation_details`` fieldset, and ``gender_identity``,
    ``roommate_preference`` and ``identified_roommate`` under ``lodging_section``.
    Reading only the top level silently loses all six — inference reports "could
    not infer an element" for each, and an adopter who trusted it would run with
    the entire lodging and poster halves of their form unmapped.

    A nested key wins over an outer one of the same name, matching Drupal, which
    rejects duplicate element keys anywhere in a form. Each flattened element
    records its container in ``#eventkit_parent`` so callers can report location.
    """
    if out is None:
        out = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        name = str(key)
        children = {
            k: v
            for k, v in value.items()
            if isinstance(k, str) and not k.startswith("#") and isinstance(v, dict)
        }
        if WebformSchema._is_element(value) or not children:
            properties = {k: v for k, v in value.items() if k not in children}
            if parent is not None:
                properties["#eventkit_parent"] = parent
            out[name] = properties
        if children:
            _flatten_elements(children, parent=name, out=out)
    return out


class WebformSchema(BaseModel):
    """A parsed Drupal webform element definition.

    These exports are element-only bodies — the top level is a mapping of
    element key to its ``#``-prefixed properties, with no config envelope. That
    matters for import (see ``drupal-event-forms/docs/IMPORT.md``): they are not
    directly ``drush cim``-able without being wrapped.
    """

    model_config = ConfigDict(extra="forbid")

    elements: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @staticmethod
    def _is_element(value: Any) -> bool:
        """A mapping is an element if it carries at least one ``#property``."""
        return isinstance(value, dict) and any(
            isinstance(k, str) and k.startswith("#") for k in value
        )

    @classmethod
    def from_yaml_text(cls, text: str) -> WebformSchema:
        raw = yaml.safe_load(text) or {}
        if not isinstance(raw, dict):
            raise ValueError("webform schema must be a YAML mapping of element keys")
        # Tolerate a full config envelope as well as a bare element body.
        if "elements" in raw and isinstance(raw["elements"], (dict, str)):
            inner = raw["elements"]
            if isinstance(inner, str):  # Drupal stores elements as a YAML string
                inner = yaml.safe_load(inner) or {}
            raw = inner
        return cls(elements=_flatten_elements(raw))

    @classmethod
    def from_path(cls, path: str | Path) -> WebformSchema:
        resolved = Path(path)
        return cls.from_yaml_text(resolved.read_text(encoding="utf-8"))

    def container_of(self, key: str) -> str | None:
        """The container element a key was nested under, if any."""
        return (self.elements.get(key) or {}).get("#eventkit_parent")

    def element_type(self, key: str) -> str | None:
        element = self.elements.get(key)
        if not element:
            return None
        value = element.get("#type")
        return str(value) if value is not None else None

    def title(self, key: str) -> str:
        element = self.elements.get(key) or {}
        return str(element.get("#title", "")).lower()

    def kind_for(self, key: str) -> FieldKind:
        element_type = self.element_type(key)
        return ELEMENT_TYPE_KINDS.get(element_type or "", "text")

    def is_required(self, key: str) -> bool:
        element = self.elements.get(key) or {}
        return bool(element.get("#required", False))

    def infer_field_map(
        self, *, want: Iterable[str]
    ) -> tuple[FieldMap, list[str]]:
        """Best-effort inference of a field map for the logical names in ``want``.

        This is the genuinely useful half of the old ``get_field_mappings``: it
        saves an adopter from hand-writing a map for a form whose element keys
        already match the canonical names.

        It never guesses silently. Every logical field resolved by anything other
        than an exact key match produces a warning string, and any field it
        cannot resolve at all is simply absent from the returned map so that
        required-field validation can report it by name.

        Returns:
            ``(field_map, warnings)``.
        """
        warnings: list[str] = []
        rules: dict[str, FieldRule] = {}

        for logical in want:
            # 1. Exact key match — the good case, no warning.
            if logical in self.elements:
                rules[logical] = FieldRule(
                    key=logical,
                    kind=self.kind_for(logical),
                    required=self.is_required(logical),
                )
                continue

            # 2. Heuristic match on element type, then key/title substrings.
            match = self._infer_one(logical)
            if match is None:
                warnings.append(
                    f"could not infer an element for logical field {logical!r}; "
                    f"declare it explicitly under drupal.field_map"
                )
                continue

            key, reason = match
            rules[logical] = FieldRule(
                key=key,
                kind=self.kind_for(key),
                required=self.is_required(key),
            )
            warnings.append(
                f"inferred logical field {logical!r} from element {key!r} ({reason}); "
                f"confirm this is correct and pin it under drupal.field_map"
            )

        return FieldMap(fields=rules), warnings

    def _infer_one(self, logical: str) -> tuple[str, str] | None:
        """Return ``(element_key, reason)`` for a heuristic match, or ``None``."""
        # Strong signal: a unique element of the semantically right type.
        wanted_types: tuple[str, ...] = ()
        if logical == "email":
            wanted_types = ("email", "webform_email_confirm", "email_confirm")
        elif logical == "name":
            wanted_types = ("webform_name",)

        if wanted_types:
            typed = [k for k in self.elements if self.element_type(k) in wanted_types]
            if len(typed) == 1:
                return typed[0], f"only element of type {self.element_type(typed[0])!r}"
            if typed:
                # Prefer a non-confirm element; a confirm composite is a fallback.
                plain = [k for k in typed if self.element_type(k) == "email"]
                chosen = plain[0] if plain else typed[0]
                return chosen, f"first of {len(typed)} elements of a matching type"

        # Weaker signal: key or title contains a hint substring.
        for hint in _INFERENCE_HINTS.get(logical, ()):
            for key in self.elements:
                if hint in key.lower():
                    return key, f"key contains {hint!r}"
            for key in self.elements:
                if hint in self.title(key):
                    return key, f"title contains {hint!r}"

        return None
