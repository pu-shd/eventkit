"""Locating, parsing and caching the event profile."""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import yaml
from pydantic import ValidationError

from ..errors import EventProfileError
from .models import EventProfile

logger = logging.getLogger("eventkit.eventprofile")

__all__ = [
    "clear_profile_cache",
    "get_profile",
    "load_profile",
    "profile_dependency",
    "profile_search_paths",
]

_ENV_VAR = "EVENT_PROFILE"
_DEFAULT_NAMES = ("event-profile.yaml", "event-profile.yml")
#: The Azure Files mount. Lets an operator correct a profile without a rebuild.
_HOSTED_DIR = Path("/home/site")


def profile_search_paths(explicit: str | Path | None = None) -> list[Path]:
    """The resolution order, most specific first.

    ``explicit`` argument -> ``$EVENT_PROFILE`` -> ``./event-profile.yaml`` ->
    ``/home/site/event-profile.yaml``.
    """
    paths: list[Path] = []
    if explicit is not None:
        paths.append(Path(explicit))
    from_env = os.getenv(_ENV_VAR)
    if from_env:
        paths.append(Path(from_env))
    for name in _DEFAULT_NAMES:
        paths.append(Path.cwd() / name)
    for name in _DEFAULT_NAMES:
        paths.append(_HOSTED_DIR / name)
    return paths


def _format_validation_error(path: Path, exc: ValidationError) -> str:
    """Render a pydantic error as an operator-readable report.

    Adopters editing YAML by hand get a location and a reason per problem rather
    than a wall of pydantic internals.
    """
    lines = [f"{len(exc.errors())} problem(s) in the event profile at {path}:", ""]
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "(root)"
        lines.append(f"  {location}: {error['msg']}")
        if error.get("input") is not None and error["type"] != "missing":
            given = repr(error["input"])
            if len(given) > 120:
                given = given[:117] + "..."
            lines.append(f"      given: {given}")
    lines.append("")
    lines.append("See EVENT-PROFILE-SPEC.md for every key, type and default.")
    return "\n".join(lines)


def load_profile(path: str | Path | None = None) -> EventProfile:
    """Load and validate an event profile.

    Raises:
        EventProfileError: if no profile is found, the YAML is malformed, or
            validation fails. Never returns a partially valid profile — an app
            that boots with a half-configured profile drops data silently.
    """
    candidates = profile_search_paths(path)
    found: Path | None = next((p for p in candidates if p.is_file()), None)

    if found is None:
        searched = "\n".join(f"  - {p}" for p in candidates)
        raise EventProfileError(
            "No event profile found. Searched, in order:\n"
            f"{searched}\n\n"
            f"Set {_ENV_VAR} to an absolute path, or place event-profile.yaml in "
            "the working directory. Start from examples/caarms-2026/event-profile.yaml."
        )

    try:
        raw = yaml.safe_load(found.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise EventProfileError(f"Event profile at {found} is not valid YAML: {exc}") from exc

    if raw is None:
        raise EventProfileError(f"Event profile at {found} is empty.")
    if not isinstance(raw, dict):
        raise EventProfileError(
            f"Event profile at {found} must be a YAML mapping, got {type(raw).__name__}."
        )

    try:
        profile = EventProfile.model_validate(raw)
    except ValidationError as exc:
        raise EventProfileError(_format_validation_error(found, exc)) from exc

    logger.info(
        "event profile loaded path=%s event=%s year=%d theme=%s checkin_days=%d",
        found,
        profile.event.slug,
        profile.event.year,
        profile.branding.theme,
        len(profile.schedule.checkin_days),
    )
    return profile


@lru_cache(maxsize=1)
def get_profile() -> EventProfile:
    """The process-wide profile, loaded once.

    Cached lazily rather than at import time. Import-time configuration is what
    forces the env-vars-before-import dance in both existing test suites
    (``ticketed/backend/main.py:28-29`` runs ``create_all()`` at import).
    """
    return load_profile()


def clear_profile_cache() -> None:
    """Drop the cached profile. For tests and for a config-reload endpoint."""
    get_profile.cache_clear()


def profile_dependency() -> Callable[[], EventProfile]:
    """A FastAPI-compatible dependency returning the cached profile.

    Deliberately not typed against FastAPI so this module stays importable
    without it.
    """

    def _dependency() -> EventProfile:
        return get_profile()

    return _dependency


def load_profile_dict(path: str | Path | None = None) -> dict[str, Any]:
    """The raw mapping, for tooling that needs to diff or re-emit YAML."""
    candidates = profile_search_paths(path)
    found = next((p for p in candidates if p.is_file()), None)
    if found is None:
        raise EventProfileError("No event profile found.")
    return yaml.safe_load(found.read_text(encoding="utf-8")) or {}
