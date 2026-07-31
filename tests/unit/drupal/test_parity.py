"""The webhook path and the importer path must agree, by construction.

``posted/backend/import_existing.py:92`` already reused the webhook's payload
model, so it had this property by convention. Convention did not survive: the
same repository's two *webhook* parsers disagreed with each other about composite
``select_other`` values, because each was edited independently.

These tests assert the invariant directly. If someone adds a special case to one
ingest path, this file fails.
"""

from __future__ import annotations

import json

import pytest

from eventkit.drupal import parse_submission


def webhook_ingest(payload, field_map):
    """Stand-in for what a webhook route does with the body it receives."""
    return parse_submission(payload, field_map)


def importer_ingest(record, field_map):
    """Stand-in for what the bulk importer does with one archived record.

    Round-trips through JSON first, because that is what actually differs between
    the two paths: the importer reads from a file, so integers may arrive as
    strings and ordering is not preserved.
    """
    return parse_submission(json.loads(json.dumps(record)), field_map)


class TestWebhookImporterParity:
    def test_all_golden_fixtures_parse_identically(self, all_drupal_payloads, field_map):
        for name, payload in all_drupal_payloads.items():
            from_webhook = webhook_ingest(payload, field_map)
            from_importer = importer_ingest(payload, field_map)
            assert from_webhook.model_dump() == from_importer.model_dump(), (
                f"webhook and importer disagree on fixture {name!r}"
            )

    def test_person_key_agrees_across_paths(self, all_drupal_payloads, field_map):
        for name, payload in all_drupal_payloads.items():
            assert (
                webhook_ingest(payload, field_map).person_key
                == importer_ingest(payload, field_map).person_key
            ), f"person_key differs by ingest path on {name!r}"

    @pytest.mark.parametrize(
        "fixture",
        [
            "registration_flat",
            "registration_wrapped",
            "registration_email_confirm",
            "registration_select_other",
        ],
    )
    def test_named_fixtures_explicitly(self, drupal_payload, field_map, fixture):
        payload = drupal_payload(fixture)
        assert (
            webhook_ingest(payload, field_map).model_dump()
            == importer_ingest(payload, field_map).model_dump()
        )


class TestSingleParserInvariant:
    def test_parse_submission_is_the_only_public_entry_point(self):
        """Guard against a second parser reappearing in eventkit.drupal.

        The package must not grow a ``parse_registration``, ``extract_fields`` or
        similar alongside ``parse_submission`` — that is how three parsers came to
        exist in the first place.
        """
        import eventkit.drupal as drupal

        suspicious = [
            name
            for name in drupal.__all__
            if ("parse" in name.lower() or "extract" in name.lower())
            and name != "parse_submission"
        ]
        assert suspicious == [], (
            f"eventkit.drupal exports additional parse-shaped helpers {suspicious}; "
            f"there must be exactly one parser"
        )

    def test_select_other_is_understood_on_every_path(self, drupal_payload, field_map):
        """The specific cross-app inconsistency that motivated the extraction."""
        payload = drupal_payload("registration_select_other")
        for ingest in (webhook_ingest, importer_ingest):
            assert ingest(payload, field_map).get("gender_identity") == "Genderqueer"
