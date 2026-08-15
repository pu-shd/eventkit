"""Tests for eventkit.mirror: MirrorAsset's exactly-one-locator validator,
bypass_header_from_env's no-default posture, mirror()'s url_path and
discover=link-css/img-src fetch paths (via respx against a sync httpx.Client),
its never-raises-for-one-bad-asset posture (network error, non-200, wrong
content-type, oversized response, a discovery miss), the manifest-driven
skip/force behaviour across repeated calls, and the `eventkit mirror run`
CLI wiring."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
import yaml

from eventkit.cli import main
from eventkit.mirror import (
    BYPASS_HEADER_ENV,
    BYPASS_VALUE_ENV,
    MirrorAsset,
    MirrorSpec,
    bypass_header_from_env,
    mirror,
)

HOST = "https://assets.example.edu"


def make_spec(**overrides) -> MirrorSpec:
    defaults = {
        "target_host": HOST,
        "assets": [MirrorAsset(name="logo", url_path="/static/logo.png")],
    }
    defaults.update(overrides)
    return MirrorSpec(**defaults)


class TestBypassHeaderFromEnv:
    def test_absent_when_neither_set(self):
        assert bypass_header_from_env({}) is None

    def test_absent_when_only_header_name_set(self):
        assert bypass_header_from_env({BYPASS_HEADER_ENV: "X-Bypass"}) is None

    def test_absent_when_only_value_set(self):
        assert bypass_header_from_env({BYPASS_VALUE_ENV: "secret"}) is None

    def test_absent_when_value_is_empty_string(self):
        env = {BYPASS_HEADER_ENV: "X-Bypass", BYPASS_VALUE_ENV: ""}
        assert bypass_header_from_env(env) is None

    def test_present_when_both_set(self):
        env = {BYPASS_HEADER_ENV: "X-Bypass", BYPASS_VALUE_ENV: "secret"}
        assert bypass_header_from_env(env) == ("X-Bypass", "secret")

    def test_reads_real_os_environ_by_default(self, monkeypatch):
        monkeypatch.setenv(BYPASS_HEADER_ENV, "X-Bypass")
        monkeypatch.setenv(BYPASS_VALUE_ENV, "secret")
        assert bypass_header_from_env() == ("X-Bypass", "secret")


class TestMirrorAssetValidation:
    def test_neither_locator_is_rejected(self):
        with pytest.raises(ValueError, match="exactly one"):
            MirrorAsset(name="logo")

    def test_both_locators_is_rejected(self):
        with pytest.raises(ValueError, match="exactly one"):
            MirrorAsset(name="logo", url_path="/x.png", discover="img-src")

    def test_url_path_only_is_accepted(self):
        MirrorAsset(name="logo", url_path="/x.png")

    def test_discover_only_is_accepted(self):
        MirrorAsset(name="logo", discover="img-src")


class TestMirrorUrlPathFetch:
    @respx.mock
    def test_fetches_and_records_manifest(self, tmp_path):
        respx.get(f"{HOST}/static/logo.png").mock(
            return_value=httpx.Response(
                200, content=b"PNGDATA", headers={"content-type": "image/png"}
            )
        )
        report = mirror(make_spec(), tmp_path)

        assert report.fetched == ["logo"]
        assert report.errors == []
        assert (tmp_path / "logo.png").read_bytes() == b"PNGDATA"

        manifest = json.loads((tmp_path / "manifest.json").read_text())
        entry = manifest["entries"]["logo"]
        assert entry["dest"] == "logo.png"
        assert entry["bytes"] == len(b"PNGDATA")
        assert entry["content_type"] == "image/png"
        assert entry["sha256"]

    @respx.mock
    def test_no_leftover_tmp_files(self, tmp_path):
        respx.get(f"{HOST}/static/logo.png").mock(return_value=httpx.Response(200, content=b"x"))
        mirror(make_spec(), tmp_path)
        assert sorted(p.name for p in tmp_path.iterdir()) == ["logo.png", "manifest.json"]

    @respx.mock
    def test_sends_bypass_header_and_user_agent(self, tmp_path):
        route = respx.get(f"{HOST}/static/logo.png").mock(
            return_value=httpx.Response(200, content=b"x")
        )
        spec = make_spec(bypass_header=("X-Bypass", "secret"), user_agent="custom-ua/1.0")
        mirror(spec, tmp_path)

        request = route.calls.last.request
        assert request.headers["X-Bypass"] == "secret"
        assert request.headers["User-Agent"] == "custom-ua/1.0"

    @respx.mock
    def test_second_call_skips_already_present_asset(self, tmp_path):
        route = respx.get(f"{HOST}/static/logo.png").mock(
            return_value=httpx.Response(200, content=b"x")
        )
        mirror(make_spec(), tmp_path)
        assert route.call_count == 1

        report = mirror(make_spec(), tmp_path)
        assert report.skipped == ["logo"]
        assert report.fetched == []
        assert route.call_count == 1  # no second HTTP call

    @respx.mock
    def test_force_refetches_even_when_present(self, tmp_path):
        route = respx.get(f"{HOST}/static/logo.png").mock(
            return_value=httpx.Response(200, content=b"x")
        )
        mirror(make_spec(), tmp_path)
        report = mirror(make_spec(), tmp_path, force=True)

        assert report.fetched == ["logo"]
        assert route.call_count == 2

    @respx.mock
    def test_refetches_when_manifest_entry_exists_but_file_was_deleted(self, tmp_path):
        respx.get(f"{HOST}/static/logo.png").mock(return_value=httpx.Response(200, content=b"x"))
        mirror(make_spec(), tmp_path)
        (tmp_path / "logo.png").unlink()

        report = mirror(make_spec(), tmp_path)
        assert report.fetched == ["logo"]

    @respx.mock
    def test_corrupt_manifest_is_treated_as_empty(self, tmp_path):
        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / "manifest.json").write_text("not json{", encoding="utf-8")
        respx.get(f"{HOST}/static/logo.png").mock(return_value=httpx.Response(200, content=b"x"))

        report = mirror(make_spec(), tmp_path)

        assert report.fetched == ["logo"]
        assert report.errors == []


class TestAtomicWrite:
    def test_write_failure_cleans_up_the_temp_file_and_reraises(self, tmp_path, monkeypatch):
        from eventkit.mirror import _atomic_write

        def boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr("os.replace", boom)

        with pytest.raises(OSError, match="disk full"):
            _atomic_write(tmp_path / "logo.png", b"data")

        assert list(tmp_path.iterdir()) == []


class TestMirrorNeverRaisesForOneBadAsset:
    @respx.mock
    def test_non_200_becomes_an_error_not_a_raise(self, tmp_path):
        respx.get(f"{HOST}/static/logo.png").mock(return_value=httpx.Response(404))
        report = mirror(make_spec(), tmp_path)

        assert report.fetched == []
        assert len(report.errors) == 1
        assert report.errors[0][0] == "logo"
        assert "404" in report.errors[0][1]
        assert report.exit_code() == 1

    @respx.mock
    def test_network_error_becomes_an_error_not_a_raise(self, tmp_path):
        respx.get(f"{HOST}/static/logo.png").mock(side_effect=httpx.ConnectError("refused"))
        report = mirror(make_spec(), tmp_path)

        assert report.fetched == []
        assert report.errors[0][0] == "logo"
        assert report.exit_code() == 1

    @respx.mock
    def test_wrong_content_type_becomes_an_error(self, tmp_path):
        respx.get(f"{HOST}/static/logo.png").mock(
            return_value=httpx.Response(200, content=b"x", headers={"content-type": "text/html"})
        )
        asset = MirrorAsset(
            name="logo", url_path="/static/logo.png", expect_content_type="image/png"
        )
        report = mirror(make_spec(assets=[asset]), tmp_path)

        assert report.fetched == []
        assert "content-type" in report.errors[0][1]

    @respx.mock
    def test_oversized_response_becomes_an_error(self, tmp_path):
        respx.get(f"{HOST}/static/logo.png").mock(
            return_value=httpx.Response(200, content=b"x" * 100)
        )
        asset = MirrorAsset(name="logo", url_path="/static/logo.png", max_bytes=10)
        report = mirror(make_spec(assets=[asset]), tmp_path)

        assert report.fetched == []
        assert "max_bytes" in report.errors[0][1]
        assert not (tmp_path / "logo.png").exists()

    @respx.mock
    def test_one_bad_asset_does_not_block_the_rest(self, tmp_path):
        respx.get(f"{HOST}/static/bad.png").mock(return_value=httpx.Response(500))
        respx.get(f"{HOST}/static/good.png").mock(return_value=httpx.Response(200, content=b"ok"))
        spec = make_spec(
            assets=[
                MirrorAsset(name="bad", url_path="/static/bad.png"),
                MirrorAsset(name="good", url_path="/static/good.png"),
            ]
        )
        report = mirror(spec, tmp_path)

        assert report.fetched == ["good"]
        assert report.errors[0][0] == "bad"


class TestMirrorDiscover:
    @respx.mock
    def test_link_css_discover_matches_by_filename_prefix(self, tmp_path):
        page_html = (
            "<html><head>"
            '<link rel="stylesheet" href="/css/other-9f8.css">'
            '<link rel="stylesheet" href="/css/align_header_text-bed7c47f.css">'
            "</head></html>"
        )
        respx.get(f"{HOST}/page").mock(return_value=httpx.Response(200, text=page_html))
        respx.get(f"{HOST}/css/align_header_text-bed7c47f.css").mock(
            return_value=httpx.Response(200, content=b"body{}")
        )
        asset = MirrorAsset(name="align_header_text", discover="link-css")
        report = mirror(make_spec(assets=[asset], discover_from=["/page"]), tmp_path)

        assert report.fetched == ["align_header_text"]
        assert (tmp_path / "align_header_text-bed7c47f.css").is_file()

    @respx.mock
    def test_img_src_discover(self, tmp_path):
        page_html = '<html><body><img src="/img/banner-abc123.png"></body></html>'
        respx.get(f"{HOST}/page").mock(return_value=httpx.Response(200, text=page_html))
        respx.get(f"{HOST}/img/banner-abc123.png").mock(
            return_value=httpx.Response(200, content=b"png")
        )
        asset = MirrorAsset(name="banner", discover="img-src")
        report = mirror(make_spec(assets=[asset], discover_from=["/page"]), tmp_path)

        assert report.fetched == ["banner"]

    @respx.mock
    def test_discover_miss_becomes_an_error(self, tmp_path):
        respx.get(f"{HOST}/page").mock(return_value=httpx.Response(200, text="<html></html>"))
        asset = MirrorAsset(name="missing", discover="img-src")
        report = mirror(make_spec(assets=[asset], discover_from=["/page"]), tmp_path)

        assert report.fetched == []
        assert "could not discover" in report.errors[0][1]

    @respx.mock
    def test_discovery_page_fetch_error_becomes_an_asset_error(self, tmp_path):
        respx.get(f"{HOST}/page").mock(side_effect=httpx.ConnectError("refused"))
        asset = MirrorAsset(name="missing", discover="img-src")
        report = mirror(make_spec(assets=[asset], discover_from=["/page"]), tmp_path)

        assert report.fetched == []
        assert "could not fetch discovery page" in report.errors[0][1]

    @respx.mock
    def test_discovery_page_is_only_fetched_once_for_multiple_assets(self, tmp_path):
        page_html = (
            '<html><body><img src="/img/one-aaa.png"><img src="/img/two-bbb.png"></body></html>'
        )
        page_route = respx.get(f"{HOST}/page").mock(
            return_value=httpx.Response(200, text=page_html)
        )
        respx.get(f"{HOST}/img/one-aaa.png").mock(return_value=httpx.Response(200, content=b"1"))
        respx.get(f"{HOST}/img/two-bbb.png").mock(return_value=httpx.Response(200, content=b"2"))
        spec = make_spec(
            assets=[
                MirrorAsset(name="one", discover="img-src"),
                MirrorAsset(name="two", discover="img-src"),
            ],
            discover_from=["/page"],
        )
        report = mirror(spec, tmp_path)

        assert sorted(report.fetched) == ["one", "two"]
        assert page_route.call_count == 1


class TestMirrorReport:
    def test_render_lists_fetched_skipped_and_errors(self):
        from eventkit.mirror import MirrorReport

        report = MirrorReport(fetched=["a"], skipped=["b"], errors=[("c", "boom")])
        text = report.render()
        assert "1 fetched" in text
        assert "1 already present" in text
        assert "1 failed" in text
        assert "fetched: a" in text
        assert "already present: b" in text
        assert "[c] boom" in text

    def test_exit_code_zero_when_clean(self):
        from eventkit.mirror import MirrorReport

        assert MirrorReport(fetched=["a"]).exit_code() == 0

    def test_exit_code_one_when_errors(self):
        from eventkit.mirror import MirrorReport

        assert MirrorReport(errors=[("a", "boom")]).exit_code() == 1


class TestMirrorCli:
    @respx.mock
    def test_mirror_run_fetches_into_dest(self, tmp_path, capsys):
        respx.get(f"{HOST}/static/logo.png").mock(return_value=httpx.Response(200, content=b"x"))
        spec_path = tmp_path / "spec.yaml"
        spec_path.write_text(
            yaml.safe_dump(
                {
                    "target_host": HOST,
                    "assets": [{"name": "logo", "url_path": "/static/logo.png"}],
                }
            ),
            encoding="utf-8",
        )
        dest = tmp_path / "dest"

        exit_code = main(["mirror", "run", "--spec", str(spec_path), "--dest", str(dest)])

        assert exit_code == 0
        assert (dest / "logo.png").is_file()
        assert "1 fetched" in capsys.readouterr().out

    @respx.mock
    def test_mirror_run_quiet_suppresses_report_but_keeps_exit_code(self, tmp_path, capsys):
        respx.get(f"{HOST}/static/logo.png").mock(return_value=httpx.Response(404))
        spec_path = tmp_path / "spec.yaml"
        spec_path.write_text(
            yaml.safe_dump(
                {
                    "target_host": HOST,
                    "assets": [{"name": "logo", "url_path": "/static/logo.png"}],
                }
            ),
            encoding="utf-8",
        )
        dest = tmp_path / "dest"

        exit_code = main(
            ["mirror", "run", "--spec", str(spec_path), "--dest", str(dest), "--quiet"]
        )

        assert exit_code == 1
        assert capsys.readouterr().out == ""

    @respx.mock
    def test_mirror_run_picks_up_bypass_header_from_env(self, tmp_path, monkeypatch):
        route = respx.get(f"{HOST}/static/logo.png").mock(
            return_value=httpx.Response(200, content=b"x")
        )
        monkeypatch.setenv(BYPASS_HEADER_ENV, "X-Bypass")
        monkeypatch.setenv(BYPASS_VALUE_ENV, "secret")
        spec_path = tmp_path / "spec.yaml"
        spec_path.write_text(
            yaml.safe_dump(
                {
                    "target_host": HOST,
                    "assets": [{"name": "logo", "url_path": "/static/logo.png"}],
                }
            ),
            encoding="utf-8",
        )

        assert (
            main(["mirror", "run", "--spec", str(spec_path), "--dest", str(tmp_path / "dest")]) == 0
        )
        assert route.calls.last.request.headers["X-Bypass"] == "secret"

    @respx.mock
    def test_mirror_run_force_flag_is_wired(self, tmp_path):
        route = respx.get(f"{HOST}/static/logo.png").mock(
            return_value=httpx.Response(200, content=b"x")
        )
        spec_path = tmp_path / "spec.yaml"
        spec_path.write_text(
            yaml.safe_dump(
                {
                    "target_host": HOST,
                    "assets": [{"name": "logo", "url_path": "/static/logo.png"}],
                }
            ),
            encoding="utf-8",
        )
        dest = tmp_path / "dest"

        main(["mirror", "run", "--spec", str(spec_path), "--dest", str(dest), "--quiet"])
        main(["mirror", "run", "--spec", str(spec_path), "--dest", str(dest), "--force", "--quiet"])

        assert route.call_count == 2

    def test_mirror_is_no_longer_in_not_yet_built(self):
        from eventkit.cli import NOT_YET_BUILT

        assert "mirror" not in NOT_YET_BUILT
