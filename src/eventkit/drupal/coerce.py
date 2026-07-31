"""Total coercion primitives for Drupal Webform Remote Post payloads.

Every function here is total: it accepts ``Any`` and returns a value or ``None``
rather than raising. No logging, no configuration, no I/O. That is what makes
them table-testable, and it is the reason the three hand-rolled parsers they
replace disagreed with each other — each grew its own inline special cases.

The three predecessors:

* ``posted/backend/schemas.py:16-76``  (poster presenter payload)
* ``posted/backend/schemas.py:111-193`` (nametags payload, ~85% identical)
* ``ticketed/backend/schemas.py:17-68`` + ``schema_parser.py:159-245``

They disagreed in ways that mattered: only the nametags parser understood
``select_other`` composites, so a registrant who typed a custom gender identity
got ``None`` in one app and their answer in another. Only the ticketed parser
coerced checkbox truthiness or handled ``destination_url``. Only the ticketed
parser lowercased email, and it did so in a separate validator that ran *after*
the mode="before" hook.

Behaviour here is a deliberate union of all three, chosen so that no existing
payload parses differently than it does today except where today's answer was
plainly wrong.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, NamedTuple

__all__ = [
    "FALSY",
    "TRUTHY",
    "Name",
    "coerce_bool",
    "coerce_email",
    "coerce_int",
    "coerce_multivalue",
    "coerce_name",
    "coerce_select_other",
    "coerce_text",
    "coerce_url",
    "split_full_name",
    "unwrap",
]

#: Truthy spellings Drupal checkboxes and hidden fields actually emit. Superset
#: of ``ticketed``'s ``("1","true","yes","on","checked")``; the additions are
#: strictly more permissive so nothing that parsed as true stops doing so.
TRUTHY: frozenset[str] = frozenset({"1", "true", "yes", "on", "checked", "y", "t"})

#: Composite-email sub-keys, in precedence order. ``webform_email_confirm``
#: posts ``{"mail_1": ..., "mail_2": ...}``; other element types use ``value``.
_EMAIL_SUBKEYS = ("mail_1", "email", "value", "mail")

_NAME_FIRST_SUBKEYS = ("first", "first_name", "given", "given_name")
_NAME_LAST_SUBKEYS = ("last", "last_name", "family", "family_name", "surname")

#: Sentinel Drupal uses in a ``webform_select_other`` element to mean "the user
#: chose Other and typed something in the companion text field".
_OTHER_SENTINEL = "_other_"


def unwrap(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Split a Remote Post body into ``(root, data_block)``.

    Drupal's Remote Post handler can be configured to nest submission values
    under a ``data`` key or to post them flat at the root. Both shapes are in
    production against these apps right now, which is why all three predecessor
    parsers open with the same four lines.

    ``sid`` and ``serial`` are read from the root in preference to the data
    block (the root is where the handler puts submission metadata), while
    element values come from the data block — so callers need both.
    """
    if not isinstance(payload, Mapping):
        return {}, {}
    data_block = payload.get("data")
    if not isinstance(data_block, Mapping):
        data_block = payload
    return payload, data_block


def coerce_text(value: Any) -> str | None:
    """Strip a scalar to text, mapping empty and ``None`` alike to ``None``.

    Empty string and absent must be indistinguishable downstream. Today they are
    not: a registrant who leaves an optional text field blank is stored as ``""``
    in some columns and ``None`` in others, so queries need
    ``(col == "") | (col.is_(None))`` to be correct and mostly aren't.
    """
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, Mapping):
        # A composite arrived where a scalar was expected; try the usual suspects
        # rather than stringifying a dict into the database.
        for key in ("value", "text", *_EMAIL_SUBKEYS):
            if key in value:
                return coerce_text(value[key])
        return None
    if isinstance(value, (list, tuple)):
        for item in value:
            text = coerce_text(item)
            if text is not None:
                return text
        return None
    return None


def coerce_email(value: Any) -> str | None:
    """Extract a single lowercased email address from any Drupal email shape.

    ``str`` -> stripped and lowercased.
    ``dict`` -> first present of ``mail_1``, ``email``, ``value``, ``mail``
    (the ``webform_email_confirm`` composite).
    ``list`` -> first non-empty entry.
    Anything else -> ``None``.

    Lowercasing happens here rather than in a separate validator so that the
    webhook and the bulk importer cannot diverge — ``ticketed`` lowercased in a
    ``field_validator`` that the importer never invoked.
    """
    if isinstance(value, Mapping):
        for key in _EMAIL_SUBKEYS:
            candidate = value.get(key)
            if candidate:
                return coerce_email(candidate)
        return None
    if isinstance(value, (list, tuple)):
        for item in value:
            email = coerce_email(item)
            if email is not None:
                return email
        return None
    text = coerce_text(value)
    return text.lower() if text else None


class Name(NamedTuple):
    first: str | None
    last: str | None

    @property
    def full(self) -> str:
        return " ".join(p for p in (self.first, self.last) if p).strip()


def split_full_name(value: str) -> Name:
    """Split a bare name string into first and last.

    ``"Ada Lovelace"``   -> ``("Ada", "Lovelace")``
    ``"Ada"``            -> ``("Ada", None)``
    ``"Ada B Lovelace"`` -> ``("Ada", "B Lovelace")``

    ``split(None, 1)`` matches all three predecessors exactly. It is wrong for
    multi-word given names and for ``"Lovelace, Ada"``, but it is what the live
    databases were populated with, so changing it here would silently disagree
    with stored rows. ``lodging-planner``'s name *matcher* is where the smarter
    handling belongs, because that is a query-time concern.
    """
    stripped = (value or "").strip()
    if not stripped:
        return Name(None, None)
    parts = stripped.split(None, 1)
    if len(parts) == 2:
        return Name(parts[0], parts[1])
    return Name(parts[0], None)


def coerce_name(value: Any) -> Name:
    """Extract ``(first, last)`` from a ``webform_name`` composite or a string."""
    if isinstance(value, Mapping):
        first = None
        last = None
        for key in _NAME_FIRST_SUBKEYS:
            if value.get(key):
                first = coerce_text(value[key])
                break
        for key in _NAME_LAST_SUBKEYS:
            if value.get(key):
                last = coerce_text(value[key])
                break
        if first is None and last is None:
            # Composite present but none of the expected sub-keys; it may hold a
            # single "value" holding the whole name.
            text = coerce_text(value)
            if text:
                return split_full_name(text)
        return Name(first, last)
    # Only a string can carry a name. Anything else — an int, a list, a bool —
    # is junk from a misconfigured element, and stringifying it would create a
    # registrant literally named "0". The documented contract is
    # "dict -> parts, str -> split, None/other -> (None, None)".
    if not isinstance(value, str):
        return Name(None, None)
    text = coerce_text(value)
    if text is None:
        return Name(None, None)
    return split_full_name(text)


def coerce_select_other(value: Any) -> str | None:
    """Resolve a ``webform_select_other`` composite to the effective answer.

    ``{"select": "_other_", "other": "Genderqueer"}`` -> ``"Genderqueer"``
    ``{"select": "Woman", "other": ""}``              -> ``"Woman"``
    ``{"select": "", "other": "Something"}``          -> ``"Something"``

    Taken from ``posted/backend/schemas.py:162-174``, the only predecessor that
    implemented it. The nametags webhook understood custom gender identities and
    the poster webhook did not, so the same registrant's answer differed by app.
    """
    if isinstance(value, Mapping):
        if "select" in value or "other" in value:
            selected = coerce_text(value.get("select"))
            other = coerce_text(value.get("other"))
            if selected is None or selected == _OTHER_SENTINEL:
                return other
            return selected
        return coerce_text(value)
    return coerce_text(value)


def coerce_bool(value: Any) -> bool:
    """Coerce a Drupal checkbox or flag to a real boolean.

    Mirrors ``ticketed/backend/schemas.py:58-68`` and extends it to the wider
    ``TRUTHY`` set. Never raises; anything unrecognised is ``False``, matching
    the predecessor's ``return False`` fallthrough.

    Note that this is the fix for the three-valued string problem: ``lodging``,
    ``student``, ``presenting_poster`` and ``attendee_status`` are ``String``
    columns today holding ``"Yes"`` / ``"yes"`` / ``None``, which is why queries
    read ``(lodging == "Yes") | (lodging == "yes")``. Coercing at the boundary is
    the only cheap moment to stop all five apps inheriting that.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in TRUTHY
    if isinstance(value, Mapping):
        # Checkbox groups post {option: truthy}; any selection is "true".
        return any(coerce_bool(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(coerce_bool(v) for v in value)
    return False


def coerce_int(value: Any) -> int | None:
    """Coerce to ``int``, mapping empty string and unparseable input to ``None``.

    ``""`` -> ``None``, ``"12"`` -> ``12``, ``" 7 "`` -> ``7``, ``12.0`` -> ``12``.
    The predecessors used a bare ``int(sid)`` that raises ``ValueError`` on
    ``""`` — a 500 on the webhook path, which Drupal logs as a failed handler
    and then forgets, losing the registration.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = coerce_text(value)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        try:
            return int(float(text))
        except ValueError:
            return None


def coerce_url(value: Any) -> str | None:
    """Strip a URL-ish value. Deliberately does not validate the scheme.

    ``destination_url`` carries a discount-code slug computed by Twig and is
    sometimes a bare slug rather than an absolute URL; rejecting non-URLs here
    would drop the conditional-ticketing signal entirely.
    """
    return coerce_text(value)


#: Spellings that mean "this checkbox was not ticked". Needed separately from
#: ``TRUTHY`` because Drupal's checkboxes element posts ``{option: option}`` for a
#: selected box — the value is the option key, which is neither truthy nor falsy
#: by word — so "not in TRUTHY" cannot be used as the exclusion test.
FALSY: frozenset[str] = frozenset({"", "0", "false", "no", "off", "n", "f", "none", "null"})


def coerce_multivalue(value: Any) -> list[str]:
    """Normalise a multi-select or checkboxes element to a list of strings.

    ``dict``  -> keys whose value is *selected*: ``{"opt_a": 1, "opt_b": 0}`` and
    ``{"opt_a": "opt_a", "opt_b": false}`` both yield ``["opt_a"]``.
    ``list``  -> non-empty entries, stringified.
    ``str``   -> single-element list.

    A comma-containing string is *not* split: option keys can legitimately hold
    commas, and guessing wrong silently invents options that were never chosen.
    ``morgan-state-…-form.yaml`` uses full URLs as radio option keys, so this is
    not a theoretical concern in this stack.
    """
    if isinstance(value, Mapping):
        selected = []
        for key, raw in value.items():
            text = coerce_text(raw)
            if text is not None and text.strip().lower() not in FALSY:
                selected.append(str(key))
        return selected
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            text = coerce_text(item)
            if text is not None:
                out.append(text)
        return out
    text = coerce_text(value)
    return [text] if text is not None else []
