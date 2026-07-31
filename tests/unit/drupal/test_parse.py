"""Tests for :func:`parse_submission` over the golden payloads."""

from __future__ import annotations

import pytest

from eventkit.drupal import FieldMap, FieldRule, parse_submission


class TestFlatAndWrapped:
    def test_flat_payload(self, drupal_payload, field_map):
        submission = parse_submission(drupal_payload("registration_flat"), field_map)
        assert submission.email == "ada@example.edu"
        assert submission.first_name == "Ada"
        assert submission.last_name == "Lovelace"
        assert submission.sid == 101
        assert submission.serial == 12
        assert submission.uuid == "3f2a9c14-8b7d-4e51-9a2f-1c6b8d0e5a73"
        assert submission.get("tickets_sold_separately") is True

    def test_wrapped_payload_reads_the_data_block(self, drupal_payload, field_map):
        submission = parse_submission(drupal_payload("registration_wrapped"), field_map)
        assert submission.email == "grace@example.edu"
        assert submission.first_name == "Grace"
        # sid and serial live at the root when the body is wrapped.
        assert submission.sid == 102
        assert submission.serial == 13
        assert submission.get("attendee_status") == "Speaker"
        assert submission.get("lodging") is True

    def test_both_shapes_produce_the_same_field_set(self, drupal_payload, field_map):
        flat = parse_submission(drupal_payload("registration_flat"), field_map)
        wrapped = parse_submission(drupal_payload("registration_wrapped"), field_map)
        assert set(flat.fields) == set(wrapped.fields)


class TestNames:
    def test_composite_name(self, drupal_payload, field_map):
        submission = parse_submission(drupal_payload("registration_flat"), field_map)
        assert (submission.first_name, submission.last_name) == ("Ada", "Lovelace")

    def test_bare_name_string_is_split(self, drupal_payload, field_map):
        submission = parse_submission(drupal_payload("registration_bare_name"), field_map)
        assert (submission.first_name, submission.last_name) == ("Ada", "Lovelace")

    def test_one_word_name(self, drupal_payload, field_map):
        submission = parse_submission(drupal_payload("registration_one_word_name"), field_map)
        assert submission.first_name == "Prince"
        assert submission.last_name is None
        assert submission.full_name == "Prince"

    def test_three_part_name_keeps_split_none_1_semantics(self, drupal_payload, field_map):
        submission = parse_submission(
            drupal_payload("registration_three_part_name"), field_map
        )
        assert (submission.first_name, submission.last_name) == ("Ada", "B Lovelace")

    def test_explicit_fields_take_precedence_over_composite(self):
        field_map = FieldMap(
            fields={
                "email": FieldRule(key="email", kind="email"),
                "first_name": FieldRule(key="fname"),
                "last_name": FieldRule(key="lname"),
                "name": FieldRule(key="registrant_name", kind="name"),
            }
        )
        payload = {
            "email": "ada@example.edu",
            "fname": "Explicit",
            "lname": "Wins",
            "registrant_name": {"first": "Composite", "last": "Loses"},
        }
        submission = parse_submission(payload, field_map)
        assert (submission.first_name, submission.last_name) == ("Explicit", "Wins")

    def test_composite_fills_what_explicit_rules_leave_empty(self):
        field_map = FieldMap(
            fields={
                "email": FieldRule(key="email", kind="email"),
                "first_name": FieldRule(key="fname"),
                "name": FieldRule(key="registrant_name", kind="name"),
            }
        )
        payload = {
            "email": "ada@example.edu",
            "fname": "Explicit",
            "registrant_name": {"first": "Ignored", "last": "FromComposite"},
        }
        submission = parse_submission(payload, field_map)
        assert submission.first_name == "Explicit"
        assert submission.last_name == "FromComposite"


class TestEmail:
    def test_composite_email_confirm(self, drupal_payload, field_map):
        submission = parse_submission(drupal_payload("registration_email_confirm"), field_map)
        assert submission.email == "ada@example.edu"

    def test_fallback_key_is_tried(self):
        field_map = FieldMap(
            fields={"email": FieldRule(key=["email", "confirm_email_address"], kind="email")}
        )
        submission = parse_submission(
            {"confirm_email_address": "Fallback@Example.EDU"}, field_map
        )
        assert submission.email == "fallback@example.edu"

    def test_first_key_wins_when_both_present(self):
        field_map = FieldMap(
            fields={"email": FieldRule(key=["email", "confirm_email_address"], kind="email")}
        )
        submission = parse_submission(
            {"email": "primary@example.edu", "confirm_email_address": "other@example.edu"},
            field_map,
        )
        assert submission.email == "primary@example.edu"


class TestSelectOther:
    def test_other_branch(self, drupal_payload, field_map):
        submission = parse_submission(drupal_payload("registration_select_other"), field_map)
        assert submission.get("gender_identity") == "Genderqueer"

    def test_select_branch(self, drupal_payload, field_map):
        submission = parse_submission(drupal_payload("registration_select_normal"), field_map)
        assert submission.get("gender_identity") == "Woman"


class TestBooleanCoercion:
    def test_mixed_truthiness_spellings(self, drupal_payload, field_map):
        submission = parse_submission(
            drupal_payload("registration_checkbox_variants"), field_map
        )
        assert submission.get("student") is True
        assert submission.get("lodging") is True
        assert submission.get("presenting_poster") is True
        assert submission.get("tickets_sold_separately") is True

    def test_falsy_poster_flag(self, drupal_payload, field_map):
        submission = parse_submission(drupal_payload("poster_no"), field_map)
        assert submission.fields["presenting_poster"] is False


class TestEmptyStrings:
    def test_blank_values_normalise_to_none(self, drupal_payload, field_map):
        submission = parse_submission(drupal_payload("registration_empty_strings"), field_map)
        assert submission.serial is None
        assert submission.last_name is None
        assert submission.fields["home_institution_or_organization"] is None
        assert submission.fields["poster_title"] is None
        assert submission.fields["faculty_adviser_name"] is None
        assert submission.fields["t_shirt_size"] is None


class TestMetadata:
    def test_serial_without_sid(self, drupal_payload, field_map):
        submission = parse_submission(drupal_payload("registration_serial_only"), field_map)
        assert submission.serial == 23
        assert submission.sid is None

    def test_missing_email_yields_no_person_key(self, drupal_payload, field_map):
        submission = parse_submission(drupal_payload("registration_missing_email"), field_map)
        assert submission.email is None
        assert submission.person_key is None
        assert submission.is_valid is False

    def test_unresolved_uuid_token_falls_back_to_email_hash(self, drupal_payload, field_map):
        submission = parse_submission(
            drupal_payload("registration_unresolved_uuid"), field_map
        )
        key = submission.person_key
        assert key is not None
        assert "[" not in key
        assert len(key) == 32

    def test_uuid_is_carried_through(self, drupal_payload, field_map):
        submission = parse_submission(drupal_payload("registration_flat"), field_map)
        assert submission.person_key == submission.uuid


class TestRequiredFields:
    def test_missing_required_field_is_reported_not_raised(self):
        field_map = FieldMap(
            fields={
                "email": FieldRule(key="email", kind="email", required=True),
                "name": FieldRule(key="registrant_name", kind="name", required=True),
            }
        )
        submission = parse_submission({"registrant_name": {"first": "Ada"}}, field_map)
        assert submission.missing_required == ["email"]
        assert submission.is_valid is False

    def test_present_required_fields_produce_no_complaint(self, drupal_payload, field_map):
        submission = parse_submission(drupal_payload("registration_flat"), field_map)
        assert submission.missing_required == []
        assert submission.is_valid is True

    def test_false_boolean_is_not_a_missing_required_field(self):
        field_map = FieldMap(
            fields={"tickets_sold_separately": FieldRule(
                key="tickets_sold_separately", kind="bool", required=True)}
        )
        submission = parse_submission({"tickets_sold_separately": "0"}, field_map)
        assert submission.get("tickets_sold_separately", False) is False
        assert submission.missing_required == []


class TestUnmappedKeys:
    def test_unknown_element_keys_are_reported(self, drupal_payload, field_map):
        submission = parse_submission(drupal_payload("registration_unmapped_keys"), field_map)
        assert "dietary_restrictions_v2" in submission.unmapped_keys
        assert "newly_renamed_element" in submission.unmapped_keys

    def test_mapped_keys_are_not_reported(self, drupal_payload, field_map):
        submission = parse_submission(drupal_payload("registration_flat"), field_map)
        assert "email" not in submission.unmapped_keys
        assert "registrant_name" not in submission.unmapped_keys

    def test_metadata_keys_are_not_reported_as_unmapped(self, drupal_payload, field_map):
        submission = parse_submission(drupal_payload("registration_flat"), field_map)
        for key in ("sid", "serial", "uuid"):
            assert key not in submission.unmapped_keys

    def test_tracking_can_be_disabled(self, drupal_payload, field_map):
        submission = parse_submission(
            drupal_payload("registration_unmapped_keys"), field_map, track_unmapped=False
        )
        assert submission.unmapped_keys == []


class TestRawPayloadHandling:
    def test_raw_is_retained_for_replay(self, drupal_payload, field_map):
        payload = drupal_payload("registration_flat")
        submission = parse_submission(payload, field_map)
        assert submission.raw == payload

    def test_raw_is_excluded_from_dumps(self, drupal_payload, field_map):
        submission = parse_submission(drupal_payload("registration_flat"), field_map)
        assert "raw" not in submission.model_dump()

    def test_raw_is_excluded_from_repr(self, drupal_payload, field_map):
        submission = parse_submission(drupal_payload("registration_flat"), field_map)
        # A submission that lands in a log line must not drag the whole original
        # payload with it. The mapped `fields` are expected in the repr; `raw` is
        # the part that must not be, since it holds everything Drupal sent.
        assert "raw=" not in repr(submission)

    def test_comment_annotations_are_not_reported_as_unmapped(
        self, drupal_payload, field_map
    ):
        submission = parse_submission(drupal_payload("registration_missing_email"), field_map)
        assert "_comment" not in submission.unmapped_keys


class TestTotality:
    @pytest.mark.parametrize("bad", [{}, {"data": None}, {"data": "nonsense"}])
    def test_degenerate_payloads_do_not_raise(self, bad, field_map):
        submission = parse_submission(bad, field_map)
        assert submission.person_key is None

    def test_every_golden_fixture_parses_without_raising(
        self, all_drupal_payloads, field_map
    ):
        assert all_drupal_payloads, "golden fixtures should ship in the package"
        for name, payload in all_drupal_payloads.items():
            submission = parse_submission(payload, field_map)
            assert submission is not None, name
