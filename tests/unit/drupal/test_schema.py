"""Webform schema parsing, field-map inference, and the fail-fast resolution order.

The behaviour under test replaces ``ticketed/backend/schema_parser.py``'s
``load_schema()``, whose embedded CAARMS ``DEFAULT_SCHEMA_YAML`` always won
because no ``webform-schema.yml`` shipped in the image.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eventkit.drupal import FieldMap, FieldRule, WebformSchema, resolve_field_map
from eventkit.errors import FieldMapError
from eventkit.eventprofile import EventProfile

EXAMPLE_SCHEMA = Path(__file__).resolve().parents[3] / "examples" / "caarms-2026" / (
    "webform-schema.yml"
)


class TestFieldRule:
    def test_single_key(self):
        assert FieldRule(key="email").keys == ("email",)

    def test_key_list(self):
        rule = FieldRule(key=["email", "confirm_email_address"])
        assert rule.keys == ("email", "confirm_email_address")

    @pytest.mark.parametrize("bad", ["", "   ", [], ["ok", ""]])
    def test_blank_keys_are_rejected(self, bad):
        with pytest.raises(ValueError):
            FieldRule(key=bad)

    def test_unknown_property_is_rejected(self):
        with pytest.raises(ValueError):
            FieldRule(key="email", kynd="email")


class TestFieldMap:
    def test_element_keys_include_all_fallbacks(self):
        field_map = FieldMap(
            fields={"email": FieldRule(key=["email", "confirm_email_address"])}
        )
        assert field_map.element_keys() == {"email", "confirm_email_address"}

    def test_required_keys(self):
        field_map = FieldMap(
            fields={
                "email": FieldRule(key="email", required=True),
                "notes": FieldRule(key="notes"),
            }
        )
        assert field_map.required_keys() == {"email"}

    def test_from_pairs(self):
        field_map = FieldMap.from_pairs({"email": "mail", "name": "registrant_name"})
        assert field_map.rule("email").keys == ("mail",)

    def test_merged_with_lets_the_overlay_win(self):
        base = FieldMap.from_pairs({"email": "email"})
        overlay = FieldMap.from_pairs({"email": "contact_mail"})
        assert base.merged_with(overlay).rule("email").keys == ("contact_mail",)


class TestWebformSchema:
    def test_parses_an_element_only_body(self):
        schema = WebformSchema.from_yaml_text(
            "email:\n  '#type': email\n  '#title': 'Email Address'\n"
        )
        assert schema.element_type("email") == "email"
        assert schema.title("email") == "email address"

    def test_tolerates_a_full_config_envelope(self):
        # Drupal stores elements as a YAML *string* inside the config object.
        schema = WebformSchema.from_yaml_text(
            "id: registration\nelements: |\n  email:\n    '#type': email\n"
        )
        assert schema.element_type("email") == "email"

    def test_ignores_non_mapping_entries(self):
        schema = WebformSchema.from_yaml_text("id: registration\nemail:\n  '#type': email\n")
        assert "id" not in schema.elements

    def test_rejects_a_non_mapping_document(self):
        with pytest.raises(ValueError):
            WebformSchema.from_yaml_text("- just\n- a\n- list\n")

    def test_empty_document_is_an_empty_schema(self):
        assert WebformSchema.from_yaml_text("").elements == {}

    def test_kind_mapping_from_element_type(self):
        schema = WebformSchema.from_yaml_text(
            "\n".join(
                [
                    "a:\n  '#type': email",
                    "b:\n  '#type': webform_name",
                    "c:\n  '#type': checkbox",
                    "d:\n  '#type': webform_select_other",
                    "e:\n  '#type': checkboxes",
                    "f:\n  '#type': number",
                    "g:\n  '#type': textfield",
                    "h:\n  '#type': something_unknown",
                ]
            )
        )
        assert schema.kind_for("a") == "email"
        assert schema.kind_for("b") == "name"
        assert schema.kind_for("c") == "bool"
        assert schema.kind_for("d") == "select_other"
        assert schema.kind_for("e") == "multiselect"
        assert schema.kind_for("f") == "int"
        assert schema.kind_for("g") == "text"
        assert schema.kind_for("h") == "text"

    def test_required_flag_is_read(self):
        schema = WebformSchema.from_yaml_text(
            "email:\n  '#type': email\n  '#required': true\n"
        )
        assert schema.is_required("email") is True


class TestInference:
    @pytest.fixture
    def schema(self):
        if not EXAMPLE_SCHEMA.is_file():
            pytest.skip("examples/caarms-2026/webform-schema.yml not available")
        return WebformSchema.from_path(EXAMPLE_SCHEMA)

    def test_exact_key_matches_produce_no_warnings(self, schema):
        field_map, warnings = schema.infer_field_map(
            want=["email", "attendee_status", "lodging"]
        )
        assert set(field_map.fields) == {"email", "attendee_status", "lodging"}
        assert warnings == []

    def test_kinds_are_taken_from_element_types(self, schema):
        field_map, _ = schema.infer_field_map(want=["email", "lodging", "gender_identity"])
        assert field_map.rule("email").kind == "email"
        assert field_map.rule("lodging").kind == "bool"
        assert field_map.rule("gender_identity").kind == "select_other"

    def test_heuristic_match_warns(self):
        schema = WebformSchema.from_yaml_text(
            "contact_mail:\n  '#type': email\n  '#title': 'Email'\n"
        )
        field_map, warnings = schema.infer_field_map(want=["email"])
        assert field_map.rule("email").keys == ("contact_mail",)
        assert len(warnings) == 1
        assert "inferred" in warnings[0]

    def test_unresolvable_field_is_absent_and_warned(self):
        schema = WebformSchema.from_yaml_text("email:\n  '#type': email\n")
        field_map, warnings = schema.infer_field_map(want=["lodging"])
        assert "lodging" not in field_map.fields
        assert any("lodging" in w for w in warnings)

    def test_never_guesses_silently(self):
        """Every non-exact resolution must be accompanied by a warning."""
        schema = WebformSchema.from_yaml_text(
            "some_mail_field:\n  '#type': email\n"
        )
        field_map, warnings = schema.infer_field_map(want=["email"])
        if "email" in field_map.fields:
            assert warnings, "an inferred field was returned with no warning"


class TestResolveFieldMap:
    def _profile(self, drupal: dict) -> EventProfile:
        return EventProfile.model_validate(
            {
                "event": {
                    "name": "Example Conference",
                    "short_name": "EX",
                    "year": 2030,
                    "slug": "ex-2030",
                    "site_url": "https://example.edu",
                    "registration_form_url": "https://example.edu/form",
                },
                "schedule": {"start_date": "2030-06-01", "end_date": "2030-06-02"},
                "branding": {"site_name": "EX 2030"},
                "drupal": drupal,
            }
        )

    def test_explicit_field_map_wins(self):
        profile = self._profile(
            {"field_map": {"fields": {"email": {"key": "email", "kind": "email"}}}}
        )
        field_map = resolve_field_map(profile, want=["email"])
        assert field_map.rule("email").keys == ("email",)

    def test_missing_required_logical_field_raises_with_a_stub(self):
        profile = self._profile(
            {"field_map": {"fields": {"email": {"key": "email", "kind": "email"}}}}
        )
        with pytest.raises(FieldMapError) as excinfo:
            resolve_field_map(profile, want=["email", "lodging"])
        message = str(excinfo.value)
        assert "lodging" in message
        # The fix should be in the traceback, not in a docs page.
        assert "field_map" in message

    def test_schema_path_is_used_when_no_field_map(self):
        if not EXAMPLE_SCHEMA.is_file():
            pytest.skip("example schema not available")
        profile = self._profile({"webform_schema": str(EXAMPLE_SCHEMA)})
        field_map = resolve_field_map(profile, want=["email", "lodging"])
        assert set(field_map.fields) == {"email", "lodging"}

    def test_nonexistent_schema_path_raises(self):
        profile = self._profile({"webform_schema": "/nonexistent/schema.yml"})
        with pytest.raises(FieldMapError) as excinfo:
            resolve_field_map(profile, want=["email"])
        assert "does not exist" in str(excinfo.value)

    def test_relative_schema_path_resolves_against_base_dir(self):
        if not EXAMPLE_SCHEMA.is_file():
            pytest.skip("example schema not available")
        profile = self._profile({"webform_schema": "webform-schema.yml"})
        field_map = resolve_field_map(
            profile, want=["email"], base_dir=EXAMPLE_SCHEMA.parent
        )
        assert "email" in field_map.fields

    def test_there_is_no_embedded_default(self):
        """The core regression this module exists to prevent.

        A profile declaring neither must fail, not silently fall back to the
        CAARMS field map. The old behaviour meant an adopter's registrations
        parsed into empty columns with no error anywhere.
        """
        with pytest.raises(ValueError):
            self._profile({"join_key": "uuid"})

    def test_no_caarms_field_names_are_baked_into_the_library(self):
        """No event-specific value may reach runtime code in the schema module.

        Checks executable code only — string literals that are not docstrings, plus
        identifiers — rather than grepping raw source. Comments and docstrings must
        stay free to explain *why* the embedded CAARMS field map was removed, which
        they cannot do without naming it. Grepping the file text made that
        explanation itself a build failure.
        """
        import ast

        import eventkit.drupal.schema as schema_module

        tree = ast.parse(Path(schema_module.__file__).read_text(encoding="utf-8"))

        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(
                node,
                (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
            ):
                doc = ast.get_docstring(node, clean=False)
                if doc is not None:
                    docstrings.add(doc)

        runtime_text: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value not in docstrings:
                    runtime_text.append(node.value)
            elif isinstance(node, ast.Name):
                runtime_text.append(node.id)
            elif isinstance(node, ast.Attribute):
                runtime_text.append(node.attr)
            elif isinstance(node, ast.arg):
                runtime_text.append(node.arg)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                runtime_text.append(node.name)
            elif isinstance(node, ast.keyword) and node.arg:
                runtime_text.append(node.arg)

        haystack = "\n".join(runtime_text).lower()
        for leaked in (
            "tickets_sold_separately",
            "t_shirt_size",
            "attendee_status",
            "caarms",
            "princeton",
        ):
            assert leaked.lower() not in haystack, (
                f"{leaked!r} appears in executable code in eventkit.drupal.schema; "
                f"event-specific element names belong in an event profile, not the "
                f"library. (Mentions in comments and docstrings are fine.)"
            )
