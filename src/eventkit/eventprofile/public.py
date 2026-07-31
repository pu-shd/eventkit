"""Projecting an :class:`EventProfile` down to what the browser may see.

``GET /api/event-profile`` is served to authenticated staff *and*, in
``poster-gallery``, to anonymous visitors. So this projection is a security
boundary, and it is written as an explicit **deny list plus a trip-wire test**:
``tests/unit/eventprofile/test_public.py`` fails if a newly added profile field
reaches the public payload without someone deciding it should.

That inversion is deliberate. The original PII leak in ``poster-gallery`` was one
careless ``response_model`` reuse — ``PresenterResponse`` was written for the
admin table and then handed to an anonymous route, exposing every presenter's
email address, ``drupal_sid`` and ``serial_number``. A default-open projection
reproduces that bug the first time someone adds a field.
"""

from __future__ import annotations

import json
from typing import Any

from .models import EventProfile

__all__ = [
    "PUBLIC_DENY_PATHS",
    "public_etag",
    "to_public_dict",
]

#: Dotted paths stripped from the public payload.
#:
#: * ``notify.*`` recipients are staff addresses; publishing them builds a
#:   spam list out of the operations team.
#: * ``drupal.field_map`` / ``drupal.webform_schema`` describe the ingest
#:   contract. Not secret, but they tell an attacker exactly which element keys
#:   to forge if they ever obtain a webhook token, and the browser has no use
#:   for them.
#: * ``discount_code_env`` names an environment variable. The code itself is
#:   semi-public (the Twig that computes it is delivered to the browser), but the
#:   variable *name* is infrastructure detail that maps the deployment.
PUBLIC_DENY_PATHS: frozenset[str] = frozenset(
    {
        "notify.default_recipients",
        "notify.from_email",
        "notify.template_dir",
        "drupal.field_map",
        "drupal.webform_schema",
        "ticketing.tiers[].discount_code_env",
        "links[].query_params_from_env",
    }
)


def to_public_dict(profile: EventProfile) -> dict[str, Any]:
    """Return the JSON-safe public view of ``profile``.

    ``event.contact_email`` is intentionally retained: it is already published on
    the event website and is the address attendees are told to write to. Every
    other address is removed.
    """
    # Round-trip through JSON mode so dates become ISO strings and the result is
    # directly serialisable by any framework.
    data: dict[str, Any] = json.loads(profile.model_dump_json())

    notify = data.get("notify")
    if isinstance(notify, dict):
        for key in ("default_recipients", "from_email", "template_dir"):
            notify.pop(key, None)

    drupal = data.get("drupal")
    if isinstance(drupal, dict):
        drupal.pop("field_map", None)
        drupal.pop("webform_schema", None)

    ticketing = data.get("ticketing")
    if isinstance(ticketing, dict):
        for tier in ticketing.get("tiers") or []:
            if isinstance(tier, dict):
                tier.pop("discount_code_env", None)

    links = data.get("links")
    if isinstance(links, dict):
        for link in links.values():
            if isinstance(link, dict):
                link.pop("query_params_from_env", None)

    return data


def public_etag(profile: EventProfile) -> str:
    """A stable strong ETag over the public payload.

    The profile changes rarely, the front end fetches it on every page load, and
    the front desk runs on conference wifi. Cheap to compute, so no cache.
    """
    import hashlib

    payload = json.dumps(to_public_dict(profile), sort_keys=True, separators=(",", ":"))
    return '"' + hashlib.sha256(payload.encode()).hexdigest()[:32] + '"'
