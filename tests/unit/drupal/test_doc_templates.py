"""The webform templates shipped in ``docs/drupal/templates`` must actually work.

Documentation that drifts from the code is worse than no documentation, because
someone follows it. These templates are what an adopter builds their event's
registration form from, so they are parsed and field-mapped here exactly as
eventkit would parse a real Drupal export.

The full template is assembled by concatenation, which is also how the docs tell
adopters to combine fragments — there is no include mechanism in Drupal webform
YAML.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eventkit.drupal import WebformSchema

TEMPLATES = Path(__file__).resolve().parents[3] / "docs" / "drupal" / "templates"

CORE = "registration.core.yaml"
FRAGMENTS = [
    "fragment.swag.yaml",
    "fragment.lodging.yaml",
    "fragment.poster.yaml",
    "fragment.ticketing.yaml",
]

#: Logical fields the assembled template must supply, by owning application.
EXPECTED_FIELDS = {
    "core": ["email", "name", "uuid", "sid", "serial", "attendee_status",
             "home_institution_or_organization"],
    "swag": ["t_shirt_size"],
    "lodging": ["lodging", "gender_identity", "roommate_preference",
                "identified_roommate"],
    "poster": ["presenting_poster", "poster_title", "faculty_adviser_name",
               "poster_presentation_abstract"],
    "ticketing": ["tickets_sold_separately", "destination_url"],
}

ALL_FIELDS = [f for group in EXPECTED_FIELDS.values() for f in group]


def _require(name: str) -> Path:
    path = TEMPLATES / name
    if not path.is_file():
        pytest.skip(f"{name} not present (docs not included in this build)")
    return path


def _assembled() -> str:
    return "\n".join(
        _require(name).read_text(encoding="utf-8") for name in [CORE, *FRAGMENTS]
    )


class TestTemplatesParse:
    @pytest.mark.parametrize("name", [CORE, *FRAGMENTS])
    def test_each_file_parses(self, name):
        schema = WebformSchema.from_yaml_text(_require(name).read_text(encoding="utf-8"))
        assert schema.elements, f"{name} produced no elements"

    def test_assembled_form_parses(self):
        assert WebformSchema.from_yaml_text(_assembled()).elements


class TestTemplatesMap:
    def test_every_documented_field_resolves(self):
        """The whole point: an adopter using these templates gets a clean map."""
        schema = WebformSchema.from_yaml_text(_assembled())
        field_map, _ = schema.infer_field_map(want=ALL_FIELDS)
        missing = [f for f in ALL_FIELDS if f not in field_map.fields]
        assert not missing, f"template does not supply: {missing}"

    @pytest.mark.parametrize("group", sorted(EXPECTED_FIELDS))
    def test_group_resolves(self, group):
        schema = WebformSchema.from_yaml_text(_assembled())
        field_map, _ = schema.infer_field_map(want=EXPECTED_FIELDS[group])
        missing = [f for f in EXPECTED_FIELDS[group] if f not in field_map.fields]
        assert not missing, f"{group} fields missing: {missing}"

    def test_nested_fieldset_children_resolve(self):
        """Regression guard for the flattening fix.

        ``gender_identity`` lives inside the ``lodging_section`` fieldset and
        ``poster_title`` inside ``poster_presentation_details``. Both were
        invisible before containers were flattened.
        """
        schema = WebformSchema.from_yaml_text(_assembled())
        assert schema.container_of("gender_identity") == "lodging_section"
        assert schema.container_of("poster_title") == "poster_presentation_details"

    def test_kinds_are_inferred_from_element_types(self):
        schema = WebformSchema.from_yaml_text(_assembled())
        field_map, _ = schema.infer_field_map(
            want=["email", "gender_identity", "lodging", "name"]
        )
        assert field_map.rule("email").kind == "email"
        assert field_map.rule("gender_identity").kind == "select_other"
        assert field_map.rule("name").kind == "name"


class TestFullTemplateIsInSync:
    """``registration.full.yaml`` is the fragments concatenated.

    It is committed because a ready-made complete form is convenient, which also
    means it can drift from the fragments it was built from. Pin it.
    """

    def test_matches_the_concatenated_fragments(self):
        full = _require("registration.full.yaml").read_text(encoding="utf-8")
        expected = "".join(
            _require(name).read_text(encoding="utf-8") for name in [CORE, *FRAGMENTS]
        )
        assert full == expected, (
            "registration.full.yaml is stale. Regenerate it:\n"
            "  cat docs/drupal/templates/registration.core.yaml "
            "docs/drupal/templates/fragment.*.yaml "
            "> docs/drupal/templates/registration.full.yaml"
        )


class TestTemplateHygiene:
    def test_no_unreplaced_placeholders_leak_into_element_keys(self):
        """Placeholders belong in copy, never in a key or an option key."""
        schema = WebformSchema.from_yaml_text(_assembled())
        for key in schema.elements:
            assert "{{" not in key, f"placeholder in element key {key!r}"

    def test_identity_plumbing_is_present(self):
        """uuid/sid/serial are declared explicitly.

        The real CAARMS export defined none of them, relying entirely on Remote
        Post submission properties. Declaring them costs nothing and removes the
        ambiguity — see docs/drupal/01-design.md.
        """
        schema = WebformSchema.from_yaml_text(_assembled())
        for key in ("uuid", "sid", "serial"):
            assert key in schema.elements, f"{key} element missing from the core template"

    def test_no_real_institutional_addresses(self):
        text = _assembled()
        assert "princeton.edu" not in text.lower(), (
            "use example.edu in shipped templates"
        )

    def test_no_nested_states_bug(self):
        """The predecessor form had '#states' nested inside '#states'.

        Drupal ignores the inner key, so the rule never fires — silently. The
        templates must not reproduce it.
        """
        import yaml

        raw = yaml.safe_load(_assembled())

        def walk(node, path="") -> None:
            if not isinstance(node, dict):
                return
            states = node.get("#states")
            if isinstance(states, dict):
                assert "#states" not in states, f"nested #states at {path}"
            for key, value in node.items():
                if isinstance(key, str) and not key.startswith("#"):
                    walk(value, f"{path}/{key}")

        walk(raw)
