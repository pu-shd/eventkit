"""Exception types shared across eventkit.

All of these are configuration or contract failures that should stop a process
at startup rather than degrade at request time. The pattern being replaced is
``ticketed/backend/database.py``, which wraps every schema change in a
``try/except`` ending at ``logger.error(...)`` and then continues running — so a
failed migration produced a booted app that 500s on the first webhook, and the
lost registration looks to Drupal like a handler blip.
"""

from __future__ import annotations

__all__ = [
    "ConfigError",
    "ContractError",
    "EventKitError",
    "EventProfileError",
    "FieldMapError",
]


class EventKitError(Exception):
    """Base class for every error eventkit raises deliberately."""


class ConfigError(EventKitError):
    """Invalid or unsafe application configuration. Raise at startup."""


class EventProfileError(ConfigError):
    """The event profile is missing, unparseable, or invalid for this app."""


class FieldMapError(EventProfileError):
    """No usable Drupal field map could be resolved.

    Fatal on purpose. A wrong or absent field map does not fail loudly at
    request time: submissions parse, values come back ``None``, and rows are
    written with missing data. Not booting is strictly better than accepting
    registrations you cannot read.
    """


class ContractError(EventKitError):
    """A frozen cross-application contract was violated."""
