"""eventkit.ui: the packaging contract (static_path/theme_ids/theme_path,
render_theme_vars's OKLCH ramp, vendor's SRI copy, and the tokens.json <->
tokens.css cross-check that guards against the kind of doc/code drift already
seen once in this repo between README and CHANGELOG)."""

from __future__ import annotations

import json
import re

import pytest

from eventkit.ui import (
    AssetsMissingError,
    ThemeNotFoundError,
    assert_assets_present,
    render_theme_vars,
    static_path,
    theme_ids,
    theme_path,
    vendor,
)

_HEX = re.compile(r"^#[0-9a-f]{6}$")
_SRI = re.compile(r"^sha384-[A-Za-z0-9+/]{64}={0,2}$")


def test_static_path_is_a_real_installed_directory():
    path = static_path()
    assert path.is_dir()
    assert (path / "tokens" / "tokens.css").is_file()


def test_theme_ids_lists_both_shipped_themes():
    assert theme_ids() == ["neutral", "princeton-orfe"]


def test_theme_path_returns_the_directory():
    path = theme_path("princeton-orfe")
    assert (path / "theme.css").is_file()


def test_theme_path_rejects_unknown_theme():
    with pytest.raises(ThemeNotFoundError) as excinfo:
        theme_path("does-not-exist")
    assert excinfo.value.available == ["neutral", "princeton-orfe"]


def test_assert_assets_present_accepts_the_shipped_kit():
    assert_assets_present(static_path())  # must not raise


def test_assert_assets_present_lists_every_missing_file(tmp_path):
    (tmp_path / "tokens").mkdir()
    (tmp_path / "tokens" / "tokens.css").write_text(":root {}")

    with pytest.raises(AssetsMissingError) as excinfo:
        assert_assets_present(tmp_path)

    assert "tokens/tokens.css" not in excinfo.value.missing
    assert "tokens/tokens.json" in excinfo.value.missing
    assert "js/main.js" in excinfo.value.missing
    assert excinfo.value.directory == tmp_path


class TestRenderThemeVars:
    def test_default_brand_color_matches_the_princeton_orfe_theme(self, event_profile):
        css = render_theme_vars(event_profile)
        theme_css = (theme_path("princeton-orfe") / "theme.css").read_text()

        rendered = dict(re.findall(r"--(color-brand-\d+):\s*(#[0-9a-f]{6});", css))
        shipped = dict(re.findall(r"--(color-brand-\d+):\s*(#[0-9a-f]{6});", theme_css))
        assert rendered == shipped

    def test_output_is_a_root_block_with_four_brand_steps(self, event_profile):
        css = render_theme_vars(event_profile)
        assert css.startswith(":root {")
        for step in ("10", "70", "80", "90"):
            match = re.search(rf"--color-brand-{step}:\s*(#[0-9a-f]{{6}});", css)
            assert match, f"missing --color-brand-{step} in:\n{css}"
            assert _HEX.match(match.group(1))

    def test_seventy_step_is_the_input_hex_verbatim(self, event_profile):
        event_profile.branding.brand_color = "#336699"
        css = render_theme_vars(event_profile)
        assert "--color-brand-70: #336699;" in css

    def test_ramp_gets_darker_from_ten_to_ninety(self, event_profile):
        event_profile.branding.brand_color = "#2f8f4e"
        css = render_theme_vars(event_profile)
        steps = dict(re.findall(r"--color-brand-(\d+):\s*(#[0-9a-f]{6});", css))

        def luminance(hex_color: str) -> float:
            r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
            return 0.2126 * r + 0.7152 * g + 0.0722 * b

        assert luminance(steps["10"]) > luminance(steps["70"])
        assert luminance(steps["70"]) > luminance(steps["80"])
        assert luminance(steps["80"]) > luminance(steps["90"])

    def test_brand_color_dark_overrides_the_eighty_step_exactly(self, event_profile):
        event_profile.branding.brand_color = "#2f8f4e"
        event_profile.branding.brand_color_dark = "#123456"
        css = render_theme_vars(event_profile)
        assert "--color-brand-80: #123456;" in css
        ninety = re.search(r"--color-brand-90:\s*(#[0-9a-f]{6});", css).group(1)
        assert ninety != "#123456"


class TestVendor:
    def test_copies_shared_assets_and_the_chosen_theme(self, tmp_path):
        manifest = vendor(tmp_path, theme="neutral")

        assert manifest.theme == "neutral"
        assert manifest.hashed is False
        assert (tmp_path / "tokens" / "tokens.css").is_file()
        assert (tmp_path / "vendor" / "mathjax-3-tex-mml-chtml.js").is_file()
        assert (tmp_path / "theme" / "theme.css").is_file()
        # princeton-orfe-only assets must not leak into a neutral vendor.
        assert not (tmp_path / "theme" / "assets").exists()

    def test_manifest_entries_have_matching_sri_and_sha256(self, tmp_path):
        manifest = vendor(tmp_path, theme="princeton-orfe")

        assert manifest.entries, "vendor() copied nothing"
        for entry in manifest.entries:
            dest_file = tmp_path / entry.dest
            assert dest_file.is_file()
            assert dest_file.stat().st_size == entry.bytes
            assert _SRI.match(entry.sri), entry.sri
            assert len(entry.sha256) == 64

    def test_hashed_true_renames_files_with_a_content_hash(self, tmp_path):
        manifest = vendor(tmp_path, theme="neutral", hashed=True)
        main_entry = next(e for e in manifest.entries if e.source.endswith("js/main.js"))

        assert main_entry.dest != "js/main.js"
        assert main_entry.dest.startswith("js/main.")
        assert main_entry.dest.endswith(".js")
        assert (tmp_path / main_entry.dest).read_bytes() == (
            static_path() / "js" / "main.js"
        ).read_bytes()

    def test_unknown_theme_raises_before_copying_anything(self, tmp_path):
        with pytest.raises(ThemeNotFoundError):
            vendor(tmp_path, theme="does-not-exist")
        assert list(tmp_path.iterdir()) == []


def _resolve_token(value, root):
    """Resolve W3C-style ``{a.b.c}`` references inside a token's ``$value``,
    including references embedded in a larger string like a shorthand."""
    if not isinstance(value, str):
        return value

    def _lookup(match: re.Match) -> str:
        node = root
        for part in match.group(1).split("."):
            node = node[part]
        resolved = node["$value"] if isinstance(node, dict) else node
        return str(_resolve_token(resolved, root))

    return re.sub(r"\{([a-zA-Z0-9_.]+)\}", _lookup, value)


def _token_value(path: str, root: dict) -> object:
    node = root
    for part in path.split("."):
        node = node[part]
    return _resolve_token(node["$value"], root)


class TestTokensJsonMatchesTokensCss:
    """tokens.json is the declared source of truth; tokens.css is what ships
    to the browser. README and CHANGELOG have already drifted from each other
    once in this repo (see mem-1786022053-0a17) — this guards the same failure
    mode here with an executable check instead of a comment."""

    @pytest.fixture
    def tokens_json(self):
        raw = (static_path() / "tokens" / "tokens.json").read_text()
        return json.loads(raw)

    @pytest.fixture
    def css_vars(self):
        text = (static_path() / "tokens" / "tokens.css").read_text()
        return dict(re.findall(r"--([a-zA-Z0-9-]+):\s*([^;]+);", text))

    @pytest.mark.parametrize(
        ("token_path", "css_name"),
        [
            ("color.white", "color-white"),
            ("color.neutral.5", "color-neutral-5"),
            ("color.neutral.100", "color-neutral-100"),
            ("color.brand.10", "color-brand-10"),
            ("color.brand.70", "color-brand-70"),
            ("color.brand.80", "color-brand-80"),
            ("color.brand.90", "color-brand-90"),
            ("color.focus-outline", "color-focus-outline"),
            ("font.size.xs", "font-size-xs"),
            ("font.size.h1", "font-size-h1"),
            ("font.weight.bold", "font-weight-bold"),
            ("radius.pill", "radius-pill"),
            ("space.1", "space-1"),
            ("space.16", "space-16"),
            ("container.max", "container-max"),
        ],
    )
    def test_value_matches(self, tokens_json, css_vars, token_path, css_name):
        assert css_vars[css_name] == str(_token_value(token_path, tokens_json))

    def test_action_aliases_resolve_to_the_same_color_as_brand_in_json(self, tokens_json):
        assert _token_value("color.action.70", tokens_json) == _token_value(
            "color.brand.70", tokens_json
        )

    def test_action_aliases_are_live_var_references_in_css(self, css_vars):
        assert css_vars["color-action-70"] == "var(--color-brand-70)"
        assert css_vars["color-action-80"] == "var(--color-brand-80)"

    def test_border_footer_accent_embeds_the_brand_color(self, tokens_json, css_vars):
        resolved = _token_value("border.footer-accent", tokens_json)
        assert resolved == "4px solid #6b6b6b"
        assert css_vars["border-footer-accent"] == "4px solid var(--color-brand-70)"


def test_no_css_var_used_by_shipped_components_is_left_undefined():
    """Every custom property a shipped component stylesheet reads via var()
    must be defined somewhere in tokens/ — an undefined custom property has no
    fallback and silently renders as unset, unlike a typo'd class name."""
    defined: set[str] = set()
    for tokens_file in ("tokens.css", "fonts.css"):
        defined |= set(
            re.findall(r"--([a-zA-Z0-9-]+):", (static_path() / "tokens" / tokens_file).read_text())
        )
    components_dir = static_path() / "components"
    used: set[str] = set()
    for css_file in components_dir.glob("*.css"):
        used |= set(re.findall(r"var\(--([a-zA-Z0-9-]+)", css_file.read_text()))
    # --btn-border-radius is declared and consumed entirely within buttons.css
    # itself (a component-local custom property, not a design token).
    used.discard("btn-border-radius")
    assert used <= defined, used - defined
