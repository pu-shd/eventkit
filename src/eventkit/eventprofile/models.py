"""``event-profile.yaml`` — one validated file describing one event.

This is the highest-leverage module in the stack. Everything the five
applications hardcode today reads from here instead: event dates, check-in day
keys, discount-code *variable names*, t-shirt vocabularies, role labels and
colours, lodging vocabularies and rule severities, Avery template choice, and
the affiliation-from-email-domain rule that exists in six separate copies.

Design rules that the type definitions enforce:

* **No secret ever lives here.** Ticket tiers carry ``discount_code_env`` — the
  *name* of an environment variable — never a code. The profile is committed and
  is served to the browser at ``GET /api/event-profile``.
* **Check-in day keys are ISO dates.** ``ticketed/frontend/app.js:1258-1262``
  hardcodes ``"6/28"``, ``"6/29"``, ``"6/30"``, ``"banquet"``, ``"7/1"``:
  year-less keys that collide across events and are ambiguous to parse (both
  ``"7/1"`` and ``"07/01"`` appear in the live data). The pattern on
  :attr:`CheckinDay.key` rejects slashes outright.
* **Every section has a safe default except the ones that cannot.** Adding a
  lodging key must not break ``nametag-press`` startup, which is why
  per-application requirements are declared by the app (see
  :meth:`EventProfile.validate_for_app`) rather than baked in here.

This module imports only pydantic and the standard library, so ``link-forge``
and ``nametag-press`` can read a profile without pulling in FastAPI or
SQLAlchemy. ``tests/unit/test_import_weight.py`` enforces that.
"""

from __future__ import annotations

import datetime as _dt
import re
from collections.abc import Iterable, Mapping
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from ..drupal.schema import FieldMap
from ..errors import EventProfileError

#: Upper snake case with at least one underscore: ``EVENTBRITE_DISCOUNT_GA``.
#: Deliberately stricter than "is it a legal env var name", see TicketTier.
_ENV_VAR_NAME_RE = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+")

#: A deliberately simple email type.
#:
#: pydantic's ``EmailStr`` would be stricter, but it requires the
#: ``email-validator`` package, which in turn pulls in ``dnspython``. That breaks
#: the import-weight contract this package advertises — ``eventkit.eventprofile``
#: must import with only pydantic, PyYAML and the standard library so that
#: ``link-forge`` and ``nametag-press`` stay light. A profile's addresses are
#: operator-authored configuration checked at startup, not untrusted input, so a
#: shape check is proportionate. Addresses arriving from Drupal go through
#: ``eventkit.drupal.coerce_email`` instead.
EmailAddress = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    ),
]

__all__ = [
    "AffiliationRules",
    "Branding",
    "CheckinDay",
    "DrupalConfig",
    "EventInfo",
    "EventProfile",
    "LinkTemplate",
    "Lodging",
    "LodgingRule",
    "LodgingVocab",
    "Match",
    "Nametags",
    "NotifyConfig",
    "Role",
    "Roles",
    "Schedule",
    "Swag",
    "SwagOption",
    "TicketTier",
    "Ticketing",
]

_HEX = r"^#[0-9a-fA-F]{6}$"


class EventInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    short_name: str
    year: int = Field(ge=1900, le=2200)
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,48}$")
    site_url: str
    registration_form_url: str
    contact_email: EmailAddress | None = None

    @property
    def title(self) -> str:
        return f"{self.short_name} {self.year}"


class CheckinDay(BaseModel):
    """One column in the front-desk check-in table.

    ``kind="event"`` covers things that are not a whole day — the CAARMS banquet
    is a separate check-in from the day it falls on, which is why a naive
    "one column per date" model does not fit.
    """

    model_config = ConfigDict(extra="forbid")

    #: No slashes, no bare "6/28". Lowercase so that a key is never
    #: case-ambiguous across a JSON round trip.
    key: str = Field(pattern=r"^[0-9a-z][0-9a-z\-]{2,32}$")
    date: _dt.date | None = None
    label: str | None = None
    kind: Literal["day", "event"] = "day"
    icon: str | None = None

    @model_validator(mode="after")
    def _default_label(self) -> CheckinDay:
        # Built by hand rather than with strftime("%-d"): the no-pad directive is
        # a glibc/BSD extension and is not portable (it fails on Windows), and
        # this label is rendered in the front-desk UI on whatever the operator
        # happens to be running.
        if self.label is None:
            if self.date is not None:
                weekday = self.date.strftime("%a")
                month = self.date.strftime("%b")
                self.label = f"{weekday} {self.date.day} {month}"
            else:
                self.label = self.key
        return self


class Schedule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str = "America/New_York"
    start_date: _dt.date
    end_date: _dt.date
    checkin_days: list[CheckinDay] = Field(default_factory=list)

    @field_validator("timezone")
    @classmethod
    def _valid_tz(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {value!r}") from exc
        return value

    @model_validator(mode="after")
    def _coherent(self) -> Schedule:
        if self.end_date < self.start_date:
            raise ValueError(
                f"end_date {self.end_date} is before start_date {self.start_date}"
            )
        seen: set[str] = set()
        for day in self.checkin_days:
            if day.key in seen:
                raise ValueError(f"duplicate checkin_days key {day.key!r}")
            seen.add(day.key)
            if day.date is not None and not (self.start_date <= day.date <= self.end_date):
                raise ValueError(
                    f"checkin day {day.key!r} has date {day.date}, outside the event "
                    f"range {self.start_date}..{self.end_date}"
                )
        return self

    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def day_keys(self) -> list[str]:
        return [day.key for day in self.checkin_days]

    def day(self, key: str) -> CheckinDay | None:
        return next((d for d in self.checkin_days if d.key == key), None)


class SwagOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The Drupal option key, e.g. "USML".
    key: str
    label: str
    #: Dense label for the check-in table, e.g. "S".
    short: str | None = None
    counts_toward_inventory: bool = True
    sort: int = 0


class Swag(BaseModel):
    """Swag vocabulary. Owned exclusively by ``ticket-reconciler``.

    ``nametag-press`` deliberately has no swag fields: two applications counting
    shirts independently is how you oversell mediums.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    kind: str = "t-shirt"
    drupal_field: str = "t_shirt_size"
    allow_replacement: bool = True
    options: list[SwagOption] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_keys(self) -> Swag:
        keys = [o.key for o in self.options]
        duplicates = {k for k in keys if keys.count(k) > 1}
        if duplicates:
            raise ValueError(f"duplicate swag option key(s): {sorted(duplicates)}")
        return self

    def _sorted(self) -> list[SwagOption]:
        return sorted(self.options, key=lambda o: (o.sort, o.key))

    def keys(self) -> list[str]:
        return [o.key for o in self._sorted()]

    def option(self, key: str | None) -> SwagOption | None:
        if key is None:
            return None
        return next((o for o in self.options if o.key == key), None)

    def label(self, key: str | None) -> str:
        option = self.option(key)
        return option.label if option else ""

    def short(self, key: str | None) -> str:
        option = self.option(key)
        if option is None:
            return ""
        return option.short or option.label

    def inventory_keys(self) -> list[str]:
        return [o.key for o in self._sorted() if o.counts_toward_inventory]


class Match(BaseModel):
    """Predicate selecting which ticket tier a registrant falls into."""

    model_config = ConfigDict(extra="forbid")

    email_domain_suffix: list[str] = Field(default_factory=list)
    field_equals: dict[str, str] = Field(default_factory=dict)
    default: bool = False

    def matches(self, *, email: str | None, fields: Mapping[str, Any]) -> bool:
        if self.default:
            return True
        if self.email_domain_suffix:
            address = (email or "").strip().lower()
            if not any(address.endswith(f"@{s}") or address.endswith(f".{s}")
                       for s in (d.strip().lower().lstrip("@") for d in self.email_domain_suffix)):
                return False
        for name, expected in self.field_equals.items():
            actual = fields.get(name)
            wanted = str(expected).strip().lower()
            if isinstance(actual, bool):
                # The field was already coerced to a real boolean upstream, so
                # compare truthiness rather than spelling.
                if (wanted in ("true", "yes", "1", "on", "checked")) != actual:
                    return False
            elif str(actual if actual is not None else "").strip().lower() != wanted:
                return False
        return bool(self.email_domain_suffix or self.field_equals)


class TicketTier(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    #: The NAME of an environment variable holding the discount code — never the
    #: code. Replaces ``ticketed/backend/main.py:499-511``, where
    #: two live Eventbrite discount codes appear as string literals in the source.
    #:
    #: Note: discount codes are semi-public by nature. The Twig that computes
    #: ``destination_url`` is delivered to the browser, so the code is visible to
    #: any registrant. Keeping them out of git is still right — they should not
    #: be in a public repo — but do not document them as secrets.
    discount_code_env: str | None = None
    price_cents: int | None = None
    match: Match = Field(default_factory=Match)

    @field_validator("discount_code_env")
    @classmethod
    def _looks_like_env_var(cls, value: str | None) -> str | None:
        if value is None:
            return None
        # Upper snake case, at least one underscore, never leading with a digit.
        #
        # The underscore requirement is what actually stops the mistake this field
        # exists to prevent. A pasted Eventbrite code ("2030EXAMPLEGA") is all-caps
        # and alphanumeric, so an isupper()/isalnum() check accepts it happily;
        # requiring the snake_case separator does not. Environment variable names
        # also cannot begin with a digit under POSIX, which catches the rest.
        if not _ENV_VAR_NAME_RE.fullmatch(value):
            raise ValueError(
                f"discount_code_env must be an environment variable NAME in upper "
                f"snake case, e.g. EVENTBRITE_DISCOUNT_GA — got {value!r}. "
                f"Never put the discount code itself here: this file is committed "
                f"and is served to the browser at GET /api/event-profile."
            )
        return value


class Ticketing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vendor: Literal["eventbrite", "none"] = "eventbrite"
    exempt_field: str | None = "tickets_sold_separately"
    exempt_means: Literal["unchecked_is_exempt", "checked_is_exempt"] = "unchecked_is_exempt"
    event_url_template: str = "https://www.eventbrite.com/e/{slug}-tickets-{event_id}"
    slug: str | None = None
    prefer_destination_url_discount: bool = True
    tiers: list[TicketTier] = Field(default_factory=list)
    #: Display order for reconciliation statuses. Replaces the hardcoded dict at
    #: ``ticketed/backend/main.py:568-576``.
    status_order: list[str] = Field(default_factory=list)

    def tier(self, key: str) -> TicketTier | None:
        return next((t for t in self.tiers if t.key == key), None)

    def resolve_tier(
        self, *, email: str | None, fields: Mapping[str, Any]
    ) -> TicketTier | None:
        """First tier whose match predicate passes; the default tier otherwise.

        Order in the YAML is significant — put the most specific tier first.
        """
        fallback: TicketTier | None = None
        for tier in self.tiers:
            if tier.match.default:
                fallback = fallback or tier
                continue
            if tier.match.matches(email=email, fields=fields):
                return tier
        return fallback

    def is_exempt(self, fields: Mapping[str, Any]) -> bool:
        """Whether this registrant needs no ticket purchase."""
        if self.exempt_field is None:
            return False
        value = bool(fields.get(self.exempt_field))
        return not value if self.exempt_means == "unchecked_is_exempt" else value

    def purchase_url(self, *, event_id: str, discount_code: str | None = None) -> str:
        base = self.event_url_template.format(
            slug=self.slug or "", event_id=event_id
        )
        if discount_code:
            separator = "&" if "?" in base else "?"
            return f"{base}{separator}discount={discount_code}"
        return base


class Role(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The Drupal option key, e.g. "Speaker".
    key: str
    label: str
    plural: str
    badge_class: str | None = None
    #: Badge colour for the printed PDF.
    color: str | None = Field(default=None, pattern=_HEX)
    sort: int = 0


class Roles(BaseModel):
    model_config = ConfigDict(extra="forbid")

    drupal_field: str = "attendee_status"
    options: list[Role] = Field(default_factory=list)
    default: str | None = None

    def role(self, key: str | None) -> Role | None:
        if key is None:
            return None
        return next((r for r in self.options if r.key == key), None)

    def label(self, key: str | None) -> str:
        role = self.role(key) or self.role(self.default)
        return role.label if role else (key or "")

    def keys(self) -> list[str]:
        return [r.key for r in sorted(self.options, key=lambda r: (r.sort, r.key))]


class AffiliationRules(BaseModel):
    """Generalises the ``princeton.edu`` -> "Princeton University" rule.

    That rule exists in six places today: four Python and two JavaScript copies
    across both repos, including a fourth copy in
    ``admin_reimbursement.html:230-238``.
    """

    model_config = ConfigDict(extra="forbid")

    drupal_field: str = "home_institution_or_organization"
    placeholder_values: list[str] = Field(
        default_factory=lambda: ["", "n/a", "na", "none", "null", "-", "--"]
    )
    #: Email domain -> institution display name.
    domain_map: dict[str, str] = Field(default_factory=dict)

    def is_placeholder(self, declared: str | None) -> bool:
        if declared is None:
            return True
        return declared.strip().lower() in {p.strip().lower() for p in self.placeholder_values}

    def normalize(self, *, email: str | None, declared: str | None) -> str | None:
        """Return the best available institution name for a registrant.

        A declared value wins unless it is a placeholder, in which case the email
        domain is consulted. Longest domain suffix wins, so ``cs.example.edu``
        can override ``example.edu``.
        """
        if not self.is_placeholder(declared):
            return (declared or "").strip()
        address = (email or "").strip().lower()
        if "@" not in address:
            return None
        domain = address.rsplit("@", 1)[1]
        best: str | None = None
        best_length = -1
        for suffix, name in self.domain_map.items():
            normalized = suffix.strip().lower().lstrip("@")
            if domain == normalized or domain.endswith(f".{normalized}"):
                if len(normalized) > best_length:
                    best, best_length = name, len(normalized)
        return best


class LodgingVocab(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gender_identity_options: list[str] = Field(default_factory=list)
    roommate_preference_options: list[str] = Field(default_factory=list)
    room_gender_options: list[str] = Field(
        default_factory=lambda: ["Any", "Man", "Woman", "Non-binary", "Mixed"]
    )
    room_categories: list[str] = Field(default_factory=list)
    default_capacity: int = Field(default=2, ge=1)
    capacities: list[int] = Field(default_factory=lambda: [1, 2, 3, 4])


class LodgingRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,48}$")
    severity: Literal["error", "warning", "info"] = "warning"
    enabled: bool = True


class Lodging(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    drupal_field: str = "lodging"
    vocab: LodgingVocab = Field(default_factory=LodgingVocab)
    rules: list[LodgingRule] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_codes(self) -> Lodging:
        codes = [r.code for r in self.rules]
        duplicates = {c for c in codes if codes.count(c) > 1}
        if duplicates:
            raise ValueError(f"duplicate lodging rule code(s): {sorted(duplicates)}")
        return self

    def severity(self, code: str, default: str = "warning") -> str:
        rule = next((r for r in self.rules if r.code == code), None)
        return rule.severity if rule else default

    def is_enabled(self, code: str) -> bool:
        rule = next((r for r in self.rules if r.code == code), None)
        return rule.enabled if rule else True


class Branding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site_name: str
    slogan: str | None = None
    theme: str = "neutral"
    #: One hex drives a derived ramp in ``eventkit.ui``. Default is Princeton's
    #: official orange, which is what ``paper-tiger/tokens/tokens.json`` already
    #: declares — resolving the ``#f58025`` vs ``#e77500`` conflict in favour of
    #: the token file rather than the ~40 inline ``style=`` attributes.
    brand_color: str = Field(default="#e77500", pattern=_HEX)
    brand_color_dark: str | None = Field(default=None, pattern=_HEX)
    logo_url: str | None = None
    logo_stacked_url: str | None = None
    favicon_url: str | None = None
    event_image_url: str | None = None
    css_override_url: str | None = None
    footer_html: str | None = None


class Nametags(BaseModel):
    model_config = ConfigDict(extra="forbid")

    avery_template: Literal["5392", "74541", "5395", "custom"] = "5392"
    show_role_badge: bool = True
    show_affiliation: bool = True
    primary_logo_url: str | None = None
    sponsor_logo_url: str | None = None


class DrupalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    webform_schema: str | None = None
    field_map: FieldMap | None = None
    join_key: Literal["uuid", "email"] = "uuid"

    @model_validator(mode="after")
    def _need_one(self) -> DrupalConfig:
        if self.field_map is None and self.webform_schema is None:
            raise ValueError(
                "drupal must declare either field_map or webform_schema. There is "
                "no built-in default: the previous implementation fell back to a "
                "hardcoded CAARMS field map, so every other event silently parsed "
                "registrations into empty columns."
            )
        return self


class NotifyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: ``log`` by default so a fresh deployment never silently fails to send.
    #: ``smtp`` is the recommended real transport: every university has a relay,
    #: whereas ACS needs a provisioned Communication Service and DNS access.
    transport: Literal["log", "smtp", "resend", "acs", "memory"] = "log"
    from_name: str | None = None
    from_email: EmailAddress | None = None
    default_recipients: list[EmailAddress] = Field(default_factory=list)
    events: dict[str, bool] = Field(default_factory=dict)
    template_dir: str | None = None


class LinkTemplate(BaseModel):
    """One kind of prefilled per-person link that ``link-forge`` can render."""

    model_config = ConfigDict(extra="forbid")

    label: str
    url: str
    #: ``fragment`` params never reach a server: not access logs, not Referer,
    #: not a CDN. ``query`` params land in Drupal's webserver log, App Service
    #: logs, and every proxy in between.
    param_style: Literal["fragment", "query"] = "fragment"
    sensitivity: Literal["low", "pii", "bearer"] = "low"
    fragment_params: dict[str, str] = Field(default_factory=dict)
    query_params: dict[str, str] = Field(default_factory=dict)
    #: Param name -> environment variable name, for values that must not be
    #: committed (a DocuSign PowerForm id, for instance).
    query_params_from_env: dict[str, str] = Field(default_factory=dict)
    #: Param name -> template string, e.g. ``"{full_name}"``.
    prefill: dict[str, str] = Field(default_factory=dict)
    #: Which roles see this link. Empty means everyone.
    roles: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _bearer_needs_query(self) -> LinkTemplate:
        if self.sensitivity == "bearer" and self.param_style == "fragment":
            raise ValueError(
                "a bearer link cannot use param_style=fragment: the fragment is "
                "never sent to the server, so the token would not prefill "
                "anything. Use query and accept that the token reaches access "
                "logs, or redesign the far side to accept a POST."
            )
        return self


class EventProfile(BaseModel):
    """The whole profile. One file, loaded once, validated at startup."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    event: EventInfo
    schedule: Schedule
    branding: Branding
    drupal: DrupalConfig
    roles: Roles = Field(default_factory=Roles)
    affiliation: AffiliationRules = Field(default_factory=AffiliationRules)
    ticketing: Ticketing = Field(default_factory=Ticketing)
    swag: Swag = Field(default_factory=Swag)
    lodging: Lodging = Field(default_factory=Lodging)
    nametags: Nametags = Field(default_factory=Nametags)
    notify: NotifyConfig = Field(default_factory=NotifyConfig)
    links: dict[str, LinkTemplate] = Field(default_factory=dict)

    # -- per-application requirements ---------------------------------------
    def validate_for_app(self, app_name: str, *, require: Iterable[str]) -> None:
        """Fail startup if this profile lacks what ``app_name`` needs.

        ``require`` is a list of dotted paths that must resolve to a non-empty
        value, e.g. ``["schedule.checkin_days", "swag.options"]``.

        Declared by the app rather than centrally, so that adding a lodging key
        cannot break ``nametag-press`` startup — the failure mode this whole
        section exists to avoid.

        Raises:
            EventProfileError: listing every missing path at once, so an adopter
                fixes the profile in one pass instead of one boot per key.
        """
        missing: list[str] = []
        for path in require:
            if _is_empty(self, path):
                missing.append(path)
        if missing:
            raise EventProfileError(
                f"Event profile is not usable by {app_name}: the following "
                f"required key(s) are missing or empty: {', '.join(missing)}.\n"
                f"See EVENT-PROFILE-SPEC.md for the required keys per application."
            )

    def checkin_day_keys(self) -> list[str]:
        return self.schedule.day_keys()

    def discount_code(self, tier: TicketTier | None, environ: Mapping[str, str]) -> str | None:
        """Read a tier's discount code out of the environment.

        Kept here so no application re-implements "which env var holds this
        code", and so that a missing variable is a ``None`` rather than a
        ``KeyError`` on the purchase-link path.
        """
        if tier is None or tier.discount_code_env is None:
            return None
        value = environ.get(tier.discount_code_env)
        return value.strip() or None if value else None


def _is_empty(root: Any, dotted: str) -> bool:
    current: Any = root
    for part in dotted.split("."):
        if isinstance(current, Mapping):
            if part not in current:
                return True
            current = current[part]
        else:
            if not hasattr(current, part):
                return True
            current = getattr(current, part)
    if current is None:
        return True
    if isinstance(current, (str, list, dict, tuple, set)) and len(current) == 0:
        return True
    if current is False:
        return True
    return False
