"""The console script. Also the CI entry point for validating an event profile."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eventkit.cli import NOT_YET_BUILT, main

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "caarms-2026" / (
    "event-profile.yaml"
)


@pytest.fixture
def example_path() -> str:
    if not EXAMPLE.is_file():
        pytest.skip("examples/caarms-2026/event-profile.yaml not available")
    return str(EXAMPLE)


class TestProfileValidate:
    def test_valid_profile_exits_zero(self, example_path, capsys):
        assert main(["profile", "validate", example_path]) == 0
        assert "OK" in capsys.readouterr().out

    def test_missing_profile_exits_one_with_a_search_path(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("EVENT_PROFILE", raising=False)
        assert main(["profile", "validate"]) == 1
        assert "No event profile found" in capsys.readouterr().err

    def test_invalid_profile_exits_one_with_a_readable_report(self, tmp_path, capsys):
        bad = tmp_path / "event-profile.yaml"
        bad.write_text("event:\n  name: Incomplete\n", encoding="utf-8")
        assert main(["profile", "validate", str(bad)]) == 1
        err = capsys.readouterr().err
        assert "problem(s)" in err
        assert "EVENT-PROFILE-SPEC" in err

    def test_malformed_yaml_exits_one(self, tmp_path, capsys):
        bad = tmp_path / "event-profile.yaml"
        bad.write_text("event: [unclosed\n", encoding="utf-8")
        assert main(["profile", "validate", str(bad)]) == 1
        assert "not valid YAML" in capsys.readouterr().err

    def test_require_flag_enforces_per_app_keys(self, example_path, capsys):
        assert main(["profile", "validate", example_path, "--require", "swag.options"]) == 0
        assert (
            main(
                [
                    "profile",
                    "validate",
                    example_path,
                    "--app",
                    "nametag-press",
                    "--require",
                    "notify.template_dir",
                ]
            )
            == 1
        )
        assert "nametag-press" in capsys.readouterr().err


class TestProfilePublic:
    def test_emits_valid_json(self, example_path, capsys):
        assert main(["profile", "public", example_path]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["event"]["slug"] == "caarms-2026"

    def test_no_discount_env_names_reach_the_public_payload(self, example_path, capsys):
        main(["profile", "public", example_path])
        out = capsys.readouterr().out
        assert "discount_code_env" not in out
        assert "EVENTBRITE_DISCOUNT" not in out

    def test_no_field_map_reaches_the_public_payload(self, example_path, capsys):
        main(["profile", "public", example_path])
        assert "field_map" not in capsys.readouterr().out

    def test_the_reference_profile_leaks_nothing_shaped_like_a_discount_code(
        self, example_path, capsys
    ):
        """Guard on the shipped example itself, not just the projection.

        Shape-based rather than a list of the real codes. The codes appear in
        exactly one place in this repository — the deny-list in
        ``.github/workflows/ci.yml`` — because a grep has to name what it is
        looking for. Repeating them here would put them in a second public file
        for no additional protection.

        The shape is what the live codes share: uppercase alphanumeric, at least
        eight characters, containing at least one digit and one letter, with no
        underscores (which is what distinguishes a code from an env var name).
        """
        import re

        main(["profile", "public", example_path])
        out = capsys.readouterr().out

        suspicious = [
            token
            for token in re.findall(r"\b[A-Z0-9]{8,20}\b", out)
            if any(c.isdigit() for c in token) and any(c.isalpha() for c in token)
        ]
        assert suspicious == [], (
            f"the public payload contains token(s) shaped like a discount code: "
            f"{suspicious}. Tiers must name an environment variable, never a code."
        )


class TestCheckinKeys:
    def test_prints_the_legacy_mapping(self, example_path, capsys):
        assert main(["profile", "checkin-keys", example_path]) == 0
        out = capsys.readouterr().out
        assert "6/28" in out
        assert "2026-06-28" in out
        assert "banquet" in out


class TestFieldmapCheck:
    def test_resolves_the_reference_field_map(self, example_path, capsys):
        assert main(["fieldmap", "check", example_path, "--want", "email", "name"]) == 0
        out = capsys.readouterr().out
        assert "email" in out
        assert "registrant_name" in out

    def test_unresolvable_field_exits_one(self, example_path, capsys):
        assert main(["fieldmap", "check", example_path, "--want", "no_such_field"]) == 1
        assert "no_such_field" in capsys.readouterr().err


class TestDbCommands:
    """CLI wiring only — `eventkit.db.migrate` has its own exhaustive tests."""

    @pytest.fixture
    def app_package(self, tmp_path, monkeypatch):
        package_dir = tmp_path / "cliapp"
        package_dir.mkdir()
        (package_dir / "__init__.py").write_text("target_metadata = None\n")
        monkeypatch.syspath_prepend(str(tmp_path))
        return "cliapp"

    def _write_revision(self, migrations_dir, *, broken=False):
        versions = migrations_dir / "versions"
        if broken:
            versions.joinpath("rev1_broken.py").write_text(
                "revision = 'rev1'\n"
                "down_revision = None\n"
                "branch_labels = None\n"
                "depends_on = None\n\n"
                "def upgrade() -> None:\n"
                "    raise RuntimeError('boom')\n\n"
                "def downgrade() -> None:\n"
                "    pass\n"
            )
        else:
            versions.joinpath("rev1_create_widgets.py").write_text(
                "revision = 'rev1'\n"
                "down_revision = None\n"
                "branch_labels = None\n"
                "depends_on = None\n"
                "from alembic import op\n"
                "import sqlalchemy as sa\n\n"
                "def upgrade() -> None:\n"
                "    op.create_table('widgets', sa.Column('id', sa.Integer, primary_key=True))\n\n"
                "def downgrade() -> None:\n"
                "    op.drop_table('widgets')\n"
            )

    def test_init_scaffolds_migrations(self, tmp_path, app_package, capsys):
        assert main(["db", "init", "--app-dir", str(tmp_path), "--package", app_package]) == 0
        assert (tmp_path / "migrations" / "env.py").exists()
        assert "OK" in capsys.readouterr().out

    def test_init_twice_fails_without_overwriting(self, tmp_path, app_package, capsys):
        main(["db", "init", "--app-dir", str(tmp_path), "--package", app_package])
        assert main(["db", "init", "--app-dir", str(tmp_path), "--package", app_package]) == 1
        assert "already exists" in capsys.readouterr().err

    def test_current_on_a_fresh_database_prints_base(self, tmp_path, capsys):
        assert main(["db", "current", "--url", f"sqlite:///{tmp_path / 'cli.db'}"]) == 0
        assert capsys.readouterr().out.strip() == "(base)"

    def test_upgrade_then_current_round_trip(self, tmp_path, app_package, capsys):
        main(["db", "init", "--app-dir", str(tmp_path), "--package", app_package])
        migrations_dir = tmp_path / "migrations"
        self._write_revision(migrations_dir)
        db_url = f"sqlite:///{tmp_path / 'cli.db'}"
        upgrade_args = ["db", "upgrade", "--url", db_url, "--migrations-dir", str(migrations_dir)]

        assert main(upgrade_args) == 0
        assert "rev1" in capsys.readouterr().out
        assert main(["db", "current", "--url", db_url]) == 0
        assert capsys.readouterr().out.strip() == "rev1"

    def test_upgrade_failure_exits_one_with_the_error(self, tmp_path, app_package, capsys):
        main(["db", "init", "--app-dir", str(tmp_path), "--package", app_package])
        migrations_dir = tmp_path / "migrations"
        self._write_revision(migrations_dir, broken=True)
        db_url = f"sqlite:///{tmp_path / 'cli.db'}"
        upgrade_args = ["db", "upgrade", "--url", db_url, "--migrations-dir", str(migrations_dir)]

        assert main(upgrade_args) == 1
        assert "must not start" in capsys.readouterr().err

    def test_stamp_sets_current_without_running_migrations(self, tmp_path, app_package, capsys):
        main(["db", "init", "--app-dir", str(tmp_path), "--package", app_package])
        migrations_dir = tmp_path / "migrations"
        self._write_revision(migrations_dir)
        db_url = f"sqlite:///{tmp_path / 'cli.db'}"
        stamp_args = [
            "db", "stamp", "--url", db_url, "--migrations-dir", str(migrations_dir), "rev1"
        ]

        assert main(stamp_args) == 0
        capsys.readouterr()
        assert main(["db", "current", "--url", db_url]) == 0
        assert capsys.readouterr().out.strip() == "rev1"


class TestUnbuiltVerbs:
    @pytest.mark.parametrize("verb", sorted(NOT_YET_BUILT))
    def test_unbuilt_verb_is_honest_and_exits_two(self, verb, capsys):
        assert main([verb]) == 2
        err = capsys.readouterr().err
        assert "not built yet" in err
        assert "Not yet built" in err

    def test_no_verb_half_provisions_anything(self):
        # `eventkit azure deploy` must not silently do part of a deployment.
        assert main(["azure", "deploy"]) == 2


class TestVersion:
    def test_version_flag(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            main(["--version"])
        assert excinfo.value.code == 0
        assert "eventkit" in capsys.readouterr().out
