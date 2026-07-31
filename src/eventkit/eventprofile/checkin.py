"""Check-in day keys and the migration off the legacy ``"6/28"`` format.

Today ``ticketed`` stores per-registrant check-in state as a JSON blob keyed by
``"6/28"``, ``"6/29"``, ``"6/30"``, ``"banquet"``, ``"7/1"``. Those keys are:

* **year-less**, so they collide across events — CAARMS 2027 would write into
  the same key space;
* **ambiguous to parse**, because both ``"7/1"`` and ``"07/01"`` appear in the
  live data, and ``"6/28"`` is not a date without a year;
* **positional in practice**, since the front-end renders them from a hardcoded
  array rather than from data.

So the migration rewrites keys **by position against the profile's schedule**,
not by parsing them as dates. :func:`legacy_key_aliases` derives the mapping
from the profile, which means it is correct for any event whose profile
describes it, and :func:`migrate_checkin_blob` refuses to guess: an unrecognised
key raises rather than being dropped, because dropping it silently discards the
record that someone attended a day.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from ..errors import ContractError
from .models import Schedule

__all__ = [
    "CHECKIN_STATES",
    "CheckinKeyError",
    "canonical_state",
    "legacy_key_aliases",
    "migrate_checkin_blob",
]

#: The four-state cycle the front desk toggles through. Values are the integers
#: already stored in the live database, so they must not be renumbered.
CHECKIN_STATES: Mapping[str, int] = {
    "UNRECORDED": 0,
    "CHECKED_IN": 1,
    "UNSURE": 2,
    "ABSENT": 3,
}

_VALID_STATES = frozenset(CHECKIN_STATES.values())


class CheckinKeyError(ContractError):
    """A check-in blob contains a key that cannot be mapped to the schedule."""


def legacy_key_aliases(schedule: Schedule) -> dict[str, str]:
    """Map every legacy spelling of a check-in key to its canonical ISO key.

    Derived from the profile rather than hardcoded, so it generalises: an adopter
    whose old data used ``M/D`` keys gets the same treatment without editing
    eventkit.

    For each day in the schedule:

    * ``kind="day"`` with a date contributes ``"6/28"`` and ``"06/28"`` (both
      spellings are present in the live data).
    * ``kind="event"`` contributes its label lowercased (``"banquet"``) and the
      trailing segment of its key.
    * every day contributes its own canonical key as an identity mapping, so a
      partially-migrated blob is idempotent under a second run.
    """
    aliases: dict[str, str] = {}

    def add(alias: str, canonical: str) -> None:
        alias = alias.strip().lower()
        if not alias:
            return
        existing = aliases.get(alias)
        if existing is not None and existing != canonical:
            raise CheckinKeyError(
                f"legacy key {alias!r} is ambiguous: it could mean {existing!r} or "
                f"{canonical!r}. Disambiguate by giving these days distinct labels "
                f"in the event profile."
            )
        aliases[alias] = canonical

    for day in schedule.checkin_days:
        add(day.key, day.key)  # identity, for idempotency
        if day.kind == "event":
            if day.label:
                add(day.label, day.key)
            tail = day.key.rsplit("-", 1)[-1]
            add(tail, day.key)
        elif day.date is not None:
            add(f"{day.date.month}/{day.date.day}", day.key)
            add(f"{day.date.month:02d}/{day.date.day:02d}", day.key)
            add(day.date.isoformat(), day.key)

    return aliases


def canonical_state(value: Any) -> int:
    """Coerce a stored state to a valid integer, defaulting to UNRECORDED."""
    if isinstance(value, bool):
        return CHECKIN_STATES["CHECKED_IN"] if value else CHECKIN_STATES["UNRECORDED"]
    if isinstance(value, int) and value in _VALID_STATES:
        return value
    if isinstance(value, str):
        stripped = value.strip().upper()
        if stripped in CHECKIN_STATES:
            return CHECKIN_STATES[stripped]
        try:
            as_int = int(stripped)
        except ValueError:
            return CHECKIN_STATES["UNRECORDED"]
        if as_int in _VALID_STATES:
            return as_int
    return CHECKIN_STATES["UNRECORDED"]


def migrate_checkin_blob(
    raw: str | Mapping[str, Any] | None,
    aliases: Mapping[str, str],
    *,
    strict: bool = True,
) -> str | None:
    """Rewrite one registrant's check-in blob to canonical ISO keys.

    Args:
        raw: the stored value — a JSON string, an already-decoded mapping, or
            ``None``. The column is declared ``JSON`` but was written as a string
            in places, so both shapes exist in the live database.
        aliases: output of :func:`legacy_key_aliases`.
        strict: when ``True`` (the default, and what the Alembic revision uses),
            an unrecognised key raises :class:`CheckinKeyError`. The alternative
            is silently discarding the fact that somebody checked in.

    Returns:
        A JSON string with canonical keys, or ``None`` if there was nothing to
        store. Returns ``None`` rather than ``"{}"`` for an empty result so the
        column stays null-or-meaningful.
    """
    if raw is None:
        return None

    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CheckinKeyError(
                f"check-in blob is not valid JSON: {text[:80]!r} ({exc})"
            ) from exc
    else:
        decoded = raw

    if decoded in (None, {}, []):
        return None
    if not isinstance(decoded, Mapping):
        raise CheckinKeyError(
            f"check-in blob must be a JSON object, got {type(decoded).__name__}"
        )

    migrated: dict[str, int] = {}
    unknown: list[str] = []

    for key, value in decoded.items():
        lookup = str(key).strip().lower()
        canonical = aliases.get(lookup)
        if canonical is None:
            unknown.append(str(key))
            continue
        state = canonical_state(value)
        # If two legacy keys collapse onto one canonical key, keep the more
        # definite record: any positive state beats UNRECORDED.
        previous = migrated.get(canonical)
        if previous is None or (previous == 0 and state != 0):
            migrated[canonical] = state

    if unknown and strict:
        raise CheckinKeyError(
            f"unrecognised check-in key(s) {sorted(unknown)}: not a known legacy "
            f"spelling and not one of the profile's canonical keys "
            f"{sorted(set(aliases.values()))}. Refusing to discard attendance "
            f"data — add the missing day to the event profile's schedule, or "
            f"re-run with strict=False to drop these keys deliberately."
        )

    if not migrated:
        return None
    return json.dumps(migrated, sort_keys=True)
