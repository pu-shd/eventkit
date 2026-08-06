"""A pytest plugin shipped with eventkit, exported via the ``pytest11`` entry point.

Installing ``eventkit-core[test]`` gives every application these fixtures with no
import and no copying. Each app's ``conftest.py`` becomes one line.

The highest-value fixture here is :func:`_no_network`, which is **autouse**.
``posted``'s suite currently makes real outbound HTTPS requests to
caarms.princeton.edu on every single run, because ``download_assets.py`` is
invoked from the application's ``lifespan`` and the tests start the app. That
means the test suite is slow, fails offline, fails when the Drupal site is being
maintained, and quietly sends a Cloudflare-bypass header to production from a
developer laptop. An autouse fixture that makes ``socket.connect`` raise is the
cheapest possible guard, and it would have caught that on day one.

Heavy fixtures (database, HTTP client) import SQLAlchemy and FastAPI *inside* the
fixture body, so merely loading this plugin does not drag them in — that is what
lets ``link-forge`` install ``[test]`` without a database driver.
"""

from __future__ import annotations

import datetime as _dt
import json
import socket
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"
DRUPAL_FIXTURE_DIR = FIXTURE_DIR / "drupal"


# --------------------------------------------------------------------------
# Network isolation
# --------------------------------------------------------------------------
class NetworkAccessDenied(RuntimeError):
    """Raised when a test attempts a real outbound connection."""


@pytest.fixture(autouse=True)
def _no_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make real outbound connections raise. Opt out with ``@pytest.mark.allow_network``.

    Loopback is permitted so that a live-server test or a local Postgres in CI
    still works; it is *remote* egress that must never happen in a unit test.
    """
    if request.node.get_closest_marker("allow_network"):
        return

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def _is_local(address: Any) -> bool:
        if isinstance(address, tuple) and address:
            host = str(address[0])
            # S104 flags "0.0.0.0" as binding to all interfaces. This is the
            # opposite: it is the _no_network fixture deciding whether an
            # outbound connect target is loopback and may be allowed through.
            return host in ("127.0.0.1", "::1", "localhost", "0.0.0.0") or host.startswith(  # noqa: S104
                "127."
            )
        # AF_UNIX and friends: local by definition.
        return True

    def guarded_connect(self: socket.socket, address: Any) -> Any:
        if _is_local(address):
            return real_connect(self, address)
        raise NetworkAccessDenied(
            f"Blocked outbound connection to {address!r} during a test. Mock it "
            f"(respx for httpx), or mark the test @pytest.mark.allow_network if "
            f"it genuinely must reach the network."
        )

    def guarded_connect_ex(self: socket.socket, address: Any) -> Any:
        if _is_local(address):
            return real_connect_ex(self, address)
        raise NetworkAccessDenied(f"Blocked outbound connection to {address!r} during a test.")

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)


# --------------------------------------------------------------------------
# Environment and settings
# --------------------------------------------------------------------------
@pytest.fixture
def eventkit_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., None]]:
    """Set environment variables and clear cached settings/profile afterwards.

    Safe because settings are lazy (``get_settings()`` with ``@lru_cache``) rather
    than instantiated at module import. That laziness is the precondition for
    this fixture existing at all: both current test suites must set environment
    variables *before* importing the application module, because
    ``settings = Settings()`` runs at import time.
    """

    def _set(**values: Any) -> None:
        for key, value in values.items():
            monkeypatch.setenv(key.upper(), "" if value is None else str(value))

    yield _set

    from ..eventprofile.load import clear_profile_cache

    clear_profile_cache()


# --------------------------------------------------------------------------
# Event profiles
# --------------------------------------------------------------------------
_MINIMAL_PROFILE: dict[str, Any] = {
    "schema_version": 1,
    "event": {
        "name": "Example Conference on Example Topics",
        "short_name": "EXCON",
        "year": 2030,
        "slug": "excon-2030",
        "site_url": "https://example.edu/excon",
        "registration_form_url": "https://example.edu/form/registration",
        "contact_email": "excon@example.edu",
    },
    "schedule": {
        "timezone": "America/New_York",
        "start_date": "2030-06-01",
        "end_date": "2030-06-03",
        "checkin_days": [
            {"key": "2030-06-01", "date": "2030-06-01"},
            {"key": "2030-06-02", "date": "2030-06-02"},
            {
                "key": "2030-06-02-banquet",
                "date": "2030-06-02",
                "kind": "event",
                "label": "Banquet",
            },
            {"key": "2030-06-03", "date": "2030-06-03"},
        ],
    },
    "branding": {"site_name": "EXCON 2030 Registration", "theme": "neutral"},
    "drupal": {
        "join_key": "uuid",
        "field_map": {
            "fields": {
                "email": {"key": ["email", "confirm_email_address"], "kind": "email",
                          "required": True},
                "name": {"key": "registrant_name", "kind": "name", "required": True},
                "uuid": {"key": "uuid", "kind": "text"},
                "sid": {"key": "sid", "kind": "int"},
                "serial": {"key": "serial", "kind": "int"},
                "tickets_sold_separately": {"key": "tickets_sold_separately", "kind": "bool"},
                "attendee_status": {"key": "attendee_status", "kind": "select"},
                "student": {"key": "student", "kind": "bool"},
                "home_institution_or_organization": {
                    "key": "home_institution_or_organization", "kind": "text"},
                "presenting_poster": {"key": "presenting_poster", "kind": "bool"},
                "poster_title": {"key": "poster_title", "kind": "text"},
                "faculty_adviser_name": {"key": "faculty_adviser_name", "kind": "text"},
                "poster_presentation_abstract": {
                    "key": "poster_presentation_abstract", "kind": "text"},
                "lodging": {"key": "lodging", "kind": "bool"},
                "gender_identity": {"key": "gender_identity", "kind": "select_other"},
                "roommate_preference": {"key": "roommate_preference", "kind": "select"},
                "identified_roommate": {"key": "identified_roommate", "kind": "text"},
                "t_shirt_size": {"key": "t_shirt_size", "kind": "select"},
                "destination_url": {"key": "destination_url", "kind": "url"},
            }
        },
    },
    "roles": {
        "drupal_field": "attendee_status",
        "default": "Attendee",
        "options": [
            {"key": "Speaker", "label": "Speaker", "plural": "Speakers", "sort": 1},
            {"key": "Organizer", "label": "Organizer", "plural": "Organizers", "sort": 2},
            {"key": "Attendee", "label": "Attendee", "plural": "Attendees", "sort": 3},
        ],
    },
    "affiliation": {"domain_map": {"example.edu": "Example University"}},
}


@pytest.fixture
def minimal_profile_dict() -> dict[str, Any]:
    """A deep copy of the minimal valid profile, for mutation in invalid-input tests."""
    import copy

    return copy.deepcopy(_MINIMAL_PROFILE)


@pytest.fixture
def event_profile(minimal_profile_dict: dict[str, Any]):
    """A minimal, valid, brand-neutral profile. No Princeton, no CAARMS."""
    from ..eventprofile.models import EventProfile

    return EventProfile.model_validate(minimal_profile_dict)


@pytest.fixture
def field_map(event_profile):
    """The field map from :func:`event_profile`."""
    return event_profile.drupal.field_map


@pytest.fixture
def caarms_profile():
    """The shipped ``examples/caarms-2026`` profile, for regression parity tests.

    Skips rather than fails when the examples directory is absent, so an app repo
    that installs eventkit from a wheel is not required to vendor it.
    """
    from ..eventprofile.load import load_profile

    candidates = [
        Path.cwd() / "examples" / "caarms-2026" / "event-profile.yaml",
        Path(__file__).resolve().parents[3] / "examples" / "caarms-2026" / "event-profile.yaml",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return load_profile(candidate)
    pytest.skip("examples/caarms-2026/event-profile.yaml not available")


# --------------------------------------------------------------------------
# Golden Drupal payloads
# --------------------------------------------------------------------------
@pytest.fixture
def drupal_payload() -> Callable[[str], dict[str, Any]]:
    """``drupal_payload("registration_wrapped")`` -> the golden fixture dict.

    Fixtures ship inside the wheel so application repos reuse the same payloads
    rather than inventing their own, which is how the three parsers drifted.
    All of them are sanitised: ``ada@example.edu``, ``Example University``.
    """

    def _load(name: str) -> dict[str, Any]:
        path = DRUPAL_FIXTURE_DIR / f"{name}.json"
        if not path.is_file():
            available = sorted(p.stem for p in DRUPAL_FIXTURE_DIR.glob("*.json"))
            raise AssertionError(
                f"no golden Drupal fixture named {name!r}. Available: {available}"
            )
        return json.loads(path.read_text(encoding="utf-8"))

    return _load


@pytest.fixture
def all_drupal_payloads() -> dict[str, dict[str, Any]]:
    """Every golden fixture, for parametrised sweeps."""
    return {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(DRUPAL_FIXTURE_DIR.glob("*.json"))
    }


# --------------------------------------------------------------------------
# Webhook headers
# --------------------------------------------------------------------------
# Not a credential: a fixed 48-char value used by the webhook fixtures so that
# tests exercise a token long enough to clear the strength check. Never valid
# anywhere. (S105 flags any name containing "TOKEN".)
STRONG_TEST_TOKEN = "0123456789abcdef0123456789abcdef0123456789abcdef"  # noqa: S105


@pytest.fixture
def webhook_token() -> str:
    """A token that passes ``assert_strong``. Long and varied enough to be valid."""
    return STRONG_TEST_TOKEN


@pytest.fixture
def webhook_headers(webhook_token: str) -> dict[str, str]:
    from ..webhook import DEFAULT_HEADER

    return {DEFAULT_HEADER: webhook_token}


@pytest.fixture
def bad_webhook_headers() -> dict[str, str]:
    from ..webhook import DEFAULT_HEADER

    return {DEFAULT_HEADER: "not-the-right-token-but-long-enough-to-look-real"}


# --------------------------------------------------------------------------
# Time
# --------------------------------------------------------------------------
@pytest.fixture
def frozen_now() -> _dt.datetime:
    """A fixed naive-UTC instant, matching the datetime flavour stored in the DB."""
    return _dt.datetime(2030, 6, 1, 12, 0, 0)


# --------------------------------------------------------------------------
# Database and HTTP client (lazy imports — need the [db] / [web] extras)
# --------------------------------------------------------------------------
@pytest.fixture
def memory_engine():
    """In-memory SQLite with a StaticPool, so every session sees one database.

    The ``StaticPool`` + ``check_same_thread=False`` dance is hand-rolled in both
    existing repos' conftests; here it is written once.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def sqlite_engine(tmp_path: Path):
    """A file-backed temporary SQLite database with eventkit's Azure Files pragmas.

    File-backed rather than ``:memory:`` on purpose: migration tests must exercise
    a real file, since that is where the locking and journal-mode behaviour lives.
    """
    from sqlalchemy import create_engine, event

    path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{path}", future=True)

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection, _record):  # pragma: no cover - driver callback
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=TRUNCATE")
        cursor.execute("PRAGMA synchronous=FULL")
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    try:
        yield engine
    finally:
        engine.dispose()


#: The address used by every auth-related fixture and test, so that a grep for a
#: real netID across the repos returns nothing. The live ``posted`` config commits
#: seven real Princeton netIDs as the default admin allow-list
#: (``posted/backend/config.py:25``); CI greps for ``@princeton.edu`` outside
#: ``examples/`` and ``themes/princeton-orfe/`` to keep that from recurring.
ADMIN_EMAIL = "admin@example.edu"

# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------
@pytest.fixture
def principal():
    """A ``Principal`` for :data:`ADMIN_EMAIL`, allow-listed by default."""
    from ..auth import Principal

    return Principal(email=ADMIN_EMAIL, display_name="Admin Example", provider="aad", id="p-1")


@pytest.fixture
def make_client() -> Callable[..., Any]:
    """``make_client(app, db=session, principal="admin@example.edu")`` -> ``TestClient``.

    Wires ``app.dependency_overrides`` for the database and auth dependencies so
    a test can drive a real FastAPI app without a live SQLite file or a real
    Easy Auth proxy in front of it. Follows the convention every app's
    ``create_app()`` factory is expected to establish: the constructed
    ``Database`` lives at ``app.state.database`` and the constructed
    ``EasyAuth`` at ``app.state.auth``. Either or both may be absent — a caller
    testing a module that needs only one of them still gets a working client.

    ``principal=None`` behaves like :func:`as_anonymous`: the *required* auth
    dependency is left wired to Easy Auth's real header check (so a request
    with no Easy Auth headers exercises the genuine redirect/401 path), while
    the *optional* dependency is overridden to ``None`` for convenience.
    """

    def _make(
        app: Any,
        *,
        db: Any = None,
        principal: Any = "admin@example.edu",
        follow_redirects: bool = True,
    ) -> Any:
        from starlette.testclient import TestClient

        from ..auth import Principal

        database = getattr(app.state, "database", None)
        if database is not None and db is not None:
            def _override_get_db() -> Iterator[Any]:
                yield db

            app.dependency_overrides[database.get_db] = _override_get_db

        auth = getattr(app.state, "auth", None)
        if auth is not None:
            if principal is None:
                app.dependency_overrides.pop(auth.dependency(), None)
                app.dependency_overrides[auth.optional()] = lambda: None
            else:
                is_principal = isinstance(principal, Principal)
                resolved = principal if is_principal else Principal(email=principal)
                app.dependency_overrides[auth.dependency()] = lambda: resolved
                app.dependency_overrides[auth.optional()] = lambda: resolved

        return TestClient(app, follow_redirects=follow_redirects)

    return _make


@pytest.fixture
def as_anonymous(make_client: Callable[..., Any]) -> Callable[..., Any]:
    """``as_anonymous(app)`` -> a ``TestClient`` with no principal overridden.

    Requests carry no Easy Auth headers, so a protected route exercises the
    real "no principal" path: redirect on a page path, 401 elsewhere. Redirects
    are not followed, so a caller can assert on the 302 and its ``Location``
    directly. For asserting the allow-list itself (an authenticated-but-rejected
    principal), use ``make_client(app, principal="nobody@example.edu")`` instead
    — that *is* a real principal, just not an authorized one.
    """

    def _make(app: Any, *, db: Any = None) -> Any:
        return make_client(app, db=db, principal=None, follow_redirects=False)

    return _make


# NOTE: `make_database`/`db_session` are still absent even though `eventkit.db`
# landed in a prior iteration — PLAN.md SS E.1's sketch (`db_session(make_database)`,
# no `Base` argument) does not by itself say which app's tables `db_session`
# should create, and guessing produces a fixture that is either schema-less
# (breaks on first real query) or silently coupled to one app's `Base`. Neither
# `eventkit.backup`'s tests nor any other module built so far needed them: each
# builds its own throwaway `Base`/`Database` inline instead. Add them for real
# once an app's `create_app()` factory exists to observe the convention against
# — `phase1:reference-app` is that first app, per DEC-002. `eventbrite_mock`
# remains deliberately absent, landing with `eventbrite.client`: a fixture that
# exists but does not work is worse than one that is missing, because the
# failure surfaces inside a test rather than at collection.


# --------------------------------------------------------------------------
# Notify
# --------------------------------------------------------------------------
@pytest.fixture
def mail_outbox():
    """A :class:`~eventkit.notify.MemoryTransport` — its ``.outbox`` list is
    every :class:`~eventkit.notify.Message` sent through it during the test.

    Construct a :class:`~eventkit.notify.Notifier` with this as its transport
    to assert on outbound notifications without touching SMTP, Resend or ACS::

        notifier = Notifier(mail_outbox, renderer, policy, from_email="a@example.edu")
        await notifier.notify("completed_payment", {"email": "ada@example.edu"})
        assert mail_outbox.outbox[0].to == ["events@example.edu"]
    """
    from ..notify import MemoryTransport

    return MemoryTransport()
