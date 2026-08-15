"""Build-time mirroring of a handful of assets off another site's origin.

Replaces ``posted/backend/download_assets.py``, which is invoked from the
app's ``lifespan`` (``posted/backend/main.py:34-40``): **every app start and
every test run** makes outbound requests to caarms.princeton.edu, carrying a
Cloudflare bypass header and a spoofed Chrome UA, and writes the responses
into a publicly-served mount. That means the app cannot boot offline, cannot
boot if the upstream site is down or blocking the request, and re-serves
another site's CSS from this origin, past its own bot filter, on every
restart.

:func:`mirror` is CLI-only (``eventkit mirror run``), meant to run in the
Docker build (so the image is reproducible and startup makes zero outbound
requests) or in a scheduled Action that opens a PR when a hash changes. It is
opt-in — the default posture, enforced by :mod:`eventkit.ui`, is "use the
shipped theme"; a missing or stale mirror destination degrades to that with
one ``WARNING`` (:func:`eventkit.ui.assert_assets_present`), never a crashed
boot.

Two asset locators, matching ``MirrorAsset.discover`` in ``PLAN.md`` B.10:

* ``url_path`` — fetch a known path directly.
* ``discover="link-css"``/``"img-src"`` — scrape ``MirrorSpec.discover_from``
  pages for ``<link rel="stylesheet" href=...>``/``<img src=...>`` and pick
  the first URL whose filename starts with the asset's ``name``. This is what
  lets an asset like the CAARMS header CSS
  (``align_header_text-bed7c47f….css``) survive an upstream cache-bust rename
  without a hardcoded content hash in this repo.

A bad asset (network error, discovery miss, wrong content-type, oversized
response) never aborts the run — like :mod:`eventkit.importer`, it becomes an
entry in :attr:`MirrorReport.errors` and the remaining assets still get a
chance. Every successful fetch is written atomically (temp file + ``os.replace``)
and recorded in a ``manifest.json`` at the destination, keyed by asset name;
:func:`mirror` reads that manifest back on the next run so ``force=False``
(the default) skips re-fetching an asset whose destination file is already on
disk. The manifest is also what lets a scheduled Action diff a checked-in
mirror against a fresh one and open a PR when an upstream hash changes.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from contextlib import suppress
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

logger = logging.getLogger("eventkit.mirror")

__all__ = [
    "BYPASS_HEADER_ENV",
    "BYPASS_VALUE_ENV",
    "MirrorAsset",
    "MirrorEntry",
    "MirrorManifest",
    "MirrorReport",
    "MirrorSpec",
    "bypass_header_from_env",
    "mirror",
]

#: Env vars carrying the Cloudflare-bypass header eventkit sends. No defaults,
#: ever — a hardcoded bypass value is the whole reason to redo this module.
BYPASS_HEADER_ENV = "MIRROR_BYPASS_HEADER"
BYPASS_VALUE_ENV = "MIRROR_BYPASS_VALUE"

_DEFAULT_USER_AGENT = "eventkit-mirror/0.1 (+https://github.com/pu-sherrerd/eventkit)"
_MANIFEST_NAME = "manifest.json"


class MirrorAsset(BaseModel):
    """One file to fetch. Exactly one of ``url_path``/``discover`` is set."""

    model_config = ConfigDict(extra="forbid")

    name: str
    url_path: str | None = None
    discover: Literal["link-css", "img-src"] | None = None
    max_bytes: int = 2_000_000
    expect_content_type: str | None = None

    @model_validator(mode="after")
    def _exactly_one_locator(self) -> MirrorAsset:
        if bool(self.url_path) == bool(self.discover):
            raise ValueError(f"asset {self.name!r} must set exactly one of url_path or discover")
        return self


class MirrorSpec(BaseModel):
    """The non-secret shape of a mirror job. ``bypass_header`` is filled in
    from the environment at CLI time (:func:`bypass_header_from_env`), never
    committed alongside the rest of this spec."""

    model_config = ConfigDict(extra="forbid")

    target_host: HttpUrl
    bypass_header: tuple[str, str] | None = None
    user_agent: str = _DEFAULT_USER_AGENT
    assets: list[MirrorAsset]
    discover_from: list[str] = Field(default_factory=list)


class MirrorEntry(BaseModel):
    """One asset :func:`mirror` has successfully fetched, as recorded in
    ``manifest.json``."""

    model_config = ConfigDict(extra="forbid")

    name: str
    dest: str  #: filename, relative to the mirror destination directory
    source_url: str
    sha256: str
    bytes: int
    content_type: str | None = None


class MirrorManifest(BaseModel):
    """The full contents of a destination's ``manifest.json``, keyed by asset name."""

    model_config = ConfigDict(extra="forbid")

    entries: dict[str, MirrorEntry] = Field(default_factory=dict)


class MirrorReport(BaseModel):
    """What one :func:`mirror` call did."""

    model_config = ConfigDict(extra="forbid")

    fetched: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    #: ``(asset name, message)`` for every asset that could not be mirrored.
    errors: list[tuple[str, str]] = Field(default_factory=list)

    def exit_code(self) -> int:
        """0 if every asset either fetched or was already present, else 1.

        Never 2 — a single bad asset degrades, per this module's docstring; it
        does not turn into a distinct "fatal" category the way an unreadable
        :mod:`eventkit.importer` source does, because there is no analogous
        "the whole source is unusable" failure mode here: each asset is its
        own independent fetch.
        """
        return 1 if self.errors else 0

    def render(self) -> str:
        lines = [
            f"Mirror report: {len(self.fetched)} fetched, "
            f"{len(self.skipped)} already present, {len(self.errors)} failed."
        ]
        if self.fetched:
            lines.append("  fetched: " + ", ".join(sorted(self.fetched)))
        if self.skipped:
            lines.append("  already present: " + ", ".join(sorted(self.skipped)))
        if self.errors:
            lines.append("Errors:")
            lines.extend(f"  [{name}] {message}" for name, message in self.errors)
        return "\n".join(lines)


def bypass_header_from_env(env: dict[str, str] | None = None) -> tuple[str, str] | None:
    """Read ``MIRROR_BYPASS_HEADER``/``MIRROR_BYPASS_VALUE``; ``None`` unless
    both are set and non-empty — there is no default bypass header."""
    source = os.environ if env is None else env
    name = source.get(BYPASS_HEADER_ENV)
    value = source.get(BYPASS_VALUE_ENV)
    if not name or not value:
        return None
    return (name, value)


class _AssetLinkParser(HTMLParser):
    """Collects ``<link rel="stylesheet" href>`` or ``<img src>`` URLs, in
    document order, for :func:`_discover_urls`."""

    def __init__(self, mode: Literal["link-css", "img-src"]) -> None:
        super().__init__()
        self._mode = mode
        self.found: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if self._mode == "link-css" and tag == "link":
            rel = (values.get("rel") or "").lower().split()
            href = values.get("href")
            if "stylesheet" in rel and href:
                self.found.append(href)
        elif self._mode == "img-src" and tag == "img":
            src = values.get("src")
            if src:
                self.found.append(src)


def _discover_urls(html_text: str, mode: Literal["link-css", "img-src"]) -> list[str]:
    parser = _AssetLinkParser(mode)
    parser.feed(html_text)
    return parser.found


def _match_candidate(name: str, urls: list[str]) -> str | None:
    """The first URL whose filename starts with ``name`` — e.g. asset name
    ``align_header_text`` matches ``.../align_header_text-bed7c47f….css``."""
    for url in urls:
        filename = url.rsplit("/", 1)[-1].split("?", 1)[0]
        if filename.startswith(name):
            return url
    return None


def _load_manifest(dest: Path) -> MirrorManifest:
    manifest_path = dest / _MANIFEST_NAME
    if not manifest_path.is_file():
        return MirrorManifest()
    try:
        return MirrorManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("mirror: %s is unreadable; starting from an empty manifest", manifest_path)
        return MirrorManifest()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp_name, path)
    except Exception:
        with suppress(OSError):
            os.remove(tmp_name)
        raise


class _AssetFetchError(Exception):
    """One asset could not be mirrored. Caught by :func:`mirror`, never escapes it."""


def _resolve_source_url(
    client: httpx.Client,
    asset: MirrorAsset,
    discover_from: list[str],
    discovered_cache: dict[str, list[str]],
) -> str:
    if asset.url_path is not None:
        return asset.url_path

    assert asset.discover is not None  # guaranteed by MirrorAsset's validator
    for page in discover_from:
        if page not in discovered_cache:
            try:
                response = client.get(page)
            except httpx.HTTPError as exc:
                raise _AssetFetchError(f"could not fetch discovery page {page}: {exc}") from exc
            discovered_cache[page] = (
                _discover_urls(response.text, asset.discover) if response.status_code == 200 else []
            )
        match = _match_candidate(asset.name, discovered_cache[page])
        if match is not None:
            return match

    raise _AssetFetchError(
        f"could not discover asset {asset.name!r} ({asset.discover}) from any of {discover_from!r}"
    )


def _fetch_asset(
    client: httpx.Client,
    asset: MirrorAsset,
    discover_from: list[str],
    discovered_cache: dict[str, list[str]],
) -> tuple[str, str, bytes, str | None]:
    """Returns ``(source_url, filename, data, content_type)`` or raises
    :class:`_AssetFetchError`."""
    source_url = _resolve_source_url(client, asset, discover_from, discovered_cache)

    try:
        with client.stream("GET", source_url) as response:
            if response.status_code != 200:
                raise _AssetFetchError(f"{source_url}: HTTP {response.status_code}")

            content_type = (response.headers.get("content-type") or "").split(";")[0].strip()
            content_type = content_type or None
            if asset.expect_content_type and content_type != asset.expect_content_type:
                raise _AssetFetchError(
                    f"{source_url}: expected content-type "
                    f"{asset.expect_content_type!r}, got {content_type!r}"
                )

            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > asset.max_bytes:
                    raise _AssetFetchError(f"{source_url}: exceeded max_bytes={asset.max_bytes}")
                chunks.append(chunk)
    except httpx.HTTPError as exc:
        raise _AssetFetchError(f"{source_url}: {exc}") from exc

    filename = source_url.rsplit("/", 1)[-1].split("?", 1)[0] or asset.name
    return source_url, filename, b"".join(chunks), content_type


def mirror(spec: MirrorSpec, dest: Path, *, force: bool = False) -> MirrorReport:
    """Fetch every asset in ``spec.assets`` into ``dest``.

    ``force=False`` (the default) skips re-fetching an asset that is already
    recorded in ``dest``'s ``manifest.json`` *and* whose file still exists on
    disk — the common case for a Docker build re-run against a warm layer
    cache. ``force=True`` always re-fetches, which is what a scheduled Action
    wants so it can diff the resulting hash against what is checked in.

    Never raises for one bad asset (network error, a discovery miss, the
    wrong content-type, an oversized response) — see :attr:`MirrorReport.errors`.
    """
    dest = Path(dest)
    manifest = _load_manifest(dest)
    report = MirrorReport()

    headers = {"User-Agent": spec.user_agent}
    if spec.bypass_header is not None:
        headers[spec.bypass_header[0]] = spec.bypass_header[1]

    discovered_cache: dict[str, list[str]] = {}

    with httpx.Client(
        base_url=str(spec.target_host).rstrip("/"), headers=headers, timeout=30.0
    ) as client:
        for asset in spec.assets:
            existing = manifest.entries.get(asset.name)
            if not force and existing is not None and (dest / existing.dest).is_file():
                report.skipped.append(asset.name)
                continue

            try:
                source_url, filename, data, content_type = _fetch_asset(
                    client, asset, spec.discover_from, discovered_cache
                )
            except _AssetFetchError as exc:
                report.errors.append((asset.name, str(exc)))
                continue

            _atomic_write(dest / filename, data)
            manifest.entries[asset.name] = MirrorEntry(
                name=asset.name,
                dest=filename,
                source_url=source_url,
                sha256=hashlib.sha256(data).hexdigest(),
                bytes=len(data),
                content_type=content_type,
            )
            report.fetched.append(asset.name)

    _atomic_write(
        dest / _MANIFEST_NAME,
        manifest.model_dump_json(indent=2).encode("utf-8") + b"\n",
    )
    return report
