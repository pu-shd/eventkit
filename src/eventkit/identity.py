"""Stable cross-application identity for a person.

THIS MODULE IS A FROZEN CONTRACT.

Five applications in this stack keep their own database. The same human being is
a row in up to four of them, and the *only* thing tying those rows together is
``person_key``. Changing how ``person_key`` is derived silently orphans every
existing row in every database: nothing errors, the old rows simply stop being
findable and the same person is re-created alongside their own history.

So:

* ``person_key`` is versioned (``PERSON_KEY_VERSION``).
* ``tests/unit/test_identity.py`` pins exact golden output vectors. If you
  change the derivation, those tests fail loudly and on purpose. Do not update
  them without a migration that rewrites the key column in every app.
* Prefer the Drupal submission ``uuid``. The CAARMS registration webform has
  always emitted it (``uuid`` element, ``#default_value: '[webform_submission:uuid]'``)
  and no application ever read it; email was used as the join key instead, which
  breaks the moment anyone corrects a typo'd address.

This module imports nothing beyond the standard library. ``IdentityMixin``
needs SQLAlchemy and is therefore resolved lazily via :pep:`562` module
``__getattr__``, so ``import eventkit.identity`` stays free of a SQLAlchemy
dependency for ``link-forge``, which has no database at all.
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass

__all__ = [
    "PERSON_KEY_VERSION",
    "IdentityError",
    # Resolved lazily by the module-level __getattr__ below (PEP 562) so that
    # importing eventkit.identity does not import SQLAlchemy. ruff's F822 cannot
    # see through __getattr__; removing the name would silently drop it from the
    # public API. Covered by TestIdentityMixinIsLazy.
    "IdentityMixin",  # noqa: F822
    "PopulationDiff",
    "diff_populations",
    "normalize_email",
    "person_key",
]

#: Bump only alongside a migration that rewrites ``person_key`` everywhere.
PERSON_KEY_VERSION = 1

_EMAIL_KEY_PREFIX = "email:"
_EMAIL_KEY_LENGTH = 32

# Values that mean "this field is empty" rather than being real data. Drupal
# webforms emit several of these interchangeably depending on element type.
_PLACEHOLDERS = frozenset({"", "0", "none", "null", "n/a", "na", "-", "--", "nil"})


class IdentityError(ValueError):
    """Raised when a record carries no usable identity at all."""


def normalize_email(raw: str | None) -> str | None:
    """Return a canonical form of ``raw``, or ``None`` if there isn't one.

    NFKC-normalises (so full-width and composed characters compare equal),
    strips surrounding whitespace, and lowercases. Matches the ``.strip().lower()``
    that the existing parsers apply, plus the Unicode normalisation they omit.

    The local part of an address is technically case-sensitive per RFC 5321, but
    no mail provider in practice treats it that way, and the existing databases
    were built with lowercased addresses. Preserving that is required for the
    ``person_key`` of an email-keyed row to stay stable across the extraction.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        raw = str(raw)
    cleaned = unicodedata.normalize("NFKC", raw).strip().lower()
    if not cleaned or cleaned in _PLACEHOLDERS:
        return None
    return cleaned


def _clean_uuid(raw: Any) -> str | None:
    """Return a usable Drupal submission uuid, or ``None``.

    Rejects two failure modes that would otherwise be catastrophic:

    * Placeholder strings (``""``, ``"null"``, ``"0"`` …).
    * An **unresolved Drupal token** such as the literal
      ``"[webform_submission:uuid]"``. If the webform element is misconfigured
      or the token is unavailable in the handler's context, Drupal posts the
      token text itself. Accepting it would give *every* registrant the same
      ``person_key`` and collapse the entire roster onto one row. This is not a
      hypothetical: the same class of bug is why the registration form's
      ``#states`` rule silently never fired.
    """
    if raw is None:
        return None
    text = unicodedata.normalize("NFKC", str(raw)).strip().lower()
    if not text or text in _PLACEHOLDERS:
        return None
    if text.startswith("[") and text.endswith("]"):
        return None
    return text


def person_key(*, uuid: str | None, email: str | None) -> str:
    """Derive the stable cross-application key for one person.

    Resolution order:

    1. The Drupal submission ``uuid``, used verbatim (lowercased, stripped).
       Stable across email corrections, which is the whole point.
    2. ``sha256("email:" + normalize_email(email))`` truncated to 32 hex chars.
       Hashed rather than stored raw so that a key appearing in a URL, a log
       line, or a public JSON payload is not itself an email address — the
       poster gallery leaked addresses through exactly that kind of reuse.

    Raises:
        IdentityError: if neither a usable uuid nor a usable email is present.
            Deliberately fatal. Generating a random key here would create a
            duplicate person on every single webhook delivery for that record.
    """
    cleaned_uuid = _clean_uuid(uuid)
    if cleaned_uuid is not None:
        return cleaned_uuid

    cleaned_email = normalize_email(email)
    if cleaned_email is not None:
        digest = hashlib.sha256(f"{_EMAIL_KEY_PREFIX}{cleaned_email}".encode()).hexdigest()
        return digest[:_EMAIL_KEY_LENGTH]

    raise IdentityError(
        "Cannot derive a person_key: both uuid and email are missing or "
        "placeholder values. Reject the submission instead of inventing a key."
    )


def is_uuid_keyed(key: str) -> bool:
    """Whether ``key`` came from a Drupal uuid rather than an email hash.

    Email-derived keys are exactly 32 lowercase hex characters with no dashes;
    Drupal uuids are 36 characters with dashes. Useful for reporting how much of
    a population is on the durable key versus the fragile one.
    """
    return "-" in key or len(key) != _EMAIL_KEY_LENGTH


@dataclass(frozen=True, slots=True)
class PopulationDiff:
    """The result of comparing two applications' rosters.

    ``a``/``b`` are whatever labels the caller passed, so a report can say
    "3 people in lodging are not in nametags" rather than "3 in set A".
    """

    label_a: str
    label_b: str
    only_in_a: tuple[str, ...] = ()
    only_in_b: tuple[str, ...] = ()
    in_both: tuple[str, ...] = ()
    #: person_key -> (value in a, value in b) for fields that disagree.
    conflicts: Mapping[str, Mapping[str, tuple[Any, Any]]] = field(default_factory=dict)

    @property
    def is_clean(self) -> bool:
        return not self.only_in_a and not self.only_in_b and not self.conflicts

    def render(self) -> str:
        """A short operator-facing summary. No email addresses."""
        lines = [
            f"{len(self.in_both)} person(s) in both {self.label_a} and {self.label_b}",
            f"{len(self.only_in_a)} only in {self.label_a}",
            f"{len(self.only_in_b)} only in {self.label_b}",
            f"{len(self.conflicts)} person(s) with conflicting fields",
        ]
        for key, fields in sorted(self.conflicts.items()):
            for name, (va, vb) in sorted(fields.items()):
                lines.append(f"  {key[:8]}… {name}: {self.label_a}={va!r} {self.label_b}={vb!r}")
        return "\n".join(lines)


def _index(records: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    indexed: dict[str, Mapping[str, Any]] = {}
    for record in records:
        key = record.get("person_key")
        if not key:
            key = person_key(uuid=record.get("uuid"), email=record.get("email"))
        indexed[str(key)] = record
    return indexed


def diff_populations(
    a: Iterable[Mapping[str, Any]],
    b: Iterable[Mapping[str, Any]],
    *,
    label_a: str = "a",
    label_b: str = "b",
    compare: Iterable[str] = ("first_name", "last_name", "email"),
) -> PopulationDiff:
    """Compare two rosters by ``person_key``.

    Powers ``<app> identity-drift --against <other-backup.json>``, so a planner
    can see who is missing from which application *before* badges are printed
    rather than at the front desk. Pure; does no I/O.
    """
    index_a = _index(a)
    index_b = _index(b)
    keys_a, keys_b = set(index_a), set(index_b)
    both = keys_a & keys_b

    conflicts: dict[str, dict[str, tuple[Any, Any]]] = {}
    for key in sorted(both):
        record_a, record_b = index_a[key], index_b[key]
        differing: dict[str, tuple[Any, Any]] = {}
        for name in compare:
            va, vb = record_a.get(name), record_b.get(name)
            if name == "email":
                va, vb = normalize_email(va), normalize_email(vb)
            if va is not None and vb is not None and va != vb:
                differing[name] = (va, vb)
        if differing:
            conflicts[key] = differing

    return PopulationDiff(
        label_a=label_a,
        label_b=label_b,
        only_in_a=tuple(sorted(keys_a - keys_b)),
        only_in_b=tuple(sorted(keys_b - keys_a)),
        in_both=tuple(sorted(both)),
        conflicts=conflicts,
    )


def _build_identity_mixin() -> type:
    """Construct ``IdentityMixin`` on first access, importing SQLAlchemy then."""
    from sqlalchemy import Integer, String
    from sqlalchemy.orm import Mapped, declarative_mixin, mapped_column

    # This module uses PEP 563 string annotations, so `Mapped[str]` below is the
    # *text* "Mapped[str]" until something resolves it. SQLAlchemy resolves a
    # mixin's annotations against `sys.modules[cls.__module__].__dict__` — this
    # module's globals — at the moment an application subclasses the mixin. The
    # imports above are function-locals, so without this the resolution fails
    # with "Could not interpret annotation Mapped[str]" in the *application's*
    # traceback, pointing at the app's model rather than at this line.
    #
    # Publishing the names here keeps SQLAlchemy an optional import (nothing is
    # imported until first access) while making the annotations resolvable.
    globals().update(
        {"Mapped": Mapped, "String": String, "Integer": Integer}
    )

    @declarative_mixin
    class IdentityMixin:
        """Person-shaped columns shared by every app's roster model.

        ``person_key`` is the primary key rather than a surrogate uuid so that a
        cross-application join is possible without a lookup table, and so that a
        backup from one app can be diffed against another's directly.
        """

        person_key: Mapped[str] = mapped_column(String(64), primary_key=True)
        drupal_uuid: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
        drupal_sid: Mapped[int | None] = mapped_column(Integer, index=True, default=None)
        serial_number: Mapped[int | None] = mapped_column(Integer, index=True, default=None)
        email_address: Mapped[str | None] = mapped_column(String(320), index=True, default=None)
        first_name: Mapped[str | None] = mapped_column(String(255), default=None)
        last_name: Mapped[str | None] = mapped_column(String(255), default=None)

        @property
        def full_name(self) -> str:
            return " ".join(p for p in (self.first_name, self.last_name) if p).strip()

    return IdentityMixin


def __getattr__(name: str) -> Any:
    """Lazily resolve ``IdentityMixin`` so SQLAlchemy stays an optional import."""
    if name == "IdentityMixin":
        mixin = _build_identity_mixin()
        globals()["IdentityMixin"] = mixin
        return mixin
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
