"""The event profile: one validated YAML per event.

Import-weight contract: this package pulls in pydantic, PyYAML and the standard
library, and nothing else. ``eventkit.eventprofile.routes`` is the only module
here that needs FastAPI, and it is *not* imported from this ``__init__`` — import
it explicitly when you are mounting the router.
"""

from __future__ import annotations

from .checkin import (
    CHECKIN_STATES,
    CheckinKeyError,
    canonical_state,
    legacy_key_aliases,
    migrate_checkin_blob,
)
from .load import (
    clear_profile_cache,
    get_profile,
    load_profile,
    profile_dependency,
    profile_search_paths,
)
from .models import (
    AffiliationRules,
    Branding,
    CheckinDay,
    DrupalConfig,
    EventInfo,
    EventProfile,
    LinkTemplate,
    Lodging,
    LodgingRule,
    LodgingVocab,
    Match,
    Nametags,
    NotifyConfig,
    Role,
    Roles,
    Schedule,
    Swag,
    SwagOption,
    Ticketing,
    TicketTier,
)
from .public import PUBLIC_DENY_PATHS, public_etag, to_public_dict

__all__ = [
    "CHECKIN_STATES",
    "PUBLIC_DENY_PATHS",
    "AffiliationRules",
    "Branding",
    "CheckinDay",
    "CheckinKeyError",
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
    "canonical_state",
    "clear_profile_cache",
    "get_profile",
    "legacy_key_aliases",
    "load_profile",
    "migrate_checkin_blob",
    "profile_dependency",
    "profile_search_paths",
    "public_etag",
    "to_public_dict",
]
