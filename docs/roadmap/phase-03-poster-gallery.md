# Phase 3 — `poster-gallery`

The first application extraction, and the proof case for everything else.

**Depends on:** Phase 1 (`db`, `backup`, `importer`, `ui`), Phase 2 for deployment.
**Blocks:** nothing, deliberately — it is the lowest-risk vertical.
**Design detail:** [`PLAN.md`](PLAN.md) §G.5.

## Why this one first

It is the smallest complete vertical: one model, one webhook, one public page, RSS,
and the importer. It exercises `drupal` + `db` + `migrate` + `backup` + `importer` +
`ui` + `eventprofile` + the pytest plugin — everything except Eventbrite,
WebSockets and Easy Auth. It has no admin UI to port. And it is public-facing, so a
regression is visible in minutes rather than at check-in.

Its deliverables become the template for the other four: the `create_app()`
factory, `migrations/` with a first revision plus a `stamp` of the live database, a
multi-stage Dockerfile with a `test` target, `deploy/app.conf`, and a ~15-line
`conftest.py`.

## Scope

Public poster-presenter directory with MathJax for LaTeX abstracts, `?presenter=`
single view, RSS 2.0 feed, the poster webhook with `is_visible` soft-delete, and
bulk import.

File tree, model, and the full route table are in `PLAN.md` §G.5.

## The security fix that defines this phase

`GET /api/presenters` is unauthenticated and served `PresenterResponse`, which
included `email_address`, `drupal_sid`, `serial_number` and `is_visible`
(`posted/backend/schemas.py:78-92`). Every presenter's address was scrapable.

**This is already fixed in the archived `posted`** — carry the fix forward, do not
re-derive it:

- `PresenterPublic` carries exactly six fields: `id`, `first_name`, `last_name`,
  `poster_title`, `faculty_adviser_name`, `poster_presentation_abstract`.
- A **trip-wire test** fails if the public model ever gains a field outside an
  explicit allow-list. The original bug was one careless `response_model` reuse, so
  pin the shape rather than relying on review.

## What to delete

**`download_assets.py` in full.** 58 lines fetching six CSS files from a live Drupal
host on every app start *and every test run*, with a hardcoded site-directory hash,
content-hashed filenames that rot on any upstream edit, a spoofed Chrome User-Agent,
and a WAF bypass header — writing the responses into a publicly-served mount.

Also: `frontend/static/css/*` (vendored copies of someone else's site, several of
which are already 0-byte in the archive, i.e. the mirror had partly failed), the
literal `"CAARMS 2026 Poster Presenters"` feed title (`main.py:88-90`), and the
hardcoded `/poster-presenters` back-link (`index.html:272,282`).

### What replaces the mirror

Paper Tiger tokens as the baseline, plus an optional **committed**
`static/theme/host-theme.css` an adopter writes once by setting ~20 CSS custom
properties. Ship a worked example plus a "how to extract these values from your
Drupal theme with devtools" recipe.

"Look like our Drupal site" is a design-token problem. A build-time mirror of a CMS
you do not control produces silent visual breakage on every upstream theme release,
plus a supply-chain path from their CDN into your page. For adopters who insist,
document an *optional offline* `eventkit ui vendor-theme <url>` that writes into the
repo and is reviewed in a PR — never at runtime.

## Cutover

1. `GET /api/admin/db-backup` on the archived app.
2. `poster-gallery import --from-legacy-backup backup.json` on the new one.
3. Run **both** behind the same webform for a week — two Remote Post handlers, two
   tokens — and diff the presenter lists nightly.
4. Remove the old handler.

## Tests

- Public payload has exactly the allow-listed keys, and **no `@` anywhere in the
  serialized JSON**.
- Hidden presenters excluded from the list, from detail (404), and from the feed.
- `presenting_poster` falsy values (`""`, `no`, `No`, `0`, `off`) all soft-hide;
  `1/true/yes/on/checked` all show (`main.py:160-162`).
- Soft-hidden then re-submitted → visible again with updated fields.
- Feed validates as RSS 2.0: `guid isPermaLink="false"`, RFC-822 `pubDate`,
  `atom:link` self.
- An abstract containing `$\alpha < \beta$` **and** `]]>` survives XML escaping.
- Importer idempotent on re-run; dedupes by `person_key`.
- vitest: MathJax typesets *after* async render — a race the current page has;
  `?presenter=<uuid>` deep link expands the right card; `esc()` on `<script>` in a
  poster title.

## Acceptance criteria

- [ ] `create_app()` factory, no import-time side effects; `conftest.py` is one line.
- [ ] `migrations/` with a first revision, and the live database `stamp`ed.
- [ ] Multi-stage Dockerfile: no `tests/` in the runtime image, non-root,
      healthcheck, no `build-essential`.
- [ ] `docker-compose run --rm test` green, pytest and vitest in one command.
- [ ] `curl /api/presenters | grep -c '@'` returns 0 against the deployed app.
- [ ] No outbound network at import or in tests (the autouse `_no_network` fixture).
- [ ] A week of parallel running with nightly diffs showing no divergence.
- [ ] `deploy/app.conf` drives the toolkit end to end.

## Risks

**This phase is the template.** Anything sloppy here gets copied four times. Spend
the time on the `create_app()` factory and the `conftest.py` shape.

**MathJax and the abstract field are the only place attacker-controlled content is
rendered on a public page.** The `#pattern` guards on the form reduce but do not
eliminate it. Escape at render, and keep the vitest escaping test.
