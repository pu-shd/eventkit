"""Table tests for the coercion primitives.

Each of these functions is total, so the tables can be exhaustive over the shapes
Drupal actually emits. The predecessors had no coverage of any of this.
"""

from __future__ import annotations

import pytest

from eventkit.drupal.coerce import (
    FALSY,
    TRUTHY,
    Name,
    coerce_bool,
    coerce_email,
    coerce_int,
    coerce_multivalue,
    coerce_name,
    coerce_select_other,
    coerce_text,
    split_full_name,
    unwrap,
)


class TestUnwrap:
    def test_flat_payload_uses_root_as_data(self):
        payload = {"email": "ada@example.edu"}
        root, data = unwrap(payload)
        assert root is payload
        assert data is payload

    def test_wrapped_payload_returns_inner_block(self):
        payload = {"sid": 1, "data": {"email": "ada@example.edu"}}
        root, data = unwrap(payload)
        assert root is payload
        assert data == {"email": "ada@example.edu"}

    @pytest.mark.parametrize("bad", [None, "string", 42, []])
    def test_non_mapping_payload_is_empty(self, bad):
        root, data = unwrap(bad)
        assert root == {}
        assert data == {}

    def test_non_dict_data_key_falls_back_to_root(self):
        # A form with an element literally named "data" must not break ingest.
        payload = {"data": "not a dict", "email": "ada@example.edu"}
        _root, data = unwrap(payload)
        assert data["email"] == "ada@example.edu"


class TestCoerceText:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("hello", "hello"),
            ("  hello  ", "hello"),
            ("", None),
            ("   ", None),
            (None, None),
            (42, "42"),
            (0, "0"),
            (True, "true"),
            (False, "false"),
            ({"value": " x "}, "x"),
            ({}, None),
            ([" ", "", "y"], "y"),
            ([], None),
        ],
    )
    def test_table(self, raw, expected):
        assert coerce_text(raw) == expected

    def test_empty_string_and_absent_are_indistinguishable(self):
        # This is the fix for the three-valued-string problem: a blank optional
        # field must not be stored as "" in one column and NULL in another.
        assert coerce_text("") is coerce_text(None) is None


class TestCoerceEmail:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("ada@example.edu", "ada@example.edu"),
            ("  Ada@Example.EDU  ", "ada@example.edu"),
            # webform_email_confirm composite
            ({"mail_1": "Ada@Example.EDU", "mail_2": "Ada@Example.EDU"}, "ada@example.edu"),
            ({"email": "grace@example.edu"}, "grace@example.edu"),
            ({"value": "kit@example.edu"}, "kit@example.edu"),
            ({"mail": "old@example.edu"}, "old@example.edu"),
            (["", "ada@example.edu"], "ada@example.edu"),
            ("", None),
            (None, None),
            ({}, None),
            ({"mail_1": ""}, None),
        ],
    )
    def test_table(self, raw, expected):
        assert coerce_email(raw) == expected

    def test_mail_1_wins_over_other_subkeys(self):
        assert coerce_email({"mail_1": "a@example.edu", "email": "b@example.edu"}) == (
            "a@example.edu"
        )

    def test_lowercasing_happens_here_not_in_a_separate_validator(self):
        # ticketed lowercased in a field_validator that the bulk importer never
        # invoked, so the same person could be two rows depending on entry path.
        assert coerce_email("MiXeD@Example.Edu") == "mixed@example.edu"


class TestSplitFullName:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Ada Lovelace", Name("Ada", "Lovelace")),
            ("Ada", Name("Ada", None)),
            ("Ada B Lovelace", Name("Ada", "B Lovelace")),
            ("  Ada   Lovelace  ", Name("Ada", "Lovelace")),
            ("", Name(None, None)),
            ("   ", Name(None, None)),
        ],
    )
    def test_table(self, raw, expected):
        assert split_full_name(raw) == expected

    def test_split_none_1_semantics_are_preserved(self):
        # Deliberately matches all three predecessors. It is wrong for multi-word
        # given names, but the live databases were populated with it, so changing
        # it here would silently disagree with stored rows.
        assert split_full_name("Mary Jane Watson") == Name("Mary", "Jane Watson")


class TestCoerceName:
    def test_composite_first_last(self):
        assert coerce_name({"first": "Ada", "last": "Lovelace"}) == Name("Ada", "Lovelace")

    def test_composite_alternate_subkeys(self):
        assert coerce_name({"first_name": "Ada", "surname": "Lovelace"}) == Name(
            "Ada", "Lovelace"
        )
        assert coerce_name({"given": "Ada", "family": "Lovelace"}) == Name("Ada", "Lovelace")

    def test_bare_string_is_split(self):
        assert coerce_name("Ada Lovelace") == Name("Ada", "Lovelace")

    def test_empty_last_becomes_none(self):
        assert coerce_name({"first": "Ada", "last": ""}) == Name("Ada", None)

    @pytest.mark.parametrize("bad", [None, "", 0, []])
    def test_unusable_input(self, bad):
        assert coerce_name(bad) == Name(None, None)

    def test_composite_with_only_value_key_is_split(self):
        assert coerce_name({"value": "Ada Lovelace"}) == Name("Ada", "Lovelace")

    def test_full_property(self):
        assert coerce_name({"first": "Ada", "last": "Lovelace"}).full == "Ada Lovelace"
        assert coerce_name("Prince").full == "Prince"


class TestCoerceSelectOther:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ({"select": "_other_", "other": "Genderqueer"}, "Genderqueer"),
            ({"select": "Woman", "other": ""}, "Woman"),
            ({"select": "", "other": "Something"}, "Something"),
            ({"select": None, "other": "Something"}, "Something"),
            ({"select": "Man", "other": "ignored"}, "Man"),
            ({"select": "_other_", "other": ""}, None),
            ("Woman", "Woman"),
            ("", None),
            (None, None),
        ],
    )
    def test_table(self, raw, expected):
        assert coerce_select_other(raw) == expected

    def test_only_one_predecessor_implemented_this(self):
        """Regression guard for a real cross-app inconsistency.

        ``posted``'s nametags webhook resolved ``select_other``; its poster
        webhook did not. The same registrant's custom gender identity was stored
        in one app and dropped in the other.
        """
        composite = {"select": "_other_", "other": "Genderqueer"}
        assert coerce_select_other(composite) == "Genderqueer"


class TestCoerceBool:
    @pytest.mark.parametrize("truthy", sorted(TRUTHY))
    def test_all_truthy_spellings(self, truthy):
        assert coerce_bool(truthy) is True
        assert coerce_bool(truthy.upper()) is True
        assert coerce_bool(f"  {truthy}  ") is True

    @pytest.mark.parametrize(
        "falsy", ["", "0", "no", "off", "false", "n", None, 0, False, "maybe", "Yes please"]
    )
    def test_falsy_values(self, falsy):
        assert coerce_bool(falsy) is False

    def test_real_booleans_pass_through(self):
        assert coerce_bool(True) is True
        assert coerce_bool(False) is False

    def test_numbers(self):
        assert coerce_bool(1) is True
        assert coerce_bool(2) is True
        assert coerce_bool(0) is False

    def test_original_five_spellings_still_work(self):
        # ticketed/backend/schemas.py:67 accepted exactly these. The wider TRUTHY
        # set must remain a superset so nothing that parsed true stops doing so.
        for value in ("1", "true", "yes", "on", "checked"):
            assert coerce_bool(value) is True


class TestCoerceInt:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("12", 12),
            (" 7 ", 7),
            (12, 12),
            (12.0, 12),
            ("12.9", 12),
            ("", None),
            ("   ", None),
            (None, None),
            ("abc", None),
            (True, None),
            (False, None),
        ],
    )
    def test_table(self, raw, expected):
        assert coerce_int(raw) == expected

    def test_empty_string_does_not_raise(self):
        """The predecessors used a bare ``int(sid)``.

        On an empty string that raises ``ValueError`` inside a pydantic
        ``mode="before"`` validator, which surfaces as a 500 on the webhook. Drupal
        records a failed handler and moves on, so the registration is simply lost.
        """
        assert coerce_int("") is None


class TestCoerceMultivalue:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ({"opt_a": 1, "opt_b": 0}, ["opt_a"]),
            ({"opt_a": "opt_a", "opt_b": False}, ["opt_a"]),
            ({"opt_a": True, "opt_b": None}, ["opt_a"]),
            (["a", "", "b"], ["a", "b"]),
            ("single", ["single"]),
            ("", []),
            (None, []),
            ({}, []),
        ],
    )
    def test_table(self, raw, expected):
        assert coerce_multivalue(raw) == expected

    def test_false_valued_key_is_excluded(self):
        # coerce_text(False) is the truthy string "false"; the exclusion test must
        # not be a bare truthiness check on the coerced text.
        assert coerce_multivalue({"picked": True, "not_picked": False}) == ["picked"]

    def test_comma_containing_string_is_not_split(self):
        # morgan-state-...-form.yaml uses full URLs as radio option keys.
        value = "https://example.com/e/a-tickets-1,2"
        assert coerce_multivalue(value) == [value]

    def test_falsy_and_truthy_sets_do_not_overlap(self):
        assert not (TRUTHY & FALSY)
