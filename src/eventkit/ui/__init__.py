"""No-bundler static UI kit: tokens, themes, vendored third-party JS, SRI.

~2-3k lines of shared JS/CSS for authenticated tools used by a few dozen staff
does not justify five repos each carrying a bundler, `package.json` and
`node_modules` in their Docker build. This module is the packaging contract
around a plain static directory (:func:`static_path`) instead: no build step,
so a CSS-only fix is a patch release with no Python review.

The cascade (each layer only overrides what it needs to):

    tokens/tokens.css              :root { --color-brand-*: <grayscale> }
    themes/<id>/theme.css          :root { --color-brand-*: <real color> } + theme-only rules
    <generated>/theme.css          :root { --color-brand-*: <per-event ramp> }  (render_theme_vars)
    <adopter>/theme.override.css   last word, mounted from outside the image, never shipped

:func:`render_theme_vars` derives the four ``--color-brand-*`` steps from a
single hex (``EventProfile.branding.brand_color``) via OKLCH lighten/darken,
so an adopter sets one color and gets a full ramp — resolving the
``#e77500``-vs-``#f58025`` orange conflict in the predecessor apps in favour of
Princeton's official color, which is also this module's own default.

:func:`vendor` copies a theme's assets (tokens, shared CSS/JS, the vendored
third-party libraries, and that theme's own files) into an app's static
directory, computing a Subresource Integrity hash for every file so a `<script
integrity="...">` / `<link integrity="...">` tag can pin it — the two
third-party libraries (SheetJS, MathJax) were unpinned CDN scripts in the
predecessor apps, so a CDN outage took out the export button and rendered
every abstract as raw LaTeX. ``hashed=True`` additionally renames each file
with a content hash for immutable caching.

This module must import with nothing beyond pydantic, PyYAML, Jinja2 and the
stdlib (``tests/unit/test_import_weight.py`` enforces it) — no FastAPI, no
SQLAlchemy. Serving these files over HTTP is an app's own concern (a static
file mount), not this module's.
"""

from __future__ import annotations

import base64
import hashlib
import math
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from .. import __version__
from ..errors import ConfigError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..eventprofile.models import EventProfile

__all__ = [
    "AssetsMissingError",
    "ThemeNotFoundError",
    "UIError",
    "VendorEntry",
    "VendorManifest",
    "assert_assets_present",
    "render_theme_vars",
    "static_path",
    "theme_ids",
    "theme_path",
    "vendor",
]

#: Paths (relative to a directory) that must be present for the kit to be
#: usable. Checked by both :func:`assert_assets_present` (against a live
#: ``static_path()`` or a mirrored copy of it) and, implicitly, by whatever a
#: theme's own asset set adds on top via ``themes/<id>/``.
_REQUIRED_RELATIVE_PATHS = (
    "tokens/tokens.css",
    "tokens/tokens.json",
    "tokens/fonts.css",
    "components/layout.css",
    "components/buttons.css",
    "components/forms.css",
    "components/cards.css",
    "components/header.css",
    "components/footer.css",
    "components/hero.css",
    "js/main.js",
    "js/hero.js",
    "vendor/xlsx-0.18.5.full.min.js",
    "vendor/mathjax-3-tex-mml-chtml.js",
)

#: Sub-directories of static_path()/"themes" that are shared assets (tokens,
#: shared js, vendor) vendored regardless of which theme is chosen.
_SHARED_ASSET_DIRS = ("tokens", "components", "js", "vendor")


class UIError(ConfigError):
    """Base class for eventkit.ui configuration/contract failures."""


class ThemeNotFoundError(UIError):
    """A theme id was requested that has no matching directory."""

    def __init__(self, theme_id: str, available: list[str]) -> None:
        self.theme_id = theme_id
        self.available = available
        super().__init__(
            f"unknown theme {theme_id!r}; available themes: {', '.join(available) or '(none)'}"
        )


class AssetsMissingError(UIError):
    """A directory that should contain the UI kit is missing required files."""

    def __init__(self, directory: Path, missing: list[str]) -> None:
        self.directory = directory
        self.missing = missing
        joined = ", ".join(missing)
        super().__init__(f"{directory} is missing required UI assets: {joined}")


def static_path() -> Path:
    """The shipped ``ui/static`` directory — a real directory even in an
    installed wheel, since :mod:`eventkit` is packaged unzipped (see
    ``[tool.hatch.build.targets.wheel]`` in ``pyproject.toml``)."""
    return Path(__file__).parent / "static"


def theme_ids() -> list[str]:
    """The available theme ids, i.e. the names of ``static_path()/"themes"``
    subdirectories that carry a ``theme.css``, sorted for determinism."""
    themes_dir = static_path() / "themes"
    return sorted(
        p.name for p in themes_dir.iterdir() if p.is_dir() and (p / "theme.css").is_file()
    )


def theme_path(theme_id: str) -> Path:
    """The directory for ``theme_id``. Raises :class:`ThemeNotFoundError` for
    an id with no ``theme.css`` — a typo'd ``branding.theme`` should fail at
    startup, not silently serve unthemed pages."""
    candidate = static_path() / "themes" / theme_id
    if not (candidate / "theme.css").is_file():
        raise ThemeNotFoundError(theme_id, theme_ids())
    return candidate


def assert_assets_present(directory: Path) -> None:
    """Raise :class:`AssetsMissingError` unless every file in
    :data:`_REQUIRED_RELATIVE_PATHS` exists under ``directory``.

    Meant for both ``static_path()`` itself (a packaging sanity check) and a
    directory an app vendored or mirrored assets into (a startup check that a
    partial sync or a pruned Docker layer did not silently drop files a page
    depends on)."""
    missing = [rel for rel in _REQUIRED_RELATIVE_PATHS if not (directory / rel).is_file()]
    if missing:
        raise AssetsMissingError(directory, missing)


# ---------------------------------------------------------------------------
# OKLCH ramp derivation
#
# Pure-Python sRGB <-> OKLab/OKLCH conversion (Björn Ottosson's OKLab), so a
# single brand_color hex can drive a lighten/darken ramp without adding an
# image/color dependency to a module that must stay import-light. Coefficients
# are the standard OKLab matrices; see https://bottosson.github.io/posts/oklab/
# ---------------------------------------------------------------------------


def _srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(c: float) -> float:
    c = max(0.0, min(1.0, c))
    return 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#" + "".join(f"{max(0, min(255, c)):02x}" for c in rgb)


def _hex_to_oklch(value: str) -> tuple[float, float, float]:
    r, g, b = (_srgb_to_linear(c / 255.0) for c in _hex_to_rgb(value))
    long = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    medium = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    short = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = long ** (1 / 3), medium ** (1 / 3), short ** (1 / 3)
    big_l = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    b2 = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    chroma = math.hypot(a, b2)
    hue = math.degrees(math.atan2(b2, a)) % 360
    return big_l, chroma, hue


def _oklch_to_hex(lightness: float, chroma: float, hue: float) -> str:
    hue_rad = math.radians(hue)
    a, b = chroma * math.cos(hue_rad), chroma * math.sin(hue_rad)
    l_ = lightness + 0.3963377774 * a + 0.2158037573 * b
    m_ = lightness - 0.1055613458 * a - 0.0638541728 * b
    s_ = lightness - 0.0894841775 * a - 1.2914855480 * b
    long, medium, short = l_**3, m_**3, s_**3
    r = 4.0767416621 * long - 3.3077115913 * medium + 0.2309699292 * short
    g = -1.2684380046 * long + 2.6097574011 * medium - 0.3413193965 * short
    b2 = -0.0041960863 * long - 0.7034186147 * medium + 1.7076147010 * short
    return _rgb_to_hex(tuple(round(_linear_to_srgb(c) * 255) for c in (r, g, b2)))


#: Multipliers applied to the base color's OKLCH lightness to derive the
#: auto-computed steps of the ramp. Tuned by eye against Princeton's official
#: orange (#e77500) — see themes/princeton-orfe/theme.css, whose four values
#: are this function's output for that exact color.
_DARKEN_80 = 0.80
_DARKEN_90 = 0.62
_TINT_10_TOWARD_WHITE = 0.85
_TINT_10_CHROMA = 0.35


def render_theme_vars(profile: EventProfile) -> str:
    """A ``:root { --color-brand-*: ...; }`` block sized to be the innermost
    cascade layer (see the module docstring), derived from
    ``profile.branding.brand_color`` via OKLCH lighten/darken so an adopter
    sets one hex and gets a full four-step ramp.

    ``profile.branding.brand_color_dark``, if set, is used directly as the
    "80" (hover/darker) step instead of the auto-computed one — a designer's
    manual pick for the color that matters most for on-dark-surface contrast.
    The "90" step is then a further auto-darken of whichever "80" is in
    effect, so overriding "80" still yields a coherent three-step ramp rather
    than two unrelated colors."""
    base_hex = profile.branding.brand_color
    lightness, chroma, hue = _hex_to_oklch(base_hex)

    tint_lightness = min(0.97, lightness + (1 - lightness) * _TINT_10_TOWARD_WHITE)
    brand_10 = _oklch_to_hex(tint_lightness, chroma * _TINT_10_CHROMA, hue)
    brand_70 = base_hex

    dark_override = profile.branding.brand_color_dark
    if dark_override:
        brand_80 = dark_override
        dark_lightness, dark_chroma, dark_hue = _hex_to_oklch(dark_override)
        brand_90 = _oklch_to_hex(dark_lightness * (_DARKEN_90 / _DARKEN_80), dark_chroma, dark_hue)
    else:
        brand_80 = _oklch_to_hex(lightness * _DARKEN_80, chroma, hue)
        brand_90 = _oklch_to_hex(lightness * _DARKEN_90, chroma, hue)

    return (
        ":root {\n"
        f"  --color-brand-10: {brand_10};\n"
        f"  --color-brand-70: {brand_70};\n"
        f"  --color-brand-80: {brand_80};\n"
        f"  --color-brand-90: {brand_90};\n"
        "}\n"
    )


# ---------------------------------------------------------------------------
# Vendoring
# ---------------------------------------------------------------------------


class VendorEntry(BaseModel):
    """One file copied by :func:`vendor`."""

    model_config = ConfigDict(extra="forbid")

    source: str  #: path relative to static_path()
    dest: str  #: path relative to the vendor destination (may be content-hashed)
    sha256: str  #: hex digest, for the manifest / debugging
    sri: str  #: "sha384-<base64>", for a <script integrity="..."> / <link integrity="...">
    bytes: int


class VendorManifest(BaseModel):
    """What :func:`vendor` copied, and the theme it was copied for."""

    model_config = ConfigDict(extra="forbid")

    theme: str
    eventkit_version: str
    hashed: bool
    entries: list[VendorEntry]


def _hash_file(path: Path) -> tuple[str, str]:
    data = path.read_bytes()
    sha256 = hashlib.sha256(data).hexdigest()
    sha384_b64 = base64.b64encode(hashlib.sha384(data).digest()).decode("ascii")
    return sha256, f"sha384-{sha384_b64}"


def _content_hashed_name(name: str, sha256: str) -> str:
    stem, _, suffix = name.rpartition(".")
    short = sha256[:8]
    if not stem:
        return f"{name}.{short}"
    return f"{stem}.{short}.{suffix}"


def vendor(dest: Path, *, theme: str, hashed: bool = False) -> VendorManifest:
    """Copy the shared UI kit assets plus one theme's own assets into
    ``dest``, computing SRI for every file.

    Layout under ``dest``: the shared directories (``tokens/``, ``components/``,
    ``js/``, ``vendor/``) keep their relative paths; the chosen theme's files
    land under ``theme/`` (an app serves exactly one theme, so there is no
    reason to nest it under ``themes/<id>/`` at the destination).

    ``hashed=True`` renames each copied file to embed an 8-character content
    hash before its final extension (``main.js`` -> ``main.9f86d081.js``) for
    immutable caching; ``hashed=False`` keeps the source names, for local
    development where a stable path is more convenient than a cache bust."""
    theme_dir = theme_path(theme)  # raises ThemeNotFoundError early
    source_root = static_path()
    assert_assets_present(source_root)

    to_copy: list[tuple[Path, str]] = []  # (absolute source, dest-relative-without-hash)
    for shared_dir in _SHARED_ASSET_DIRS:
        shared_root = source_root / shared_dir
        for file_path in sorted(shared_root.rglob("*")):
            if file_path.is_file():
                rel = Path(shared_dir) / file_path.relative_to(shared_root)
                to_copy.append((file_path, str(rel)))
    for file_path in sorted(theme_dir.rglob("*")):
        if file_path.is_file():
            to_copy.append((file_path, str(Path("theme") / file_path.relative_to(theme_dir))))

    entries: list[VendorEntry] = []
    for source_file, dest_relative in to_copy:
        sha256, sri = _hash_file(source_file)
        if hashed:
            dest_dir_part, _, dest_name = dest_relative.rpartition("/")
            hashed_name = _content_hashed_name(dest_name, sha256)
            dest_relative = f"{dest_dir_part}/{hashed_name}" if dest_dir_part else hashed_name
        dest_file = dest / dest_relative
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_file, dest_file)
        entries.append(
            VendorEntry(
                source=str(source_file.relative_to(source_root.parent)),
                dest=dest_relative,
                sha256=sha256,
                sri=sri,
                bytes=source_file.stat().st_size,
            )
        )

    return VendorManifest(theme=theme, eventkit_version=__version__, hashed=hashed, entries=entries)
