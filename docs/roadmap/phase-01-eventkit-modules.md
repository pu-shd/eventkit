# Phase 1 — Finish the eventkit library

Build the eleven modules that are declared but absent, so the five applications
have something to sit on.

**Depends on:** nothing beyond what is merged.
**Blocks:** every application phase.
**Design detail:** [`PLAN.md`](PLAN.md) appendix §B (module API surfaces), §E (testing).

## Why this comes first

Every app phase consumes `auth`, `db` and `backup` on its first day. Building an
app first means inventing those in app-local form and then extracting them — which
is the mistake the two predecessor repos made, and why there are three copies of
the Drupal parser and two hand-rolled migrators.

The modules already built (`identity`, `drupal`, `eventprofile`, `webhook`,
`logging`, `eventbrite.models`/`aggregate`, `testing`, `cli`) were chosen because
they have no FastAPI or SQLAlchemy coupling. This phase adds the ones that do.

## Order within the phase

Build in this order; each is useful on its own and the later ones depend on the
earlier.

| Step | Module | Why here |
|---|---|---|
| 1 | `db` | Everything with a database needs it, and the Alembic decision shapes every app's `migrations/`. |
| 2 | `auth` | Nothing can be safely deployed without it. Also the single biggest deletion from the predecessors. |
| 3 | `backup` | Small, self-contained, and immediately replaces ~55 lines of hand-written field lists per app. |
| 4 | `realtime` | Needed by `ticket-reconciler`; polling-first, so simpler than it sounds. |
| 5 | `notify` | Optional at runtime (log transport default), so it cannot block a deploy. |
| 6 | `eventbrite.client` + `sync` | Only `ticket-reconciler` needs these. `aggregate` — the hard part — is already done and tested. |
| 7 | `importer` | Needed for every app's cutover backfill. |
| 8 | `admin` | Destructive-op task tokens. Needed only if you keep an HTTP path for destructive ops. |
| 9 | `ui` | Largest surface, but nothing blocks on it until an app needs a browser page. |
| 10 | `mirror` | Optional feature, CLI-only. Do last or never. |

## Specifications

Full signatures are in [`PLAN.md`](PLAN.md) §B.3–§B.10 and §D. The decisions that
are easy to get wrong:

### `db` — adopt Alembic, retire the hand-rolled migrator

`PLAN.md` §B.5 argues this at length. The short version: the predecessor migrators
wrap every `ALTER TABLE` in a `try/except` that logs and *continues*, so a failed
migration leaves the app serving against a schema it believes it has, and the first
write 500s in the webhook path — a dropped registration with no version row to
diagnose from. They also cannot rename, backfill, or add an index, and two changes
this extraction needs are exactly those.

Non-obvious requirements:

- **`journal_mode=TRUNCATE`, not WAL.** WAL needs a shared-memory mmap that SMB
  does not provide; on Azure Files you get intermittent `disk I/O error`. Also
  `synchronous=FULL`, `busy_timeout` ≥ 15000, and a single-connection pool.
- **`render_as_batch=True` and an explicit naming convention** in the shipped
  Alembic template, or SQLite migrations cannot name constraints to drop them.
- **`create_all()` moves out of module import** into `lifespan`, and `Settings`
  becomes `get_settings()` with `@lru_cache`. This one change deletes the
  env-vars-before-import preamble from all five apps' `conftest.py`.
- Ship `ensure_columns()` as a documented **hotfix-only** escape hatch that raises
  on failure, so nobody reinvents the 240-line version at 2am mid-conference.

### `auth` — a dependency, not a function call

`posted` calls `is_admin_authorized(request)` imperatively at the top of **18**
handlers. That pattern is a bug generator: a new handler that forgets the line is
silently public. `Depends` on an `APIRouter(dependencies=[...])` cannot be
forgotten.

- `dev_principal` has **no default**, and `EasyAuth.__init__` **raises** if it is
  set while `WEBSITE_SITE_NAME` indicates App Service.
- `require_claims_header=True`: demand and decode `X-MS-CLIENT-PRINCIPAL`, not just
  the name header. Today one spoofable header is the entire authentication.
- Empty allow-list means **deny all**.
- The themed access-denied page replaces ~90 lines of inline HTML.

### `realtime` — polling is the default, WebSocket is the opt-in

`ticketed` keeps sockets in a module-level Python list, so with two App Service
instances a check-in on one never reaches a browser on the other, and every send
error is swallowed. Two front-desk iPads silently disagree about who is checked in.

`GET /api/changes?since=<cursor>` over a monotonic change log is correct on N
instances, needs no sticky sessions, and survives the captive-portal wifi that kills
long-lived sockets. Three iPads polling every 3s is nothing.

### `notify` — SMTP, not ACS, as the recommended real transport

Default is `LogTransport` so a missing credential can never block a deploy. For a
real transport prefer SMTP: every university has a relay, whereas ACS Email needs a
provisioned Communication Service and DNS access the adopter may not have. Resend
and ACS ship as extras.

Wrap blocking SDK calls in `anyio.to_thread` — the predecessor called blocking
`resend.Emails.send` from an `async def` inside the webhook path.

### `ui` — no bundler

~2–3k lines of shared JS, native ESM, for authenticated tools used by a few dozen
staff. A bundler would mean five repos each needing `package.json` and
`node_modules` in their Docker build for no measured benefit. Node enters only as a
test dependency, confined to the Docker `test` stage.

Two things that matter more than bundling:

- **Vendor SheetJS and MathJax with SRI.** They are unpinned CDN scripts today, so
  an outage takes out the export button and renders every abstract as raw LaTeX.
- **Delete string-interpolated inline handlers.** `app.js` builds
  `onclick="openLinkModal('${escapeHtml(row.first_name)}', …)"` — an HTML escaper
  applied to a JavaScript string-literal context, so a name containing `\'` breaks
  out. `table.js` uses `data-action` and one delegated listener.

Delete from the vendored Paper Tiger kit: `components/cards.html` and `hero.html`
(fabricated ORFE news headlines attributed to a real department, plus a
plausible-looking netID) and the Sherrerd Hall address line in `footer.html`.

### `mirror` — build-time only, and opt-in

`posted` fetches CSS from a live Drupal host on **every app start and every test
run**, carrying a WAF bypass header and a spoofed Chrome UA, writing the responses
into a publicly-served mount. Move it to a CLI invoked in the Docker build, with
content-type and size validation and atomic writes, bypass value from env with no
default. The default posture becomes "use the shipped theme".

## Tests

The suite layout is in [`PLAN.md`](PLAN.md) §E.1. Priorities:

- `db`: empty → head; a legacy-`stamp`ed database → head; concurrent upgrade
  blocked by the filelock; **a failed migration does not leave the app running**;
  pragmas actually applied and `TRUNCATE` not WAL.
- `auth`: the header matrix; dev bypass refused when `WEBSITE_SITE_NAME` is set;
  redirect vs 401 by path; WS ticket expiry and tampering.
- `backup`: round trip through `TestClient`; restore rejects a foreign
  `manifest.app_name`; the whole payload is validated **before the first DELETE**.
- `eventbrite.sync`: a fake `SyncPorts` plus `respx`, asserting emitted events —
  zero DB, zero network.
- `notify`: loader precedence, autoescape, and that blocking transports run off the
  event loop.
- `tests/integration/test_reference_app.py`: a ~120-line app wiring **every**
  module together. This is the executable version of "here is how you build an app
  on eventkit" and it catches API breaks that unit tests miss.

## Acceptance criteria

- [ ] All ten modules importable, with the extras split respected:
      `eventkit.eventprofile` and `eventkit.ui` still import with no FastAPI and no
      SQLAlchemy (`tests/unit/test_import_weight.py` enforces it).
- [ ] `test_reference_app.py` exercises every module together and passes.
- [ ] `docker-compose run --rm test` green; coverage does not regress below 86%.
- [ ] `ruff check src tests` clean.
- [ ] README's "Not yet built" list reduced to only what is genuinely absent.
- [ ] `CHANGELOG.md` records the breaking surface for v0.2.
- [ ] No `@princeton.edu`, no discount codes, no bypass header value anywhere
      outside `examples/` and the Princeton theme.

## Risks

**The extras split is easy to break.** One stray `from fastapi import …` in
`eventprofile` makes `link-forge` and `nametag-press` pull in a web framework to
render a PDF. The import-weight test is the guard; do not skip it when it fails.

**Alembic on SQLite over SMB is the least-tested combination here.** Test against
a file-backed SQLite database, not `:memory:`, or the migration tests prove
nothing about the deployment.

**`ui` will churn weekly during the first event cycle** while the Python API
should be stable. Keep the documented rule that patch releases may change
`ui/static/**` and nothing else, so a CSS fix is a pin bump with no Python review.
