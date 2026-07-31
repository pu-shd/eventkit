"""The import-weight contract.

``link-forge`` is stateless and has no database. ``nametag-press`` makes no
outbound HTTP calls. Neither should be forced to install FastAPI, SQLAlchemy or
httpx merely to read an event profile or parse a webform payload.

Enforced by importing the light modules in a subprocess where those three
packages are made unimportable. A plain ``import`` test would pass by accident on
a developer machine that happens to have them installed — which is every machine
that can run this suite, since ``[test]`` pulls in ``[app]``.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

#: Modules that must import with only pydantic, PyYAML, Jinja2 and the stdlib.
LIGHT_MODULES = [
    "eventkit",
    "eventkit.errors",
    "eventkit.identity",
    "eventkit.logging",
    "eventkit.drupal",
    "eventkit.drupal.coerce",
    "eventkit.drupal.schema",
    "eventkit.drupal.parse",
    "eventkit.eventprofile",
    "eventkit.eventprofile.models",
    "eventkit.eventprofile.load",
    "eventkit.eventprofile.public",
    "eventkit.eventprofile.checkin",
    "eventkit.eventbrite.models",
    "eventkit.eventbrite.aggregate",
    "eventkit.eventbrite",
    "eventkit.webhook",
]

HEAVY_PACKAGES = ["fastapi", "sqlalchemy", "httpx", "starlette", "resend", "alembic"]

_BLOCKER = textwrap.dedent(
    """
    import sys

    BLOCKED = {blocked!r}

    class Blocker:
        def find_module(self, name, path=None):
            return self.find_spec(name, path)

        def find_spec(self, name, path=None, target=None):
            root = name.split(".")[0]
            if root in BLOCKED:
                raise ImportError(
                    "blocked by the eventkit import-weight test: " + name
                )
            return None

    # Drop anything already imported so the blocker actually bites.
    for mod in list(sys.modules):
        if mod.split(".")[0] in BLOCKED:
            del sys.modules[mod]

    sys.meta_path.insert(0, Blocker())

    {body}
    print("OK")
    """
)


def run_without_heavy_packages(body: str) -> subprocess.CompletedProcess[str]:
    script = _BLOCKER.format(blocked=HEAVY_PACKAGES, body=textwrap.indent(body, ""))
    # A fresh interpreter is the only way to measure what a module imports:
    # sys.modules in this process is already populated. The argv is built here
    # from sys.executable and a literal script, never from external input.
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", script], capture_output=True, text=True
    )


@pytest.mark.parametrize("module", LIGHT_MODULES)
def test_module_imports_without_heavy_dependencies(module):
    result = run_without_heavy_packages(f"import {module}")
    assert result.returncode == 0, (
        f"{module} cannot be imported without {HEAVY_PACKAGES}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_the_blocker_actually_blocks():
    """Guard against the test passing because the blocker is broken."""
    result = run_without_heavy_packages("import fastapi")
    assert result.returncode != 0
    assert "blocked by the eventkit import-weight test" in result.stderr


def test_a_realistic_link_forge_workload_needs_nothing_heavy():
    """link-forge's whole job: load a profile and render per-person links."""
    body = textwrap.dedent(
        """
        from eventkit.eventprofile.models import EventProfile
        from eventkit.identity import person_key

        profile = EventProfile.model_validate({
            "event": {
                "name": "Example Conference", "short_name": "EX", "year": 2030,
                "slug": "ex-2030", "site_url": "https://example.edu",
                "registration_form_url": "https://example.edu/form",
            },
            "schedule": {"start_date": "2030-06-01", "end_date": "2030-06-02"},
            "branding": {"site_name": "EX 2030"},
            "drupal": {"field_map": {"fields": {
                "email": {"key": "email", "kind": "email"}}}},
            "links": {
                "reimbursement": {
                    "label": "Reimbursement", "url": "https://example.edu/reimburse",
                    "param_style": "fragment", "sensitivity": "pii",
                    "prefill": {"Signer_email": "{email}"},
                }
            },
        })
        assert profile.links["reimbursement"].param_style == "fragment"
        assert person_key(uuid=None, email="ada@example.edu")
        """
    )
    result = run_without_heavy_packages(body)
    assert result.returncode == 0, result.stderr


def test_a_realistic_nametag_workload_needs_no_http_client():
    """nametag-press parses submissions and reads roles; it never calls out."""
    body = textwrap.dedent(
        """
        from eventkit.drupal import FieldMap, FieldRule, parse_submission

        field_map = FieldMap(fields={
            "email": FieldRule(key="email", kind="email", required=True),
            "name": FieldRule(key="registrant_name", kind="name", required=True),
            "attendee_status": FieldRule(key="attendee_status", kind="select"),
        })
        submission = parse_submission(
            {"email": "ada@example.edu",
             "registrant_name": {"first": "Ada", "last": "Lovelace"},
             "attendee_status": "Speaker"},
            field_map,
        )
        assert submission.full_name == "Ada Lovelace"
        assert submission.get("attendee_status") == "Speaker"
        """
    )
    result = run_without_heavy_packages(body)
    assert result.returncode == 0, result.stderr


def test_webhook_verification_works_without_fastapi():
    """The verification primitives must not require the web extra.

    Only ``WebhookTokens.dependency()`` needs FastAPI, and it imports it lazily.
    """
    body = textwrap.dedent(
        """
        from eventkit.webhook import WebhookTokens, assert_strong, generate_token

        token = generate_token()
        assert_strong(token, name="tok")
        tokens = WebhookTokens({"registration": token})
        assert tokens.check("registration", token) is True
        assert tokens.check("registration", "wrong") is False
        """
    )
    result = run_without_heavy_packages(body)
    assert result.returncode == 0, result.stderr
