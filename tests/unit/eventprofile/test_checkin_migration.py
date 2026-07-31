"""The ``"6/28"`` -> ISO check-in key migration.

This is the migration the hand-rolled migrator in ``ticketed/backend/database.py``
cannot express: it only appends columns, and this is a data rewrite inside a JSON
blob.
"""

from __future__ import annotations

import json

import pytest

from eventkit.eventprofile import (
    CHECKIN_STATES,
    CheckinKeyError,
    canonical_state,
    legacy_key_aliases,
    migrate_checkin_blob,
)


@pytest.fixture
def aliases(event_profile):
    return legacy_key_aliases(event_profile.schedule)


class TestLegacyKeyAliases:
    def test_month_slash_day_maps_to_iso(self, aliases):
        assert aliases["6/1"] == "2030-06-01"
        assert aliases["6/2"] == "2030-06-02"

    def test_zero_padded_variant_also_maps(self, aliases):
        """Both ``"7/1"`` and ``"07/01"`` appear in the live data."""
        assert aliases["06/01"] == "2030-06-01"
        assert aliases["06/03"] == "2030-06-03"

    def test_event_kind_maps_from_its_label(self, aliases):
        assert aliases["banquet"] == "2030-06-02-banquet"

    def test_canonical_keys_map_to_themselves_for_idempotency(self, aliases):
        assert aliases["2030-06-01"] == "2030-06-01"
        assert aliases["2030-06-02-banquet"] == "2030-06-02-banquet"

    def test_event_day_does_not_claim_the_bare_date_key(self, aliases):
        # The banquet falls on 2030-06-02, but "6/2" must mean the day, not the
        # banquet — they are separate check-ins.
        assert aliases["6/2"] == "2030-06-02"


class TestMigrateCheckinBlob:
    def test_rewrites_legacy_keys(self, aliases):
        result = migrate_checkin_blob(json.dumps({"6/1": 1, "banquet": 3}), aliases)
        assert json.loads(result) == {"2030-06-01": 1, "2030-06-02-banquet": 3}

    def test_handles_both_padded_and_unpadded_in_one_blob(self, aliases):
        result = migrate_checkin_blob(json.dumps({"6/1": 1, "06/03": 2}), aliases)
        assert json.loads(result) == {"2030-06-01": 1, "2030-06-03": 2}

    def test_is_idempotent(self, aliases):
        once = migrate_checkin_blob(json.dumps({"6/1": 1}), aliases)
        twice = migrate_checkin_blob(once, aliases)
        assert json.loads(once) == json.loads(twice)

    def test_accepts_an_already_decoded_mapping(self, aliases):
        # The column is declared JSON but was written as a string in places, so
        # both shapes exist in the live database.
        result = migrate_checkin_blob({"6/1": 1}, aliases)
        assert json.loads(result) == {"2030-06-01": 1}

    @pytest.mark.parametrize("empty", [None, "", "   ", "{}", {}])
    def test_empty_input_yields_none(self, aliases, empty):
        assert migrate_checkin_blob(empty, aliases) is None

    def test_unknown_key_raises_rather_than_dropping_attendance(self, aliases):
        with pytest.raises(CheckinKeyError) as excinfo:
            migrate_checkin_blob(json.dumps({"12/25": 1}), aliases)
        assert "12/25" in str(excinfo.value)

    def test_unknown_key_can_be_dropped_deliberately(self, aliases):
        result = migrate_checkin_blob(
            json.dumps({"6/1": 1, "12/25": 1}), aliases, strict=False
        )
        assert json.loads(result) == {"2030-06-01": 1}

    def test_malformed_json_raises(self, aliases):
        with pytest.raises(CheckinKeyError):
            migrate_checkin_blob("{not json", aliases)

    def test_non_object_json_raises(self, aliases):
        with pytest.raises(CheckinKeyError):
            migrate_checkin_blob("[1, 2, 3]", aliases)

    def test_colliding_legacy_keys_keep_the_definite_record(self, aliases):
        # "6/1" and "06/01" both mean 2030-06-01. If one says UNRECORDED and the
        # other says CHECKED_IN, the person checked in.
        result = migrate_checkin_blob(json.dumps({"6/1": 0, "06/01": 1}), aliases)
        assert json.loads(result) == {"2030-06-01": 1}

    def test_output_is_key_sorted_for_stable_diffs(self, aliases):
        result = migrate_checkin_blob(json.dumps({"6/3": 1, "6/1": 1}), aliases)
        assert result is not None
        assert result.index("2030-06-01") < result.index("2030-06-03")


class TestCanonicalState:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (0, 0),
            (1, 1),
            (2, 2),
            (3, 3),
            ("1", 1),
            ("CHECKED_IN", 1),
            ("absent", 3),
            (True, 1),
            (False, 0),
            (99, 0),
            ("nonsense", 0),
            (None, 0),
        ],
    )
    def test_table(self, raw, expected):
        assert canonical_state(raw) == expected

    def test_state_numbering_is_frozen(self):
        # These integers are already in the live database; renumbering them would
        # silently reclassify every recorded check-in.
        assert CHECKIN_STATES == {
            "UNRECORDED": 0,
            "CHECKED_IN": 1,
            "UNSURE": 2,
            "ABSENT": 3,
        }


class TestAgainstTheCaarmsProfile:
    def test_real_legacy_keys_map(self, caarms_profile):
        aliases = legacy_key_aliases(caarms_profile.schedule)
        assert aliases["6/28"] == "2026-06-28"
        assert aliases["6/29"] == "2026-06-29"
        assert aliases["6/30"] == "2026-06-30"
        assert aliases["7/1"] == "2026-07-01"
        assert aliases["07/01"] == "2026-07-01"
        assert aliases["banquet"] == "2026-06-30-banquet"

    def test_a_real_blob_migrates(self, caarms_profile):
        aliases = legacy_key_aliases(caarms_profile.schedule)
        legacy = {"6/28": 1, "6/29": 1, "banquet": 1, "7/1": 3}
        result = migrate_checkin_blob(json.dumps(legacy), aliases)
        assert json.loads(result) == {
            "2026-06-28": 1,
            "2026-06-29": 1,
            "2026-06-30-banquet": 1,
            "2026-07-01": 3,
        }
