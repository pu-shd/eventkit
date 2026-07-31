"""Event profile validation, ticketing resolution, affiliation, public projection."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from eventkit.errors import EventProfileError
from eventkit.eventprofile import (
    AffiliationRules,
    EventProfile,
    Match,
    TicketTier,
    to_public_dict,
)


class TestValidProfile:
    def test_minimal_profile_validates(self, event_profile):
        assert event_profile.event.slug == "excon-2030"
        assert event_profile.event.title == "EXCON 2030"

    def test_defaults_are_safe(self, event_profile):
        # An app that does not use these sections must still boot.
        assert event_profile.lodging.enabled is False
        assert event_profile.swag.enabled is False
        assert event_profile.notify.transport == "log"
        assert event_profile.branding.brand_color == "#e77500"

    def test_checkin_day_labels_are_derived(self, event_profile):
        day = event_profile.schedule.day("2030-06-01")
        assert day is not None and day.label
        banquet = event_profile.schedule.day("2030-06-02-banquet")
        assert banquet is not None and banquet.label == "Banquet"

    def test_day_keys_are_iso(self, event_profile):
        for key in event_profile.checkin_day_keys():
            assert "/" not in key


class TestInvalidProfiles:
    """Each case is a real misconfiguration an adopter can make."""

    def test_day_key_with_a_slash_is_rejected(self, minimal_profile_dict):
        minimal_profile_dict["schedule"]["checkin_days"][0]["key"] = "6/28"
        with pytest.raises(ValidationError):
            EventProfile.model_validate(minimal_profile_dict)

    def test_duplicate_day_keys_are_rejected(self, minimal_profile_dict):
        days = minimal_profile_dict["schedule"]["checkin_days"]
        days[1]["key"] = days[0]["key"]
        days[1]["date"] = days[0]["date"]
        with pytest.raises(ValidationError):
            EventProfile.model_validate(minimal_profile_dict)

    def test_day_outside_event_range_is_rejected(self, minimal_profile_dict):
        minimal_profile_dict["schedule"]["checkin_days"][0]["date"] = "2031-01-01"
        with pytest.raises(ValidationError):
            EventProfile.model_validate(minimal_profile_dict)

    def test_end_before_start_is_rejected(self, minimal_profile_dict):
        minimal_profile_dict["schedule"]["end_date"] = "2029-01-01"
        with pytest.raises(ValidationError):
            EventProfile.model_validate(minimal_profile_dict)

    def test_unknown_timezone_is_rejected(self, minimal_profile_dict):
        minimal_profile_dict["schedule"]["timezone"] = "Mars/Olympus_Mons"
        with pytest.raises(ValidationError):
            EventProfile.model_validate(minimal_profile_dict)

    def test_bad_slug_is_rejected(self, minimal_profile_dict):
        minimal_profile_dict["event"]["slug"] = "Not A Slug"
        with pytest.raises(ValidationError):
            EventProfile.model_validate(minimal_profile_dict)

    def test_bad_brand_hex_is_rejected(self, minimal_profile_dict):
        minimal_profile_dict["branding"]["brand_color"] = "orange"
        with pytest.raises(ValidationError):
            EventProfile.model_validate(minimal_profile_dict)

    def test_unknown_top_level_key_is_rejected(self, minimal_profile_dict):
        minimal_profile_dict["lodgeing"] = {"enabled": True}
        with pytest.raises(ValidationError):
            EventProfile.model_validate(minimal_profile_dict)

    def test_drupal_without_field_map_or_schema_is_rejected(self, minimal_profile_dict):
        minimal_profile_dict["drupal"] = {"join_key": "uuid"}
        with pytest.raises(ValidationError):
            EventProfile.model_validate(minimal_profile_dict)

    def test_bad_avery_template_is_rejected(self, minimal_profile_dict):
        minimal_profile_dict["nametags"] = {"avery_template": "9999"}
        with pytest.raises(ValidationError):
            EventProfile.model_validate(minimal_profile_dict)

    def test_duplicate_swag_keys_are_rejected(self, minimal_profile_dict):
        minimal_profile_dict["swag"] = {
            "enabled": True,
            "options": [
                {"key": "USML", "label": "Small"},
                {"key": "USML", "label": "Small again"},
            ],
        }
        with pytest.raises(ValidationError):
            EventProfile.model_validate(minimal_profile_dict)

    def test_duplicate_lodging_rule_codes_are_rejected(self, minimal_profile_dict):
        minimal_profile_dict["lodging"] = {
            "enabled": True,
            "rules": [
                {"code": "OVER_CAPACITY", "severity": "error"},
                {"code": "OVER_CAPACITY", "severity": "warning"},
            ],
        }
        with pytest.raises(ValidationError):
            EventProfile.model_validate(minimal_profile_dict)

    def test_lowercase_rule_code_is_rejected(self, minimal_profile_dict):
        minimal_profile_dict["lodging"] = {
            "enabled": True,
            "rules": [{"code": "over_capacity"}],
        }
        with pytest.raises(ValidationError):
            EventProfile.model_validate(minimal_profile_dict)


class TestDiscountCodesAreNeverInTheProfile:
    def test_a_literal_code_is_rejected_as_an_env_var_name(self):
        # The whole point: `discount_code_env` holds a NAME. Someone pasting an
        # actual discount code here must be stopped by validation.
        #
        # The string below is shaped like a real code (mixed case and digits, no
        # underscores) but is synthetic. Real codes are not written down anywhere
        # in this repository, including in tests — CI greps for them.
        with pytest.raises(ValidationError):
            TicketTier(key="ga", label="GA", discount_code_env="2030EXAMPLEGA")

    def test_lowercase_name_is_rejected(self):
        with pytest.raises(ValidationError):
            TicketTier(key="ga", label="GA", discount_code_env="eventbrite_discount_ga")

    def test_upper_snake_case_is_accepted(self):
        tier = TicketTier(key="ga", label="GA", discount_code_env="EVENTBRITE_DISCOUNT_GA")
        assert tier.discount_code_env == "EVENTBRITE_DISCOUNT_GA"

    def test_code_is_read_from_the_environment(self, event_profile):
        tier = TicketTier(key="ga", label="GA", discount_code_env="EVENTBRITE_DISCOUNT_GA")
        assert event_profile.discount_code(tier, {"EVENTBRITE_DISCOUNT_GA": "ABC123"}) == (
            "ABC123"
        )

    def test_absent_variable_is_none_not_an_error(self, event_profile):
        tier = TicketTier(key="ga", label="GA", discount_code_env="EVENTBRITE_DISCOUNT_GA")
        assert event_profile.discount_code(tier, {}) is None


class TestTicketing:
    @pytest.fixture
    def ticketing(self):
        from eventkit.eventprofile import Ticketing

        return Ticketing(
            slug="excon-2030",
            tiers=[
                TicketTier(
                    key="affiliate",
                    label="Affiliate",
                    discount_code_env="DISCOUNT_AFFILIATE",
                    match=Match(email_domain_suffix=["example.edu"]),
                ),
                TicketTier(
                    key="student",
                    label="Student",
                    discount_code_env="DISCOUNT_STUDENT",
                    match=Match(field_equals={"student": "yes"}),
                ),
                TicketTier(key="general", label="General", match=Match(default=True)),
            ],
        )

    def test_domain_match_wins(self, ticketing):
        tier = ticketing.resolve_tier(email="ada@example.edu", fields={})
        assert tier is not None and tier.key == "affiliate"

    def test_subdomain_matches(self, ticketing):
        tier = ticketing.resolve_tier(email="ada@cs.example.edu", fields={})
        assert tier is not None and tier.key == "affiliate"

    def test_field_match(self, ticketing):
        tier = ticketing.resolve_tier(email="ada@elsewhere.org", fields={"student": True})
        assert tier is not None and tier.key == "student"

    def test_default_tier_is_the_fallback(self, ticketing):
        tier = ticketing.resolve_tier(email="ada@elsewhere.org", fields={})
        assert tier is not None and tier.key == "general"

    def test_anonymous_falls_back_to_default(self, ticketing):
        tier = ticketing.resolve_tier(email=None, fields={})
        assert tier is not None and tier.key == "general"

    def test_order_is_significant(self, ticketing):
        # Both predicates hold; the earlier tier wins.
        tier = ticketing.resolve_tier(email="ada@example.edu", fields={"student": True})
        assert tier is not None and tier.key == "affiliate"

    def test_purchase_url_without_discount(self, ticketing):
        url = ticketing.purchase_url(event_id="1234567890")
        assert url == "https://www.eventbrite.com/e/excon-2030-tickets-1234567890"

    def test_purchase_url_with_discount(self, ticketing):
        url = ticketing.purchase_url(event_id="1234567890", discount_code="ABC")
        assert url.endswith("?discount=ABC")

    def test_exemption_unchecked_means_exempt(self, ticketing):
        assert ticketing.is_exempt({"tickets_sold_separately": False}) is True
        assert ticketing.is_exempt({"tickets_sold_separately": True}) is False


class TestAffiliation:
    @pytest.fixture
    def rules(self):
        return AffiliationRules(
            domain_map={
                "example.edu": "Example University",
                "cs.example.edu": "Example CS Department",
            }
        )

    def test_declared_value_wins(self, rules):
        assert rules.normalize(email="ada@example.edu", declared="Somewhere Else") == (
            "Somewhere Else"
        )

    @pytest.mark.parametrize("placeholder", ["", "  ", "n/a", "N/A", "none", "-", "--", None])
    def test_placeholder_falls_back_to_the_domain(self, rules, placeholder):
        assert rules.normalize(email="ada@example.edu", declared=placeholder) == (
            "Example University"
        )

    def test_longest_domain_suffix_wins(self, rules):
        assert rules.normalize(email="ada@cs.example.edu", declared=None) == (
            "Example CS Department"
        )

    def test_unknown_domain_yields_none(self, rules):
        assert rules.normalize(email="ada@elsewhere.org", declared=None) is None

    def test_malformed_email_yields_none(self, rules):
        assert rules.normalize(email="not-an-email", declared=None) is None


class TestPerAppValidation:
    def test_missing_required_section_raises_with_all_paths_listed(self, event_profile):
        with pytest.raises(EventProfileError) as excinfo:
            event_profile.validate_for_app(
                "ticket-reconciler", require=["swag.options", "ticketing.tiers"]
            )
        message = str(excinfo.value)
        assert "swag.options" in message
        assert "ticketing.tiers" in message
        assert "ticket-reconciler" in message

    def test_satisfied_requirements_pass(self, event_profile):
        event_profile.validate_for_app(
            "nametag-press", require=["roles.options", "schedule.checkin_days"]
        )

    def test_adding_a_lodging_key_does_not_break_a_nametag_app(self, event_profile):
        """The stated reason per-app validation exists."""
        event_profile.validate_for_app("nametag-press", require=["roles.options"])
        with pytest.raises(EventProfileError):
            event_profile.validate_for_app("lodging-planner", require=["lodging.rules"])


class TestPublicProjection:
    def test_discount_env_names_are_stripped(self, minimal_profile_dict):
        minimal_profile_dict["ticketing"] = {
            "slug": "excon-2030",
            "tiers": [
                {
                    "key": "ga",
                    "label": "GA",
                    "discount_code_env": "EVENTBRITE_DISCOUNT_GA",
                    "match": {"default": True},
                }
            ],
        }
        profile = EventProfile.model_validate(minimal_profile_dict)
        public = to_public_dict(profile)
        assert "discount_code_env" not in public["ticketing"]["tiers"][0]

    def test_notify_recipients_are_stripped(self, minimal_profile_dict):
        minimal_profile_dict["notify"] = {
            "transport": "log",
            "default_recipients": ["staff@example.edu"],
        }
        profile = EventProfile.model_validate(minimal_profile_dict)
        public = to_public_dict(profile)
        assert "default_recipients" not in public["notify"]

    def test_field_map_is_stripped(self, event_profile):
        public = to_public_dict(event_profile)
        assert "field_map" not in public["drupal"]
        assert public["drupal"]["join_key"] == "uuid"

    def test_no_staff_addresses_reach_the_public_payload(self, minimal_profile_dict):
        minimal_profile_dict["notify"] = {
            "transport": "smtp",
            "default_recipients": ["secret-staff-list@example.edu"],
            "from_email": "noreply@example.edu",
        }
        profile = EventProfile.model_validate(minimal_profile_dict)
        import json

        payload = json.dumps(to_public_dict(profile))
        assert "secret-staff-list" not in payload
        assert "noreply@example.edu" not in payload

    def test_contact_email_is_intentionally_retained(self, event_profile):
        # Already published on the event website; it is the address attendees are
        # told to write to.
        public = to_public_dict(event_profile)
        assert public["event"]["contact_email"] == "excon@example.edu"

    def test_public_payload_is_json_serialisable(self, event_profile):
        import json

        assert json.loads(json.dumps(to_public_dict(event_profile)))

    def test_trip_wire_every_top_level_section_is_accounted_for(self, event_profile):
        """Fails when a new profile section is added without a public decision.

        The original poster-gallery leak was one careless ``response_model`` reuse
        on an anonymous route. A default-open projection reproduces that class of
        bug the first time somebody adds a field, so the set of sections reaching
        the browser is pinned here and must be updated deliberately.
        """
        expected = {
            "schema_version",
            "event",
            "schedule",
            "branding",
            "drupal",
            "roles",
            "affiliation",
            "ticketing",
            "swag",
            "lodging",
            "nametags",
            "notify",
            "links",
        }
        assert set(to_public_dict(event_profile)) == expected

    def test_etag_is_stable_and_changes_with_content(self, event_profile, minimal_profile_dict):
        from eventkit.eventprofile import public_etag

        first = public_etag(event_profile)
        assert first == public_etag(event_profile)
        minimal_profile_dict["branding"]["site_name"] = "Something Else"
        other = EventProfile.model_validate(minimal_profile_dict)
        assert public_etag(other) != first
