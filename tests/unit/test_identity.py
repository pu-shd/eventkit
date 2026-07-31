"""Tests for the frozen ``person_key`` contract.

These tests are deliberately written to fail if the derivation changes. The email
branch is verified by an **independent re-computation** with the prefix and
truncation length written out as literals here rather than imported from the
module, so editing ``_EMAIL_KEY_PREFIX`` or ``_EMAIL_KEY_LENGTH`` in
``eventkit.identity`` breaks this file. That is the intent: changing the
derivation orphans every row in every application's database, and it must not be
possible to do it quietly.
"""

from __future__ import annotations

import hashlib

import pytest

from eventkit.identity import (
    PERSON_KEY_VERSION,
    IdentityError,
    diff_populations,
    is_uuid_keyed,
    normalize_email,
    person_key,
)

# Written out literally. Do not import these from the module under test.
FROZEN_PREFIX = "email:"
FROZEN_LENGTH = 32


def expected_email_key(email: str) -> str:
    """Independent implementation of the email branch of the contract."""
    return hashlib.sha256(f"{FROZEN_PREFIX}{email}".encode()).hexdigest()[:FROZEN_LENGTH]


class TestNormalizeEmail:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("ada@example.edu", "ada@example.edu"),
            ("  Ada@Example.EDU  ", "ada@example.edu"),
            ("ADA@EXAMPLE.EDU", "ada@example.edu"),
            ("", None),
            ("   ", None),
            (None, None),
            # Placeholder spellings Drupal emits for an unfilled field.
            ("n/a", None),
            ("NULL", None),
            ("-", None),
        ],
    )
    def test_normalizes(self, raw, expected):
        assert normalize_email(raw) == expected

    def test_nfkc_folds_fullwidth(self):
        # Full-width characters normalise to ASCII, so the same human typing on a
        # CJK keyboard does not become a second person.
        assert normalize_email("ａｄａ@example.edu") == "ada@example.edu"

    def test_non_string_input_is_coerced(self):
        assert normalize_email(12345) == "12345"


class TestPersonKeyFrozenContract:
    def test_version_is_one(self):
        # If this changes, a migration rewriting every person_key column in five
        # databases must accompany it.
        assert PERSON_KEY_VERSION == 1

    def test_uuid_is_preferred_and_used_verbatim(self):
        uuid = "3f2a9c14-8b7d-4e51-9a2f-1c6b8d0e5a73"
        assert person_key(uuid=uuid, email="ada@example.edu") == uuid

    def test_uuid_is_lowercased_and_stripped(self):
        uuid = "3F2A9C14-8B7D-4E51-9A2F-1C6B8D0E5A73"
        assert person_key(uuid=f"  {uuid}  ", email=None) == uuid.lower()

    def test_email_branch_matches_independent_computation(self):
        assert person_key(uuid=None, email="ada@example.edu") == expected_email_key(
            "ada@example.edu"
        )

    def test_email_branch_is_32_hex_chars(self):
        key = person_key(uuid=None, email="ada@example.edu")
        assert len(key) == FROZEN_LENGTH
        assert all(c in "0123456789abcdef" for c in key)

    def test_email_key_is_not_the_email(self):
        # A person_key appears in URLs, logs and public JSON. It must not be an
        # address: reusing an admin schema on a public route is exactly how the
        # poster gallery leaked every presenter's email.
        key = person_key(uuid=None, email="ada@example.edu")
        assert "@" not in key
        assert "ada" not in key

    def test_email_normalisation_applies_before_hashing(self):
        assert person_key(uuid=None, email="  Ada@Example.EDU ") == person_key(
            uuid=None, email="ada@example.edu"
        )

    def test_different_emails_give_different_keys(self):
        assert person_key(uuid=None, email="ada@example.edu") != person_key(
            uuid=None, email="grace@example.edu"
        )

    def test_uuid_survives_an_email_correction(self):
        # The entire reason uuid is preferred.
        uuid = "3f2a9c14-8b7d-4e51-9a2f-1c6b8d0e5a73"
        before = person_key(uuid=uuid, email="typo@example.edu")
        after = person_key(uuid=uuid, email="correct@example.edu")
        assert before == after


class TestPersonKeyRejectsUnusableIdentity:
    def test_no_identity_at_all_raises(self):
        with pytest.raises(IdentityError):
            person_key(uuid=None, email=None)

    def test_empty_strings_raise(self):
        with pytest.raises(IdentityError):
            person_key(uuid="", email="   ")

    @pytest.mark.parametrize("placeholder", ["", "0", "none", "null", "N/A", "-", "--"])
    def test_placeholder_uuid_falls_back_to_email(self, placeholder):
        assert person_key(uuid=placeholder, email="ada@example.edu") == expected_email_key(
            "ada@example.edu"
        )

    @pytest.mark.parametrize(
        "token",
        [
            "[webform_submission:uuid]",
            "[current-user:uuid]",
            "[WEBFORM_SUBMISSION:UUID]",
        ],
    )
    def test_unresolved_drupal_token_is_refused(self, token):
        """The catastrophic case: an unresolved token would key everyone the same.

        If the ``uuid`` element is misconfigured, Drupal posts the token text
        itself. Accepting it verbatim would collapse the entire roster onto one
        primary key.
        """
        key = person_key(uuid=token, email="ada@example.edu")
        assert key == expected_email_key("ada@example.edu")

    def test_unresolved_token_with_no_email_raises_rather_than_colliding(self):
        with pytest.raises(IdentityError):
            person_key(uuid="[webform_submission:uuid]", email=None)

    def test_two_people_with_unresolved_tokens_do_not_collide(self):
        a = person_key(uuid="[webform_submission:uuid]", email="ada@example.edu")
        b = person_key(uuid="[webform_submission:uuid]", email="grace@example.edu")
        assert a != b


class TestIsUuidKeyed:
    def test_uuid_key(self):
        assert is_uuid_keyed("3f2a9c14-8b7d-4e51-9a2f-1c6b8d0e5a73") is True

    def test_email_key(self):
        assert is_uuid_keyed(expected_email_key("ada@example.edu")) is False


class TestDiffPopulations:
    def test_identical_populations_are_clean(self):
        rows = [{"person_key": "k1", "first_name": "Ada"}]
        diff = diff_populations(rows, rows, label_a="lodging", label_b="nametags")
        assert diff.is_clean
        assert diff.in_both == ("k1",)

    def test_reports_who_is_missing_from_which_side(self):
        diff = diff_populations(
            [{"person_key": "k1"}, {"person_key": "k2"}],
            [{"person_key": "k2"}, {"person_key": "k3"}],
            label_a="lodging",
            label_b="nametags",
        )
        assert diff.only_in_a == ("k1",)
        assert diff.only_in_b == ("k3",)
        assert diff.in_both == ("k2",)
        assert not diff.is_clean

    def test_detects_conflicting_fields(self):
        diff = diff_populations(
            [{"person_key": "k1", "last_name": "Lovelace"}],
            [{"person_key": "k1", "last_name": "Byron"}],
        )
        assert diff.conflicts["k1"]["last_name"] == ("Lovelace", "Byron")

    def test_email_conflicts_compare_normalised(self):
        diff = diff_populations(
            [{"person_key": "k1", "email": "Ada@Example.EDU"}],
            [{"person_key": "k1", "email": "ada@example.edu"}],
        )
        assert diff.is_clean

    def test_missing_value_on_one_side_is_not_a_conflict(self):
        diff = diff_populations(
            [{"person_key": "k1", "last_name": "Lovelace"}],
            [{"person_key": "k1"}],
        )
        assert diff.is_clean

    def test_keys_are_derived_when_absent(self):
        diff = diff_populations(
            [{"email": "ada@example.edu"}],
            [{"uuid": None, "email": "ada@example.edu"}],
        )
        assert diff.is_clean
        assert diff.in_both == (expected_email_key("ada@example.edu"),)

    def test_render_does_not_leak_full_keys_or_addresses(self):
        diff = diff_populations(
            [{"person_key": "k1", "last_name": "Lovelace"}],
            [{"person_key": "k1", "last_name": "Byron"}],
        )
        rendered = diff.render()
        assert "conflicting" in rendered
        assert "@" not in rendered


class TestIdentityMixinIsLazy:
    def test_importing_identity_does_not_require_sqlalchemy(self):
        # The module must be importable by link-forge, which has no database.
        import subprocess
        import sys

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; "
                "sys.modules['sqlalchemy'] = None; "
                "import eventkit.identity as i; "
                "assert i.person_key(uuid=None, email='a@example.edu'); "
                "print('ok')",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "ok" in result.stdout

    def test_mixin_resolves_on_access(self):
        from eventkit import identity

        mixin = identity.IdentityMixin
        assert hasattr(mixin, "person_key")

    def test_unknown_attribute_still_raises(self):
        from eventkit import identity

        with pytest.raises(AttributeError):
            _ = identity.NoSuchThing
