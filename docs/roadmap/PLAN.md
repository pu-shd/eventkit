<!--
  Committed copy of the implementation plan. Authored 2026-07-30, before any code
  was written; kept verbatim as the durable design record.

  REDACTED FOR PUBLICATION. Three classes of value were replaced so this file can
  live in a public repository without weakening the CI hygiene guards:

    * Ten Eventbrite discount codes -> CODE_* pseudonyms. They are semi-public by
      nature (the Twig that computes them is delivered to the browser) but do not
      belong in git.
    * The WAF bypass header name -> `x-waf-bypass`.
    * One institutional address -> example.edu.

  Nothing else was changed. Where this file and a phase document disagree, the
  phase document is newer and wins.
-->

# Break `ticketed` + `posted` into a reusable event-management stack

## Context

Two repos (`~/Downloads/ticketed`, `~/Downloads/posted`) plus a set of Drupal 10 Webforms ran
CAARMS 2026 at caarms.princeton.edu successfully, but they were assembled and edited live as the
event's needs changed. The result works and is CAARMS-shaped: event dates, Eventbrite discount
codes, t-shirt size vocabularies, Princeton netIDs, hotel-less lodging rules, and Avery geometry
are hardcoded across Python, HTML, JS, shell, and CI — often in five places at once.

Goal: break the apps into separately adoptable components of one event-management stack, public
under the `pu-sherrerd` GitHub org, documented well enough that other event planners and IT
specialists can run their own events, and reusable for our future events. CAARMS 2026 was the most
complicated event we run (conditional ticketing, special lodging rules, t-shirt swag), so it
becomes the reference example rather than the baked-in assumption.

Adopter prerequisites, assumed and no more: the full Drupal 10 Webform module suite, an Eventbrite
team with API access, and Azure for GitOps deployment.

### Decisions already made (with the user)

| | |
|---|---|
| Repo topology | 8 public repos: `eventkit` SDK + 5 apps + `drupal-event-forms` + `event-stack` meta |
| Data flow | Independent DB per app; each app registers its own Drupal Remote Post handler with its own token. No inter-service runtime dependency. |
| Datastore | SQLite on Azure Files (`sqlite:////home/<app>.db`) default; Postgres Flexible Server opt-in |
| Reference event | Sanitized `examples/caarms-2026/` profile — real codes, tokens, netIDs, and speaker PII stripped |

### Repo map

| Repo | Purpose |
|---|---|
| `event-stack` | Meta: architecture, quickstart, event-profile spec, runbook, security/privacy, ADRs |
| `eventkit` | Python core lib + Paper Tiger UI kit + Azure zsh bootstrap toolkit |
| `drupal-event-forms` | Webform YAML exports, Remote Post recipes, email templates, DocuSign PowerForm recipe, FieldMap contracts |
| `ticket-reconciler` | Drupal↔Eventbrite reconciliation, front-desk check-in, swag inventory, waivers |
| `lodging-planner` | Rooms, drag-and-drop assignment board, gender/roommate rules engine |
| `nametag-press` | Avery-template PDF badges |
| `link-forge` | Prefilled per-person links: reimbursement, DocuSign media release, slide upload, tokenized speaker webform prefill |
| `poster-gallery` | Public poster-presenter directory + RSS + MathJax |

---

## Do these first, independent of the refactor

1. **Rotate every live secret.** `DRUPAL_WEBHOOK_TOKEN`, `NAMETAGS_WEBHOOK_TOKEN`,
   `EVENTBRITE_API_TOKEN`, `RESEND_API_KEY`, the `x-waf-bypass` value, and the three Drupal
   Remote Post handler tokens. `ticketed/backend/main.py:285-286` logs all request headers **and**
   both the received and expected webhook token at INFO on every webhook call — those values are in
   App Service logs and Log Analytics, readable by anyone with Reader on the resource group.
2. **Regenerate the 9 speaker webform prefill tokens.** `~/Downloads/links-for-speakers.html`
   contains nine live 43-char `?token=` bearer links, one per named speaker, each of which lets the
   holder open and edit that speaker's submission. Treat as leaked.
3. **Tag both repos `pre-extraction`** so the working state is recoverable.

---

## Phase 0 — hygiene and scaffolding (½ day)

- Create the 8 repos with **fresh `git init`, no history import**. `posted/backend/config.py:25`
  hardcodes seven real Princeton netIDs; `posted/backend/main.py:224` and
  `ticketed/backend/main.py:210` hardcode dev principals; `.env.deploy` has been written locally.
  `git filter-repo` on two repos to produce six is more work and less certain than starting clean,
  and the history has no external value.
- Archive (don't delete) `ticketed` and `posted` at the end, READMEs pointing at the new repos.
- Org-level branch protection, required status checks, a `pu-sherrerd/.github` repo for reusable
  workflows, and `CODEOWNERS` on `migrations/`, `contracts/`, and `.eventkit/`.
- CI grep that fails on `@princeton.edu` outside `examples/` and `themes/princeton-orfe/`, plus
  `gitleaks` on every PR.
  **Superseded:** no third-party scanners or marketplace actions. The greps stayed and were
  extended to the generic credential shapes; gitleaks was removed. See `STATUS.md`.

## Phase 1 — `eventkit` v0.1, proved in place (1–2 weeks)

The anti-big-bang move: build the FastAPI/SQLAlchemy-free modules first, then adopt them **inside
the existing `ticketed` and `posted` repos** and delete the duplicated code there. Ship both to
Azure. If the reconciler still reconciles against real Drupal traffic, the core API is validated
before any repo is split — and a wrong API is fixed in one library with two callers, not five.

Build order: `identity`, `drupal`, `eventprofile`, `notify`, `importer`, `eventbrite.aggregate`,
`eventbrite.client`, `ui`, `testing`.

Then delete from the old repos: `ticketed/backend/schema_parser.py` (248 lines),
`ticketed/backend/notifications.py` (137 lines), the aggregation loop at
`ticketed/backend/eventbrite.py:78-160`, the `mode="before"` validators in both `schemas.py`
(`posted/backend/schemas.py:16-76` and `:111-193` are ~85% duplicated), and
`posted/backend/download_assets.py` from the startup path.

## Phase 2 — `poster-gallery` (1 week)

The right proof case: smallest complete vertical (one model, one webhook, one public page, RSS,
importer), exercises everything except Eventbrite/WebSockets/Easy Auth, no admin UI to port, and
public-facing so a regression shows in minutes rather than at check-in. Its deliverables become the
template for the other four: `create_app()` factory, `migrations/` + `stamp` of the live DB,
multi-stage Dockerfile with a `test` target, `deploy/app.conf`, ~15-line `conftest.py`.

Cutover: `GET /api/admin/db-backup` on old → `poster-gallery import` on new; run both behind the
same webform for a week with two Remote Post handlers, diff nightly, then remove the old handler.

## Phase 3 — `ticket-reconciler` (2–3 weeks)

Highest value and highest risk, so it runs second on a validated foundation. Carries `auth`,
`webhook`, `backup`, `eventbrite.sync`, `realtime`, the check-in key migration, and all of the
security fixes below. Cut over well outside any registration window.

## Phase 4 — split `posted` into `nametag-press`, then `lodging-planner` (2–3 weeks)

The hard one: both read the same `Registrant` table today, and independent-DB-per-app makes that
two tables in two databases. `nametag-press` first (read-mostly), then `lodging-planner` (rooms,
rules engine, drag-drop, concurrency). Document explicitly that lodging write-ins do **not**
propagate to nametags, and ship the bridge: `nametag-press import --from lodging-backup.json` plus
`identity-drift --against <other-backup.json>`.

## Phase 5 — `link-forge` (2–3 days) · Phase 6 — `drupal-event-forms` + `event-stack` (1 week)

---

## `eventkit` — the SDK

One repo, **three published distributions** plus a convenience meta-package, because the Python
core (imported by five apps at runtime), the UI kit (changes weekly during a conference cycle), and
the operator CLI have unrelated release cadences: `eventkit-core`, `eventkit-ui`, `eventkit-azure`,
`eventkit` (depends on all three). Import name stays `eventkit`. **Check PyPI name availability
before settling — `eventkit` is likely taken; prefix the distributions if so.**

Consumption at v0.1: GitHub codeload tarball in `requirements.txt`
(`eventkit-core[app] @ https://github.com/pu-sherrerd/eventkit/archive/refs/tags/v0.1.0.tar.gz`) —
a plain HTTPS fetch, so `python:3.11-slim` needs no `git`. PyPI publication + Sigstore signing at
v0.2; CI pins by commit SHA, not tag, since a tag is mutable and this code runs with Azure
credentials in its environment.

`src/` layout, hatchling, extras (`web`, `db`, `http`, `resend`, `acs`, `postgres`, `app`, `test`).
Hard constraint: `eventkit.eventprofile` and `eventkit.ui` import with zero FastAPI/SQLAlchemy so
`link-forge` and `nametag-press` stay light.

### Modules

| Module | Replaces | Notes |
|---|---|---|
| `drupal` | 3 near-identical parsers | `coerce.py` primitives (unwrap `data` wrapper, composite email/name, `select_other`, truthy set, bare-name split) + configurable `FieldMap` + one `parse_submission()` used by **both** webhook and importer |
| `identity` | email-as-join-key | `person_key(uuid, email)` preferring the Drupal submission uuid the webform already emits and nobody uses; `IdentityMixin`. **Frozen, versioned, tested** — changing it later orphans every row. |
| `auth` | 2 header-trusting helpers + 90 lines of inline HTML | `Depends()`-based Easy Auth, allow-list (empty ⇒ deny all), themed access-denied page, HMAC WebSocket tickets. Refuses to start if a dev bypass is set while `WEBSITE_SITE_NAME` exists. |
| `webhook` | `!=` string compares | `hmac.compare_digest`, `assert_strong()` rejecting weak/short tokens, `deferred()` for slow work, and **HMAC-over-body + timestamp** (plain token accepted with a warning for one release) |
| `db` | 341 lines of hand-rolled `ALTER TABLE` | `Database`, Alembic via `lifespan_migrations` (filelock + pre-migration snapshot), Azure Files pragmas (`journal_mode=TRUNCATE` — WAL is impossible over SMB — `synchronous=FULL`, `busy_timeout≥15000`, single-connection pool) |
| `backup` | 2 hand-written dump/restore pairs | Mountable router over a declared `BackupSpec`; column list from `sqlalchemy.inspect`, not hand-maintained; validate whole payload before the first DELETE; `enable_restore` defaults **False** |
| `eventbrite` | client + 80-line loop + coupling | Pure `aggregate_by_email()` (table-testable), `EventbriteClient` with an injectable transport for `respx`, `SyncPorts` protocol so sync doesn't import notifications or write `SyncLog` itself |
| `notify` | Resend welded to 4 templates | Transport protocol; **`LogTransport` default, `SmtpTransport` recommended** (every university has a relay; ACS needs a provisioned Communication Service and DNS access), Resend/ACS as extras. Blocking SDKs wrapped in `anyio.to_thread`. |
| `eventprofile` | ~all the hardcoding | See below |
| `realtime` | module-global socket list | **Polling-first**: `GET /api/changes?since=<cursor>` over a monotonic change log. Correct on N instances, no sticky sessions, survives captive-portal wifi. WS opt-in. |
| `importer` | `import_existing.py` | Generalized to any app model |
| `mirror` | `download_assets.py` | **CLI-only, build-time**, never imported by app modules |
| `ui` | 5 drifting copies of the same chrome | Paper Tiger tokens/themes + ES modules |
| `testing` | 2 hand-rolled conftests | pytest plugin: `eventkit_env`, `event_profile`, `make_database`, `make_client`, autouse `_no_network` |

### `event-profile.yaml` — the de-CAARMS-ification layer

One validated YAML per event, loaded once, exposed to the browser as public JSON at
`GET /api/event-profile`. Per-domain top-level sections with **per-app required-key validation** so
adding a lodging key doesn't break `nametag-press` startup.

Sections: `event` (name/short_name/year/slug/urls) · `schedule` (timezone, dates, and
**check-in day keys as ISO dates** — today `ticketed/frontend/app.js:1258-1262` hardcodes `6/28`,
`6/29`, `6/30`, `banquet`, `7/1`, year-less keys that collide across events) · `branding` (one
brand hex drives a derived ramp) · `drupal` (`field_map` or `webform_schema` path; **no embedded
default** — fail fast, because today's CAARMS default silently wins for every adopter) · `roles` ·
`affiliation` (`domain_map`, generalizing the `princeton.edu` → "Princeton University" rule
duplicated in six places) · `ticketing` (tiers carry a **discount-code env var name**, never the
code; replaces `ticketed/backend/main.py:499-511` where `CODE_AFFILIATE` and `CODE_GENERAL` are
hardcoded alongside the `caarms-2026-tickets-{id}` slug) · `swag` (the
`NONE/USML/UMED/ULRG/U1XL/U2XL/U3XL` vocabulary, currently duplicated in five-plus places) ·
`lodging` (vocabularies + per-rule severity) · `nametags` · `notify` · `links`.

Resolve the `#f58025` vs `#e77500` conflict in favour of `#e77500`, which is what
`paper-tiger/tokens/tokens.json` already declares. A CI check fails on hex literals inside `style="`
attributes in app markup — that's what made the conflict possible.

### UI kit

Keep the tokens, component CSS, `main.js`, `hero.js`, and logos. **Delete** `components/cards.html`
and `hero.html` (fabricated ORFE news headlines attributed to a real department, plus
`jdoe@example.edu`) and the Sherrerd Hall address line in `footer.html`; replace with one
`kitchen-sink.html` per theme using obviously-synthetic content. Two themes at v0.1: `neutral` and
`princeton-orfe`.

Shared ES modules replacing five drifting copies: `esc`, `fetchx` (401 → login redirect, 409 →
reload toast), `toast`, `table` (declarative, `data-action` + one delegated listener),
`filters`, `chrome`, `backup-panel`, `checkin`, `lodging-rules` (pure, testable), `qr`,
`eventprofile`.

**No bundler.** ~2–3k lines of shared JS, native ESM, HTTP/2, authenticated tools for a few dozen
staff. A bundler means five repos need `package.json` + `node_modules` in the Docker build for zero
measured benefit, and the zero-build reality lets an IT specialist fix a typo in devtools. Node
enters only as a test dependency (`vitest` + `jsdom`) confined to the Docker `test` stage.
**But vendor SheetJS and MathJax with SRI** — they're unpinned CDN scripts today, so an outage
takes out the export button and renders every abstract as raw LaTeX.

Also eliminate string-interpolated inline handlers: `ticketed/frontend/app.js` builds
`onclick="openLinkModal('${escapeHtml(row.first_name)}', …)"`, applying an HTML escaper to a
JavaScript string-literal context.

---

## `eventkit azure` — the bootstrap toolkit

Shipped as package data, run without a clone: `pipx run --spec <pinned> eventkit azure <verb>`.

`lib/{boot,color,log,prompt,state,name,az,gh,secrets,manual,verify}.zsh` + numbered step files, so
adding a sixth app is a config file rather than a new script. Today `ticketed/deploy/` (~870 lines)
and `posted/deploy/` (~605) are 70–90% the same boilerplate.

**Verbs:** `deploy` (idempotent and resumable by default), `resume` (alias that skips re-prompting),
`update`, `teardown`, plus `doctor`, `status`, `bootstrap` (whole-event, dependency-ordered),
`adopt`, `oidc`, `secrets rotate`, `domain`, `scale-guard`, `backup`/`restore`, `drift`,
`gate ack`, `logs`, `open`, `eject`. Colorized with `NO_COLOR`/non-TTY respect, `[4/12]` step
counters, per-answer validation at entry (ACR alnum-only 5–50 lowercase, web app global
uniqueness), `--yes`/`--non-interactive`, and `--dry-run` printing the `az` commands.

### The manual-step gate — the specific ask

`await_manual_step` prints a numbered copy-pasteable checklist with a deep link to the exact Azure
portal blade, then **polls a verification predicate** with a spinner and elapsed time, showing what
it is waiting for. It succeeds the instant the predicate passes; offers `[s]kip / [r]etry /
[o]pen portal / [q]uit and resume later`; persists position on quit so `resume` continues there;
times out with instructions rather than hanging; and under `--yes` fails fast with the checklist
instead of blocking a CI job. Skipped gates with `risk: critical` surface in nightly drift, with
`gate ack --until <date> --reason <ticket>` for tracked, time-boxed exceptions so alert fatigue
doesn't re-establish the `&>/dev/null` habit.

Gates, each with an `az … --query` predicate: Entra ID app registration + Easy Auth identity
provider (**the step `posted/deploy/deploy.sh` doesn't script or even document, despite its entire
admin authorization model depending on the resulting header**); DNS CNAME + custom domain +
managed cert; Eventbrite private token + event id (a human fetches these from the Eventbrite UI);
Drupal Remote Post handler creation — verified by polling a new app-side
`GET /api/webhook/status` "have I received an authenticated submission yet" counter; resource
provider registration; `gh auth` state.

### State and orchestration

Replace `.env.deploy` with a committed `.eventkit/state.json` step ledger — each step records
`pending|done|skipped|failed`, a timestamp, and the resource id it created, so `resume` replays only
`pending`. Secrets never enter it: App Service app settings are the source of truth, read back on
resume; local values stay in a gitignored `.env.deploy`. Committing the ledger makes it a
supply-chain surface, so `CODEOWNERS` on `.eventkit/**` plus a CI check that `names.*` and
`subscriptionId` are unchanged without an `infra-change` label.

One resource group per event with all apps inside; one shared App Service Plan (B1 hosts several);
one ACR per org. Parameterized naming, no `orfe-` hardcoding, checked against Azure length limits.

### Fixes, not ports

- `ticketed/deploy/deploy.sh:391` — a backslash followed by a blank line truncates the
  `az webapp config appsettings set` command, so everything from `EVENTBRITE_API_TOKEN` onward is
  silently dropped.
- **The toolkit is the only writer of app settings.** They're defined twice today (`deploy.sh` and
  `deploy.yml`) and have already drifted three ways on the admin-principal list alone (7 in
  `posted/backend/config.py:25`, 7 in `deploy.yml:100`, 4 in `deploy.sh:137`). CI only ships images.
- **One build path.** `az acr build` in the shell scripts vs local `docker build`+`push` in CI.
- `RG_NAME` drift: `ticketed/deploy/teardown.sh:21` defaults to `orfe-reconciler-rg` while
  `.github/workflows/teardown.yml:28` hardcodes the real production RG. A CI teardown and a local
  teardown target different resource groups.
- `az postgres flexible-server create --public-access None`, then add only App Service outbound
  IPs — the current `0.0.0.0`-then-narrow leaves a real exposure window for no reason. Outbound IPs
  change when the plan is scaled, so `drift.yml` **auto-remediates** this one (the failure is an
  outage, not a report).
- `WEBSITES_CONTAINER_START_TIME_LIMIT=600` for every app with startup migrations — the 230s
  default will kill a first boot.
- **Nightly backup is mandatory, not optional.** SQLite on `/home` has no PITR and no server-side
  backup, so "free datastore" otherwise means "one bad restore from total loss" — precisely the
  `db_admin_tool.py` failure mode being eliminated. Scheduled snapshot → Storage Account with
  lifecycle rules.
- Easy Auth client secrets expire. `drift.yml` warns at <60 days; `secrets rotate` uses `--append`
  so there's no window. This is the most likely "the site broke and nobody knows why" event on a
  two-year horizon and nothing today would catch it.
- Split the single all-powerful deployer SP into least-privilege identities; add
  `environment:production` federated-credential subjects, not just `ref:refs/heads/main`
  (note: FIC subjects don't support wildcards).
- Never interpolate `secrets.*` into `if [ -n "…" ]` shell tests.
- Delete the postgres service container from the test job that then sets
  `DATABASE_URL=sqlite:///:memory:`.
- `db_admin_tool.py` is replaced by `eventkit azure backup`/`restore` against the app's own unified
  format. Its dumps cover only `registrants` + `payments`, so restoring one silently wipes
  `saved_groups` and `shirt_inventory`.

### Shell testing

`shellcheck` + `bats-core` for the pure helpers, and a **mock `az` on `PATH`** that records
invocations and replays canned JSON so the whole `deploy` flow runs end to end with no Azure
account — in the Docker `test` target, same command locally and in CI.

**Build the manual-step gate before the provisioning steps.** It's the stated requirement, and it's
also what lets the undocumented Easy Auth configuration on the live `posted` deployment get
verified and documented this week, without waiting for a toolkit that can provision anything.

---

## Per-app carve-outs

### `ticket-reconciler` (from `ticketed`)

Reconciliation report engine (`main.py:448-579`) extracted as a **pure**
`build_report(registrants, payments, profile)` with no `Session` and no `settings` — it's the most
valuable and least testable code in the stack. Plus Eventbrite sync, front-desk check-in, swag
inventory/replacement/issuance, saved groups, waivers, refund override, Excel export, QR links.

- `Payment.email` **drops `unique=True`** (`models.py:29`). One purchaser buying two tickets is a
  500 today; the HEAD commit worked around it by aggregating, but the constraint is the bug.
- Migration `0002`: rewrite `checkin_status` JSON keys `"6/28"` → ISO by **position** against
  `profile.schedule`, not by parsing — `"7/1"` and `"07/01"` both appear and `"6/28"` is ambiguous
  across years. Fail loudly on an unrecognized key.
- `t_shirt_size` → `swag_size`; this app owns swag **exclusively** (see `nametag-press`).
- Keep in-browser SheetJS export (filtered rows, what staff want) and add `/export.xlsx`
  (auditable full dump).

### `lodging-planner` (from `posted`)

Rooms, bulk create with zero-pad-preserving auto-increment (`main.py:438-447`), the occupied-room
edit lock (`:512-524`), drag-and-drop board in grid and list views, drag-to-reorder, write-ins and
"promote a registered non-lodging attendee", and the advisory rules engine.

Three fixes:

1. **Optimistic concurrency.** No version check exists anywhere in `main.py:384-733`; two planners
   dragging the same board silently overwrite each other, in the week when that's most likely.
   `row_version` on every mutating route, 409 + fresh entity, reload toast. Room reorder uses a
   single board-level etag since per-row versions can't express an ordering conflict.
2. **Server-side rules as source of truth.** `rules.py` mirrors the client engine
   (`admin_lodging.html:1572-1650`), which has **zero test coverage** today because it lives in a
   `<script>` tag reading page globals. Codes not prose: `OVER_CAPACITY`,
   `ROOM_GENDER_MISMATCH`, `REQUIRE_SAME_GENDER_VIOLATED`, `PREFER_SAME_GENDER_VIOLATED`,
   `ROOMMATE_NOT_REGISTERED`, `ROOMMATE_ELSEWHERE`, `ROOMMATE_ONE_SIDED`. A shared JSON fixture
   runs through both engines and asserts identical code sets. Add a `RuleWaiver` table so a planner
   acknowledges "yes, this couple shares a mixed-gender room" once instead of living with a
   permanent warning.
3. **Name matching.** `admin_lodging.html:1550,1562-1569` uses a bidirectional `includes()`, so
   `"Bob"` matches `"Bobby Jones"` and `find()` silently picks the first of two Joneses. Replace
   with normalized exact → last-name + first-initial → token-set, returning `AMBIGUOUS` with
   candidates. Roommate reciprocity compares resolved `person_key`s, not strings.

**Keep SQLite as the default here.** Lodging is a pre-event batch activity with a handful of
planners and single-digit writes per minute. The reason to reach for Postgres is concurrent-write
safety, and that's solved by `row_version`, not by the engine. Test Postgres in CI and document
switching at >5 simultaneous planners or when PITR is needed.

*This rules engine is the highest-risk item in the whole extraction* — it's the only place a silent
logic change harms attendees (wrong roommate, wrong gender room) rather than annoying staff. Port it
test-first, before the board UI, with the parity fixture built from real anonymized 2026 assignments
so the rewrite is provably behavior-preserving.

### `nametag-press` (from `posted`)

ReportLab badge generation, three Avery templates, auto-shrinking name/affiliation text, role→label
+colour from the profile, logo upload, blank sheets.

- **Drop the browser-print path.** Geometry and card content are defined twice — ReportLab
  (`main.py:937-976`, `:1051-1118`) and print CSS/JS (`admin_nametags.html:213-330`, `:947-983`,
  `:1203-1214`) — and the CSS version can't reproduce per-line autoshrink, so a long name prints
  differently in the two paths. That's the failure that ruins a sheet of Avery stock. One renderer,
  plus an in-browser PDF preview (`<iframe src="/api/badges.pdf#toolbar=0">`). `layouts.json` is
  generated from `layout.py` and asserted equal in CI so JS can still draw the selection grid
  without owning dimensions.
- Logos move into the DB as bytes. Uploads land in `frontend/static/images/` today
  (`main.py:342-370`), which is not the Azure Files mount, so they vanish on container restart.
- **Drop `t_shirt_size`.** Stored, backed up, never rendered. Two apps counting shirts is how you
  oversell mediums.

### `link-forge` (from `posted/frontend/admin_reimbursement.html`)

**Stateless, no database, own repo.** Not folded into `ticket-reconciler`: the audience is event and
finance staff who shouldn't get an authorization that also exposes gross revenue, and reimbursement
links get used for weeks after the reconciler is torn down. Not a static page either, because the
tokenized speaker links are bearer credentials that must not be committed — the leaked
`links-for-speakers.html` is exactly that mistake.

Link kinds come from `profile.links`: reimbursement (the entire current feature is the one URL
template at `admin_reimbursement.html:289`), **DocuSign PowerForm media release** (a PowerForm URL
with event specifics as URL params, per `registration-receipt-email-body.html`), speaker slide
upload, and tokenized Drupal webform prefill.

**Fragment vs query, made explicit and enforced.** `#`-fragment params never reach a server — not
access logs, not Referer, not a CDN. `?`-query params land in Drupal's webserver log, App Service
logs, and any proxy between. Each kind declares `param_style: fragment|query` and
`sensitivity: low|pii|bearer`; a `bearer` kind with `fragment` is refused, and a `query`+`pii` kind
renders a warning banner — the slide-upload link puts participant email in log lines today.
link-forge logs `kind` plus a SHA-256 prefix, never a rendered URL, with a test asserting no `@`
reaches caplog.

### `poster-gallery` (from `posted`)

Public directory with MathJax 3, `?presenter=` single view, RSS 2.0, poster webhook, bulk import.

- **PII fix:** unauthenticated `GET /api/presenters` returns `email_address`, `drupal_sid`, and
  `serial_number` for every presenter (`schemas.py:78-92` + `main.py:126-133`). Split
  `PublicPresenter` from `AdminPresenter`, with a trip-wire test that fails if the public model ever
  gains a field outside an explicit allowlist — the original bug is one careless `response_model`
  reuse.
- **Delete `download_assets.py` entirely.** 58 lines fetching six CSS files live from the Drupal
  site on every app start and every test run, with a hardcoded `sites/g/files/toruqf4381` path,
  content-hashed filenames that rot on any upstream edit, a spoofed Chrome User-Agent, and the
  Cloudflare bypass header — writing the responses into a publicly-served mount. Replace with
  Paper Tiger tokens plus a committed `host-theme.css` an adopter writes once by setting ~20 custom
  properties, and a documented devtools recipe for extracting them. "Look like our Drupal site" is
  a design-token problem, not a build-time mirror of a CMS you don't control.

### `drupal-event-forms`

Webform exports, Remote Post recipes (with the `headers:`-nesting gotcha spelled out and **no token
values**), the receipt email with its Speaker-only tokenized links, the group router, the travel
form, the DocuSign PowerForm recipe, and `contracts/*.fieldmap.yml`.

- **Import path:** `drush webform:import` imports *submissions*, not definitions. Document the two
  real paths — `drush cim --partial` with files renamed to `webform.webform.<id>.yml` and wrapped in
  a config envelope (`tools/` provides the wrapper, since these exports are element-only bodies), or
  the Webform UI → Build → Source paste, which is what CAARMS actually did and which the docs should
  lead with. Required contrib: Webform, CAS, Captcha, `computed_twig`.
- **FieldMap sync contract**, three layers: `check_fieldmap_sync.py` asserts every contract key
  exists in the referenced webform YAML; each app's CI asserts its `webform-schema.yml` matches the
  contract at a pinned tag; at runtime `eventkit.drupal` logs `unmapped_keys` and the app exposes
  them, so a live Drupal element rename shows up as a warning within one submission rather than as
  silently dropped registrations.
- **The travel form collects passport number, country of issue, expiry, DOB, and gender, and emails
  them in plaintext HTML to an agency.** Publishing the *structure* is fine and useful; the repo must
  not imply the *pattern* is safe. Ship it with `#results_disabled` guidance, mandatory purge-after-N
  days, restricted results access, and a "prefer the agency's own secure portal" banner.
- Two bugs to fix and regression-assert: `registration.yaml`'s `actions` element has a doubly-nested
  `'#states': {'#states': {…}}`, so its "disable submit for an un-logged-in Princeton affiliate"
  rule never fires — PU affiliates could and did submit registrations that invalidate their
  exemption; and `morgan-state-…-form.yaml`'s `chair_notice` compares against Eventbrite event id
  `1986024760521` while the actual option key is `1993012012580`, so the chair never sees the
  "purchase on behalf of the group" instruction. Also reconcile the student-count discrepancy
  (8 in the form copy, 14 in `administrative-utilities.html`).

### `event-stack`

`ARCHITECTURE.md` with committed Mermaid: Drupal on top with five labeled Remote Post arrows
fanning out; five app boxes each over its **own** DB cylinder (visually separate — that's the
point), all on a shared `eventkit` bar, `link-forge` with no cylinder; Eventbrite bidirectional to
`ticket-reconciler` only; DocuSign dashed (link-only, no API); plus a T-8-weeks → T+2-weeks
timeline swimlane.

`RUNBOOK.md` follows what staff actually did: registration opens → conditional Eventbrite purchase
with `Pending` chased weekly → rooms bulk-created at T-3w, assignments final at T-5d with the rules
panel clean or waived → nametags printed the day prior (blank-sheet calibration first, spares for
walk-ins) → front-desk check-in per day key with swag issuance and on-site waivers → post-event
reimbursement and media-release links, refund overrides, gallery stays up, backups pulled, apps torn
down except `link-forge` and `poster-gallery`. Each phase names the exact route and a "how you know
it worked" check.

`SECURITY-PRIVACY.md` with a field → app → sensitivity → retention inventory: emails everywhere;
gender identity and roommate requests (sensitive, delete at T+30d); dietary restrictions; passport
data (Drupal only, never in an app DB); payment amounts (no card data ever touches these apps).
Plus token rotation, the fragment-vs-query rule, "a backup download is a full PII export", and the
real cost of independent DBs named out loud: **a deletion request means a pass over five databases.**

Also document a single-container all-in-one compose deployment as a first-class option — five Azure
Web Apps for a one-week event is real money and five things to patch. Per-app repos are right;
per-app *hosting* shouldn't be mandatory.

---

## Security fixes that must land in the extraction

| Issue | Where | Fix |
|---|---|---|
| `POST /api/admin/clear` has **no auth dependency** — an anonymous caller who knows the path can post `{"target":"both","confirm":"DESTROY"}` and wipe every registration and payment. Deliberately open so `clear_data.yml` can `curl` it. | `ticketed/backend/main.py:1087-1091` | Delete the route. The workflow OIDC-federates into Azure and runs a one-shot container command instead; HMAC task token as fallback. Audit row for every destructive op. |
| `WEBSOCKET /ws/checkin` unauthenticated | `ticketed/backend/main.py:695` | HMAC ticket from an authed endpoint, `Origin` check — and polling becomes the default anyway |
| Webhook token + all headers logged at INFO | `ticketed/backend/main.py:285-286` | Delete; log `outcome`/`reason`/`fp=sha256[:6]`. `SecretStr` everywhere + a `RedactFilter` so a future `logger.info(settings)` can't leak. |
| `"secret_drupal_token"` / `"secret_nametags_token"` as **defaults** — an adopter who forgets the app setting deploys a publicly-documented token | `ticketed/config.py:22`, `posted/config.py:23-24` | Required, no default; `assert_strong()`; `compare_digest`; `openssl rand -hex 32` in the toolkit |
| Seven real netIDs in committed config; `X-Mock-Admin-Principal` trusted whenever `ALLOW_LOCAL_DEV_ADMIN=true` — one mis-set app setting grants full admin to anyone sending a header | `posted/config.py:25`, `posted/main.py:223-224` | Empty allow-list ⇒ **deny all**. Delete the mock header. Dev principal off unless explicitly set, and refused on Azure. |
| Easy Auth headers trusted with no corroboration — if the container is ever reachable other than through the front door, one header is a complete auth bypass | `ticketed/main.py:204-238`, `posted/main.py:218-231` | Also require and decode `X-MS-CLIENT-PRINCIPAL`, cross-check the claim against the name header. CRITICAL log if `WEBSITE_SITE_NAME` is set without `WEBSITE_AUTH_ENABLED`. |
| QR modal sends attendee purchase URLs — **including the live discount code** — to `api.qrserver.com` on every render, and the front desk stops working on captive-portal wifi | `ticketed/frontend/app.js:1237` | Vendor a ~4 KB MIT QR encoder, render SVG locally |
| `enable_restore: bool = True` in both repos — a table-truncating endpoint on by default | both `config.py` | Default `False`; principal + flag + confirm phrase + pre-restore snapshot + full payload validation before the first DELETE |
| Unauthenticated `GET /api/presenters` leaks every presenter's email | `posted/schemas.py:78-92` | Public/admin schema split + allowlist trip-wire test |
| Runtime writes of remote content into a publicly-served mount, past a WAF, with the bypass value committed | `posted/download_assets.py` | Build-time CLI only; bypass header from env with no default. **Get a real shared secret from WDS or drop the mechanism** — `x-waf-bypass: true` in a public repo is a bypass with no secret in it. |
| Runtime image ships `tests/`, runs as root, no healthcheck, carries `build-essential` | both `Dockerfile`s | Multi-stage with a `test` target |

---

## Testing

Every repo: `pytest` + `vitest` in one `docker compose run --rm test`, same command locally and in
CI, matrixed over SQLite and Postgres. `eventkit.testing` exports the fixtures so each app's
`conftest.py` is one line — the precondition being `get_settings()` with `@lru_cache` and
`create_all`/migrations moved into `lifespan`, which is what forces today's
env-vars-before-import dance.

The autouse `_no_network` fixture is the single highest-value fixture: `posted`'s suite currently
makes real HTTP calls to caarms.princeton.edu on every run.

Priority coverage that doesn't exist today: `build_report`'s status truth table and the
manual-link-must-not-double-claim guard; `aggregate_by_email` (paid beats refunded, multi-paid
summing, latest-wins identity, no-email skip); the lodging rules engine via a shared
Python/JS parity fixture; Avery layout geometry (`margin*2 + cols*w + gaps <= 8.5in` for every
template) and `fit_text` monotonicity; the `checkin_status` key migration; the public-schema
trip-wire; `shellcheck` + `bats` with a mock `az` for the whole deploy flow.

---

## Verification

1. `docker compose run --rm test` green in `eventkit` and each app repo; coverage gate met.
2. Phase 1 proof: old `ticketed` and `posted`, running on `eventkit`, deployed to Azure and
   reconciling real Drupal traffic — before any repo is split.
3. `eventkit azure deploy` on a scratch subscription from zero to a working app, exercising at
   least one manual gate; then interrupt at the gate and confirm `resume` continues correctly;
   then `--dry-run` and `--yes` paths; then `teardown` leaves nothing behind.
4. `event-stack/scripts/verify-stack.sh`: `/healthz` on each app, a synthetic webform submission to
   each webhook asserting a row appears, anonymous access denied on admin routes, and no `@` in
   any public JSON response.
5. Parallel-run cutover per app behind duplicate Remote Post handlers, diffed nightly, before the
   old handler is removed.
6. A full dry-run of the runbook against the sanitized CAARMS profile on a scratch event: register
   → conditional purchase → assign rooms → print a badge sheet → check someone in → issue a
   reimbursement link.

## Flags I'm carrying forward

- **Identity across five databases is the main risk in the chosen architecture.** The same person is
  a row in four DBs. Mitigations in the plan: `person_key` preferring the Drupal uuid (frozen and
  versioned), an `identity-drift` CLI, and a cross-app resolver in `event-stack/scripts/`. Accepted
  cost: deletion requests are a five-database operation.
- **Five Remote Post handlers on one webform is synchronous coupling.** Configure all handlers for
  the **Completed** state only with errors ignored and a short timeout; every webhook must be
  idempotent, return 200 in ~200 ms, and defer slow work. Give each app a replay route over stored
  raw payloads — today if `posted` is down for an hour those submissions are simply lost.
- **Normalize the three-valued strings now.** `attendee_status`, `student`, `lodging`, and
  `presenting_poster` are `String` columns holding `"Yes"`/`"yes"`/`None`, and queries read
  `(lodging == "Yes") | (lodging == "yes")` (`posted/main.py:607-609`). The extraction is the only
  cheap moment; otherwise all five apps inherit it.
- **Discount codes are semi-public by nature** — they're in the Drupal twig the browser receives.
  Keeping them in App Service settings and out of git is still right, but don't document them as
  secrets.
- `eventkit` version skew: bump all app pins in one PR wave per release so nobody debugs a version
  mismatch during a conference.

---
---

# APPENDIX — full design detail

Everything below is the durable record of the design work behind the plan above. Signatures are
targets, not gospel; where an appendix detail conflicts with the plan body, the plan body wins.

## A. `eventkit` repo layout and packaging

```
eventkit/
├── pyproject.toml                      # src layout, hatchling, extras
├── README.md  CHANGELOG.md  LICENSE
├── Dockerfile                          # test/dev image ONLY (multi-stage, `test` target)
├── docker-compose.yml                  # `docker compose run --rm test`
├── run_tests.sh                        # py + js in one container
├── src/eventkit/
│   ├── __init__.py                     # __version__
│   ├── settings.py                     # BaseAppSettings + get_settings() lazy cache
│   ├── logging.py                      # configure_logging(), RedactFilter
│   ├── identity.py                     # normalize_email, person_key, IdentityMixin
│   ├── errors.py
│   ├── drupal/{coerce,schema,parse}.py
│   ├── auth/{easyauth,denied,wsticket}.py + templates/access_denied.html.j2
│   ├── webhook.py
│   ├── db/{__init__,urls,migrate}.py + alembic_template/
│   ├── backup.py
│   ├── eventbrite/{client,aggregate,sync,models}.py
│   ├── notify/{__init__,render}.py + transports/{log,smtp,resend,acs,memory}.py
│   │                                  + templates/*.html.j2
│   ├── eventprofile/{models,load,public,routes,checkin}.py
│   ├── importer.py
│   ├── realtime.py
│   ├── admin.py                        # destructive-op task tokens
│   ├── mirror.py                       # opt-in Drupal asset mirroring (CLI only)
│   ├── cli.py                          # `eventkit` console script
│   ├── testing/{plugin,factories}.py + fixtures/drupal/*.json
│   ├── azure/                          # the zsh toolkit, as package data
│   │   ├── lib/{boot,color,log,prompt,state,name,az,gh,secrets,manual,verify}.zsh
│   │   ├── {bootstrap,deploy,update,teardown,setup-oidc,scale-guard}.sh
│   │   └── templates/{app.env.example,Dockerfile.app,compose.app.yml,workflows/*.yml}
│   └── ui/
│       ├── __init__.py                 # static_path(), theme_path(), render_theme_vars(), vendor()
│       └── static/
│           ├── tokens/{base.css,tokens.json,fonts.css}
│           ├── css/{layout,buttons,forms,cards,table,toast,modal,chrome,badge}.css
│           ├── js/{esc,fetchx,toast,table,filters,chrome,backup-panel,
│           │       eventprofile,checkin,lodging-rules,qr}.js
│           ├── themes/neutral/{theme.css,theme.json}
│           └── themes/princeton-orfe/{theme.css,theme.json,assets/logos/*}
├── examples/caarms-2026/{event-profile.yaml,webform-schema.yml,theme.override.css,README.md}
├── docs/{quickstart,consuming,event-profile,migrations,security,ui-kit,testing}.md
└── tests/{conftest.py,unit/**,integration/**,js/*.test.js}
```

`src/` layout is deliberate: it makes "tests import the installed package, not the working tree"
free, which is the bug class that forced today's `PYTHONPATH=.` and env-vars-before-import dance.

### `pyproject.toml` — the load-bearing parts

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "eventkit"          # CHECK PyPI AVAILABILITY — likely taken; prefix if so
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["pydantic>=2.7,<3", "PyYAML>=6", "Jinja2>=3.1"]

[project.optional-dependencies]
web      = ["fastapi>=0.111,<1", "python-multipart>=0.0.9"]
db       = ["SQLAlchemy>=2.0,<3", "alembic>=1.13", "filelock>=3.13"]
http     = ["httpx>=0.27,<1"]
resend   = ["resend>=0.8"]
acs      = ["azure-communication-email>=1.0"]
postgres = ["psycopg[binary]>=3.1"]
app      = ["eventkit[web,db,http]"]          # what the 5 apps install
test     = ["eventkit[app,resend]", "pytest>=8.2", "pytest-asyncio>=0.23",
            "respx>=0.21", "freezegun>=1.5"]

[project.entry-points.pytest11]
eventkit = "eventkit.testing.plugin"

[project.scripts]
eventkit = "eventkit.cli:main"

[tool.hatch.build.targets.wheel]
packages = ["src/eventkit"]
artifacts = ["src/eventkit/ui/static/**", "src/eventkit/azure/**",
             "src/eventkit/**/templates/**", "src/eventkit/db/alembic_template/**"]

[tool.pytest.ini_options]
addopts = "-q --strict-markers"
testpaths = ["tests"]
asyncio_mode = "auto"
```

Extras split matters: `nametag-press` needs `[web,db]` but not `http`; `link-forge` needs only the
base. **Hard constraint: `eventkit.eventprofile` and `eventkit.ui` import with zero third-party
deps beyond pydantic/PyYAML.**

Three distributions from this one repo (`eventkit-core`, `eventkit-ui`, `eventkit-azure`, plus an
`eventkit` meta-package) so an operator's `pipx` install doesn't drag FastAPI and ReportLab onto
their laptop, and a CSS fix doesn't force five apps to bump a runtime dependency. Documented rule:
**patch releases may change `ui/static/**` and nothing else.**

### Consumption

Do **not** use `pip install git+https://…` — `python:3.11-slim` has no `git`, so every app
Dockerfile would need `apt-get install git`. Use the codeload tarball, a plain HTTPS fetch:

```
# ticket-reconciler/requirements.txt
eventkit-core[app] @ https://github.com/pu-sherrerd/eventkit/archive/refs/tags/v0.1.0.tar.gz
```

Public repos ⇒ no auth. Enforce annotated tags only + tag protection, since a moved tag is remote
code execution with Azure credentials in the environment. `pip hash` + `--require-hashes` documented
but not required at v0.1. PyPI + Sigstore at v0.2; CI pins by commit SHA, not tag.

**UI kit into a container, Mode A (default, zero build step):**

```python
from eventkit.ui import static_path
app.mount("/ui", StaticFiles(directory=static_path()), name="ui")
```

`static_path()` is `importlib.resources.files("eventkit.ui") / "static"`. Assert in a test that it
resolves to a real directory in an *installed wheel*. One mount prefix removes today's failure mode
where paper-tiger demos reference `/assets/logos/...` while the app mounts `/paper-tiger/assets/...`
and nothing resolves.

**Mode B (cache headers / CDN):** `python -m eventkit.cli ui vendor --dest … --theme …` copies
static + theme, writes `manifest.json` with per-file sha256, optionally rewrites to
`name.<hash8>.css`.

**Azure toolkit:** shell runs *before* the app exists, so it can't use the app's venv.
`eventkit azure <script> [args…]` `os.execvp`s zsh on the packaged script with `EVENTKIT_AZURE_LIB`
exported. `eventkit azure eject --dest ./deploy` materializes the scripts for an app that must
diverge.

## B. `eventkit` module API surface

### B.1 `eventkit.drupal`

Three near-identical parsers today: `posted/backend/schemas.py:16-76`, `posted/backend/schemas.py:111-193`
(~85% duplicated), and `ticketed/backend/schemas.py:17-68` + `schema_parser.py:159-245`. They
disagree: only the nametags one handles `select_other`; only ticketed handles `destination_url` and
truthy coercion; only ticketed lowercases email (in a separate `field_validator`).

**Layer 1 — pure coercion primitives** (`coerce.py`), each total, no logging, no config:

```python
def unwrap(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Return (root, data_block). Drupal Remote Post wraps fields in `data`;
    if `data` is absent or not a dict, data_block is root."""

def coerce_email(value: Any) -> str | None:
    """str -> stripped/lowered. dict -> mail_1 | email | value | mail (composite
    webform_email_confirm). list -> first non-empty. Anything else -> None."""

class Name(NamedTuple):
    first: str | None
    last: str | None

def coerce_name(value: Any) -> Name:
    """dict -> (first|first_name|given, last|last_name|family|surname).
    str -> split_full_name. None/other -> (None, None)."""

def split_full_name(value: str) -> Name:
    """'Ada Lovelace' -> ('Ada','Lovelace'); 'Ada' -> ('Ada', None);
    'Ada B Lovelace' -> ('Ada','B Lovelace')  # split(None, 1), matches today"""

def coerce_select_other(value: Any) -> str | None:
    """dict {'select','other'} -> other if select in (None,'','_other_') else select.
    str -> str. Handles Drupal webform_select_other."""

TRUTHY: frozenset[str] = frozenset({"1", "true", "yes", "on", "checked", "y", "t"})
def coerce_bool(value: Any) -> bool: ...
def coerce_int(value: Any) -> int | None: ...          # "" -> None, "12" -> 12
def coerce_multivalue(value: Any) -> list[str]: ...    # checkboxes: dict/list/str -> list
```

**Layer 2 — configurable field mapping** (`schema.py`):

```python
class FieldRule(BaseModel):
    key: str | list[str]                     # webform element key(s), tried in order
    kind: Literal["text","email","name","bool","int","select","select_other",
                  "multiselect","url"] = "text"
    required: bool = False
    default: Any = None

class FieldMap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fields: dict[str, FieldRule]
    def logical_keys(self) -> set[str]: ...

class WebformSchema(BaseModel):
    elements: dict[str, dict[str, Any]]
    @classmethod
    def from_yaml_text(cls, text: str) -> "WebformSchema": ...
    @classmethod
    def from_path(cls, path: Path) -> "WebformSchema": ...
    def infer_field_map(self, *, want: Iterable[str]) -> tuple[FieldMap, list[str]]:
        """Best-effort inference (the useful half of schema_parser.get_field_mappings).
        Returns (map, warnings). Never guesses silently: every inference that used a
        heuristic rather than an exact key match produces a warning string."""
```

**The `webform-schema.yml` mechanism is redesigned.** Today `schema_parser.load_schema()` looks for
the file in repo root then CWD, and since none ships, the embedded CAARMS `DEFAULT_SCHEMA_YAML`
*always* wins — every adopter silently runs the CAARMS field map. Replacement:

1. No embedded default, ever.
2. Resolution order, explicit and logged once at startup: `profile.drupal.field_map` (authoritative)
   → `profile.drupal.webform_schema` path → `infer_field_map()` with warnings at WARNING → neither
   ⇒ **raise `EventProfileError` at startup**, naming the missing logical fields and printing a
   copy-pasteable YAML stub.

Fail-fast is right: a wrong field map silently drops registrations, which is worse than not booting.

**Layer 3 — canonical submission model** (`parse.py`):

```python
class WebformSubmission(BaseModel):
    sid: int | None = None
    serial: int | None = None
    uuid: str | None = None            # carry this — it's the identity fix
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    fields: dict[str, Any] = {}        # logical name -> coerced value
    raw: dict[str, Any] = Field(default_factory=dict, repr=False, exclude=True)
    @property
    def full_name(self) -> str: ...
    def get(self, name: str, default: Any = None) -> Any: ...
    @property
    def person_key(self) -> str | None: ...

def parse_submission(payload: Mapping[str, Any], field_map: FieldMap) -> WebformSubmission:
    """The ONE parser. Replaces all three validators."""

class DrupalSubmissionModel(BaseModel):
    """Optional base for apps wanting a typed payload model. Subclass, declare typed
    fields, set __field_map__ (or leave None for the ambient profile); the
    mode='before' validator is inherited."""
    __field_map__: ClassVar[FieldMap | None] = None
    @model_validator(mode="before")
    @classmethod
    def _parse(cls, values: Any) -> Any: ...
```

**Invariant to enforce with a test:** `parse_submission` is the only entry point for both the webhook
and the bulk importer. `posted/backend/import_existing.py:92` already has this property by
convention; make it true by construction.

### B.2 `eventkit.identity`

```python
def normalize_email(raw: str | None) -> str | None: ...   # strip, lower, NFKC

def person_key(*, uuid: str | None, email: str | None) -> str:
    """Prefer the Drupal submission uuid; else sha256('email:'+normalized)[:32].
    Stable across email corrections when uuid is present.
    FROZEN AND VERSIONED — changing this silently orphans every row in every app."""

class IdentityMixin:
    """Mixin for every app's person-shaped model: person_key (unique, indexed),
    drupal_uuid, drupal_sid, serial_number, email_address, first_name, last_name."""

def diff_populations(a, b) -> PopulationDiff: ...
```

The CAARMS webform already emits `uuid` (`schema_parser.py:28-31`) and nobody uses it. Exposed per
app as `<app> identity-drift --against <other-backup.json>` so a planner can see "3 people in
lodging are not in nametags" *before* badges are printed.

### B.3 `eventkit.auth`

```python
@dataclass(frozen=True, slots=True)
class Principal:
    email: str
    display_name: str | None = None
    provider: str | None = None            # X-MS-CLIENT-PRINCIPAL-IDP
    id: str | None = None                  # X-MS-CLIENT-PRINCIPAL-ID
    claims: Mapping[str, Any] = field(default_factory=dict)   # decoded X-MS-CLIENT-PRINCIPAL

class AllowList:
    def __init__(self, entries: Iterable[str]): ...
    @classmethod
    def parse(cls, csv: str) -> "AllowList": ...     # comma-separated, lowercased, stripped
    def allows(self, email: str) -> bool: ...        # exact email OR "@domain.tld" suffix rule
    def __bool__(self) -> bool: ...

class EasyAuth:
    def __init__(self, allow_list: AllowList, *,
                 dev_principal: str | None = None,        # NO default; None disables bypass
                 login_path: str = "/.auth/login/aad",
                 logout_path: str = "/.auth/logout",
                 page_paths: Collection[str] = ("/",),    # redirect instead of 401
                 theme: "DeniedTheme | None" = None,
                 require_claims_header: bool = True): ...
    def dependency(self) -> Callable[..., Principal]: ...
    @property
    def require(self) -> Callable[..., Principal]: ...
    def optional(self) -> Callable[..., Principal | None]: ...

def install(app: FastAPI, auth: EasyAuth) -> None:
    """Registers RedirectToLogin / NotAuthorized exception handlers."""

class RedirectToLogin(Exception): post_login_redirect_url: str
class NotAuthorized(Exception):   email: str

class DeniedTheme(BaseModel):
    app_title: str
    brand_color: str = "#e77500"
    logo_url: str | None = None
    support_contact: str | None = None
    logout_url: str = "/.auth/logout?post_logout_redirect_uri=/"
    @classmethod
    def from_profile(cls, profile: "EventProfile") -> "DeniedTheme": ...

def render_access_denied(email: str, theme: DeniedTheme) -> str:
    """Jinja render of templates/access_denied.html.j2 (autoescaped)."""

# WebSocket tickets — HMAC-SHA256 over scope|email|exp, constant-time compare,
# no server-side store, so it survives restarts and multiple instances.
def issue_ws_ticket(principal: Principal, *, secret: str, ttl_s: int = 60,
                    scope: str = "checkin") -> str: ...
def verify_ws_ticket(ticket: str, *, secret: str, scope: str = "checkin") -> Principal: ...
def ws_dependency(auth: EasyAuth, *, secret: str, scope: str) -> Callable[..., Principal]: ...
```

Two hardening rules baked in:

```python
# in EasyAuth.__init__
if dev_principal and os.getenv("WEBSITE_SITE_NAME"):
    raise ConfigError(
        "dev_principal is set but WEBSITE_SITE_NAME indicates Azure App Service. "
        "Refusing to start with the auth bypass enabled in a hosted environment.")
```

and `require_claims_header=True` demands the base64 claims blob `X-MS-CLIENT-PRINCIPAL`, not just
`X-MS-CLIENT-PRINCIPAL-NAME`. Today one spoofable header is the whole authentication
(`ticketed/backend/main.py:204-238`, `posted/backend/main.py:218-231`).

This deletes ~90 lines of inline HTML from `ticketed/backend/main.py:106-201` and replaces `posted`'s
**18 imperative `if not is_admin_authorized(request):` call sites**. The imperative pattern is the
security-bug generator: a new handler that forgets the line is silently public, whereas a `Depends`
on `APIRouter(dependencies=[...])` cannot be forgotten.

### B.4 `eventkit.webhook`

```python
class WebhookTokens:
    def __init__(self, tokens: Mapping[str, SecretStr], *,
                 header: str = "X-Drupal-Webhook-Token"): ...
    @classmethod
    def from_settings(cls, **named: SecretStr) -> "WebhookTokens": ...
    def dependency(self, name: str) -> Callable[..., str]:
        """FastAPI dependency verifying the header via hmac.compare_digest."""

WEAK_TOKENS: frozenset[str] = frozenset({
    "secret_drupal_token", "secret_nametags_token", "changeme", "test", "token"})

def assert_strong(token: SecretStr, *, name: str, min_len: int = 24) -> None:
    """Raise ConfigError if weak or short. Called at startup, including on Azure."""

def deferred(fn): ...   # run slow work (notify, Eventbrite) after the 200 returns
```

Usage: `@router.post("/api/drupal-webhook", dependencies=[Depends(tokens.dependency("registration"))])`.

Logs **only** `webhook.verify name=registration outcome=deny reason=mismatch fp=3f9a21`, where `fp`
is `sha256(presented)[:6]`. Never the token, never `dict(request.headers)`.

Roadmap: HMAC-over-body + timestamp + nonce, accepting the bare token for one release with a
deprecation warning. A shared-secret header with no signature means any Drupal admin, any log
containing headers, or any misconfigured proxy has full write access — including overwriting a paid
registrant's email.

### B.5 `eventkit.db` and the migration decision

```python
class Database:
    def __init__(self, url: str, *, echo: bool = False,
                 sqlite_pragmas: Mapping[str, Any] | None = None,
                 pool_pre_ping: bool = True): ...
    engine: Engine
    session_factory: sessionmaker[Session]
    def get_db(self) -> Iterator[Session]: ...            # FastAPI dependency
    @contextmanager
    def session(self) -> Iterator[Session]: ...           # scripts / background tasks
    @property
    def is_sqlite(self) -> bool: ...
    def sqlite_file(self) -> Path | None: ...

def declarative_base(*, naming_convention: bool = True) -> type[DeclarativeBase]:
    """Base with an explicit naming_convention — REQUIRED for Alembic + SQLite batch
    mode to name/drop constraints. Today's bare declarative_base() cannot."""

def sqlite_url_for(app_name: str, *, home: str = "/home") -> str: ...

AZURE_FILES_PRAGMAS: Final[dict[str, Any]] = {
    "journal_mode": "TRUNCATE",   # NOT wal — SMB has no shared-memory mmap
    "synchronous": "FULL",        # SMB reorders writes
    "busy_timeout": 15000,        # SMB latency makes the 5s default fire constantly
    "foreign_keys": "ON",
}
# plus NullPool / pool_size=1 so one process doesn't hold multiple SMB file handles
```

**Adopt Alembic; retire the hand-rolled migrator.** Reasoning specific to this codebase:

1. **It fails silently.** `ticketed/backend/database.py` wraps every `ALTER TABLE` in a `try/except`
   that ends at `logger.error(...)` and continues. If the ALTER genuinely fails (locked file on
   Azure Files, typo'd type), the app boots believing the column exists and the first write raises a
   500 in the webhook path — a dropped registration. There is no version row, so nobody can answer
   "what schema is production actually on?"
2. **It can only add columns.** Two changes the extraction needs can't be expressed: rewriting
   `checkin_status` JSON keys `"6/28"` → `"2026-06-28"` (a data migration), and adding
   `uuid`/`person_key` *with a backfill*.
3. **Cost is 341 lines today and grows linearly with columns, per app.** Five apps ⇒ five copies.
   Alembic is ~15 lines of `env.py` (shipped by eventkit) plus one small file per change, authored by
   the app developer, never the adopter.
4. **The adopter-experience objection doesn't survive contact.** Event planners never run
   `alembic revision`; they run `deploy`. Migrations execute in-container at startup. The only new
   artifact they see is a `migrations/` directory they never open.
5. **SQLite + Alembic is fine** given `render_as_batch=True` and a naming convention — both shipped
   by the template, so no app gets it wrong.

```python
# eventkit/db/migrate.py
def init_migrations(app_dir: Path, *, package: str) -> None:
    """`eventkit db init` — copies alembic_template/, writes alembic.ini,
    wires env.py to the app's Base.metadata."""

def upgrade_to_head(db: Database, *, migrations_dir: Path,
                    lock_timeout_s: int = 60, backup_first: bool = True) -> str:
    """Acquire a filelock next to the DB (or a Postgres advisory lock), snapshot the
    SQLite file to <db>.pre-<rev>.bak, run `alembic upgrade head`, return the new
    revision. Raises on failure — never swallows."""

def current_revision(db: Database) -> str | None: ...
def assert_at_head(db: Database, *, migrations_dir: Path) -> None: ...   # CI --check / readiness
def stamp(db: Database, revision: str, *, migrations_dir: Path) -> None:
    """One-time: adopt the two EXISTING live databases without re-running DDL."""

def lifespan_migrations(db: Database, *, migrations_dir: Path,
                        mode: Literal["upgrade","check","off"] = "upgrade"
                        ) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """Drop-in lifespan factory. Replaces Base.metadata.create_all() + run_migrations()
    at module import time (which is what forces today's conftest env dance)."""
```

Two supporting decisions:

- **`create_all()` moves out of module import.** `ticketed/backend/main.py:28-29` and
  `initialize_shirt_inventory()` at `:45` run at import; that's why `tests/conftest.py` must set env
  vars before `import backend.main`. Everything DB-touching moves into `lifespan`; `Settings` becomes
  `get_settings()` with `@lru_cache`. This one change deletes the conftest preamble in all 5 apps.
- **Multi-instance:** for SQLite the answer is one instance, enforced by `scale-guard`. The filelock
  is belt-and-braces for the container overlap App Service creates at every deploy and slot swap.

One legitimate concession: `ensure_columns(engine, table, {name: type})` ships as a **documented
hotfix-only escape hatch**, raising on failure and logging a loud "this bypasses Alembic; add a
revision" warning — so nobody re-invents the 240-line version at 2am during a conference.

### B.6 `eventkit.backup`

```python
class TableSpec(BaseModel):
    model: type[DeclarativeBase]
    key: str                                     # JSON key, e.g. "registrants"
    order: int = 0                               # insert order; delete order reversed
    redact: Callable[[dict], dict] | None = None
    seed_if_missing: Callable[[Session], None] | None = None   # e.g. swag inventory

class BackupSpec(BaseModel):
    app_name: str
    tables: list[TableSpec]
    filename_prefix: str = "backup"
    required_keys: set[str] = set()

class BackupManifest(BaseModel):
    app_name: str
    eventkit_version: str
    app_version: str
    alembic_revision: str | None
    created_at: datetime
    row_counts: dict[str, int]
    format_version: int = 1

def dump(session: Session, spec: BackupSpec, *,
         manifest_extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Column list from sqlalchemy.inspect(model).columns — no hand-written field
    lists (today ~55 lines per repo, silently stale when a column is added)."""

def restore(session: Session, spec: BackupSpec, payload: Mapping[str, Any], *,
            dry_run: bool = False) -> BackupManifest:
    """Validate the ENTIRE payload (schema + manifest compatibility) before any
    DELETE. Single transaction; delete in reverse `order`; insert in `order`."""

def make_backup_router(spec: BackupSpec, *,
    db: Callable[..., Session],                  # Depends(database.get_db)
    principal: Callable[..., Principal],         # Depends(auth.require)
    enable_restore: Callable[[], bool],
    prefix: str = "/api/admin",
    confirm_phrase: str = "RESTORE",
) -> APIRouter:
    """GET  {prefix}/db-backup   -> attachment JSON, Cache-Control: no-store
       POST {prefix}/db-restore  -> multipart file + form field `confirm`
       POST {prefix}/db-restore/validate -> dry run, returns manifest + diff summary"""
```

`enable_restore` defaults **False**. Restore auto-snapshots the SQLite file first, refuses a file
whose `manifest.app_name` differs, and requires `?force=1` when `alembic_revision` differs.

### B.7 `eventkit.eventbrite`

The valuable, currently-untestable core is the 80-line aggregation loop at
`ticketed/backend/eventbrite.py:78-160`. Extracted as a pure function it becomes table-driven.

```python
# models.py
class Attendee(BaseModel):
    id: str | None; order_id: str | None
    email: str; first_name: str | None; last_name: str | None
    status_raw: str
    gross_cents: int = 0; net_cents: int = 0
    created: datetime | None = None
    ticket_class_name: str | None = None
    @classmethod
    def from_api(cls, obj: Mapping[str, Any]) -> "Attendee | None": ...   # None if no email

class PaymentStatus(StrEnum):
    PAID = "paid"; REFUNDED = "refunded"; CANCELLED = "cancelled"; UNKNOWN = "unknown"

DEFAULT_STATUS_MAP: Final[Mapping[str, PaymentStatus]] = {
    "attending": PAID, "checked in": PAID, "registered": PAID, "placed": PAID,
    "cancelled": CANCELLED, "deleted": CANCELLED,
    "refunded": REFUNDED, "not attending": REFUNDED}

class AggregatedPayment(BaseModel):
    email: str; first_name: str | None; last_name: str | None
    order_id: str | None; attendee_id: str | None
    status: PaymentStatus
    paid_at: datetime
    gross_cents: int; net_cents: int
    attendee_count: int = 1

# aggregate.py — PURE: no I/O, no DB, no settings
def aggregate_by_email(attendees: Iterable[Attendee], *,
                       status_map: Mapping[str, PaymentStatus] = DEFAULT_STATUS_MAP,
                       now: datetime | None = None) -> dict[str, AggregatedPayment]:
    """paid beats refunded/cancelled; multiple paid sum gross/net; latest paid_at wins
    the identity fields. Exactly today's semantics, extracted verbatim + tested."""

# client.py
class EventbriteClient:
    def __init__(self, token: SecretStr, event_id: str, *,
                 base_url: str = "https://www.eventbriteapi.com/v3",
                 timeout: float = 30.0,
                 transport: httpx.AsyncBaseTransport | None = None,   # for respx
                 max_pages: int = 200): ...
    async def iter_attendees(self) -> AsyncIterator[Attendee]: ...
    async def fetch_attendees(self) -> list[Attendee]: ...
    def purchase_url(self, *, slug: str, discount_code: str | None) -> str: ...

# sync.py — the port boundary (today eventbrite.py imports notifications and
# writes SyncLog rows itself)
class SyncEvent(StrEnum):
    UNMATCHED_PAYMENT = "unmatched_payment"
    COMPLETED_PAYMENT = "completed_payment"
    PENDING_PAYMENT   = "pending_payment"
    EXEMPT_REGISTRATION = "exempt_registration"
    STATUS_CHANGED    = "status_changed"

class SyncPorts(Protocol):
    def load_existing_payments(self) -> Mapping[str, Any]: ...
    def load_registrant_index(self) -> Mapping[str, Any]: ...
    def upsert_payment(self, agg: AggregatedPayment) -> tuple[Any, bool]: ...  # (row, created)
    def record_sync(self, result: "SyncResult") -> None: ...
    async def emit(self, event: SyncEvent, ctx: Mapping[str, Any]) -> None: ...

class SyncResult(BaseModel):
    status: Literal["success","failed"]
    started_at: datetime; finished_at: datetime
    records_pulled: int = 0
    payments_created: int = 0
    payments_updated: int = 0
    error: str | None = None

async def run_sync(client: EventbriteClient, ports: SyncPorts, *,
                   status_map=DEFAULT_STATUS_MAP) -> SyncResult: ...

class SqlAlchemySyncPorts:
    """Batteries-included impl the reconciler uses; takes the app's Payment /
    Registrant / SyncLog classes + column names as constructor args."""
```

`run_sync` becomes testable with a fake `ports` and `respx`-mocked HTTP — zero DB, zero network.

### B.8 `eventkit.notify`

```python
class Message(BaseModel):
    to: list[str]; subject: str; html: str; text: str | None = None
    from_email: str | None = None; from_name: str | None = None
    reply_to: str | None = None; tags: dict[str, str] = {}

class Transport(Protocol):
    name: ClassVar[str]
    async def send(self, msg: Message) -> bool: ...

class LogTransport:      # DEFAULT. Renders and logs at INFO. Zero dependencies.
class MemoryTransport:   # tests. .outbox: list[Message]
class SmtpTransport:     # stdlib smtplib via anyio.to_thread — RECOMMENDED 2nd
class ResendTransport:   # extra [resend]
class AcsTransport:      # extra [acs]

def transport_from_settings(s: "NotifySettings") -> Transport:
    """'log'|'smtp'|'resend'|'acs'|'memory'. Falls back to log with a WARNING if the
    named transport's credentials are absent — never raises at startup."""

class NotifyPolicy(BaseModel):
    enabled: dict[str, bool] = {}
    recipients: dict[str, list[str]] = {}     # per-event override
    default_recipients: list[str] = []
    def wants(self, event: str) -> bool: ...
    def recipients_for(self, event: str) -> list[str]: ...

class Notifier:
    def __init__(self, transport, renderer, policy, *, from_email, from_name): ...
    async def notify(self, event: str, ctx: Mapping[str, Any]) -> bool: ...

class Renderer:
    """Jinja2 ChoiceLoader: adopter dir (/home/site/templates) -> profile dir ->
    eventkit package defaults. Autoescape on. Each event needs
    <event>.subject.txt.j2 and <event>.html.j2 (+ optional .txt.j2)."""
```

Shipped templates, extracted from `ticketed/backend/notifications.py:43-96` and de-CAARMSified:
`unmatched_payment`, `completed_payment`, `pending_payment`, `exempt_registration`, `sync_failed`.
Sender display name from `profile.branding.site_name`, not the hardcoded `"Drupal Reconciler"`.

**Second transport is SMTP, not ACS.** ACS Email needs a provisioned Communication Service, DNS
verification, and either an Azure-managed domain (senders look like `donotreply@<guid>.azurecomm.net`)
or DNS access the adopter may not have. Every university has an SMTP relay. Also fixed: today
`send_reconciliation_alert` is `async def` but calls blocking `resend.Emails.send`
(`notifications.py:132`), stalling the event loop mid-webhook.

### B.9 `eventkit.importer`

```python
class ImportOutcome(StrEnum):
    CREATED = "created"; UPDATED = "updated"; SKIPPED = "skipped"; INVALID = "invalid"

class ImportReport(BaseModel):
    total: int = 0
    counts: dict[ImportOutcome, int] = {}
    errors: list[tuple[int, str]] = []       # (record index, message)
    def exit_code(self) -> int: ...          # 0 ok, 1 had invalid, 2 fatal
    def render(self) -> str: ...

def iter_records(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    """.tar.gz/.tgz (JSON members) | directory (**/*.json) | .jsonl | .json (list, or
    dict-of-submissions keyed by sid/uuid) | .csv. Generalizes
    posted/backend/import_existing.py:14-77 and adds jsonl + csv."""

def run_import(path: Path, *,
    parse: Callable[[Mapping[str, Any]], Any],       # THE SAME fn the webhook uses
    upsert: Callable[[Session, Any], ImportOutcome],
    session_factory: Callable[[], Session],
    accept: Callable[[Any], bool] | None = None,     # e.g. presenting_poster truthy
    dry_run: bool = False, limit: int | None = None,
    fail_fast: bool = False, batch_size: int = 200,
    progress: Callable[[int, int], None] | None = None,
) -> ImportReport: ...

def add_import_arguments(parser: argparse.ArgumentParser) -> None:
    """--dry-run --limit --fail-fast --quiet; apps get a consistent CLI for free."""
```

Each app ships `python -m <app>.cli import <path>` in ~25 lines. `--dry-run` is the missing safety
feature today — `import_existing.py` commits or nothing, with no preview.

### B.10 `eventkit.mirror` — Drupal asset mirroring

`posted/backend/download_assets.py` is invoked from `lifespan` (`posted/backend/main.py:34-40`), so
**every app start and every test run makes 8 outbound requests to caarms.princeton.edu carrying a
Cloudflare bypass header and a spoofed Chrome UA**, writing responses into a publicly-served mount.

**Remove it from the runtime path entirely.**

```python
class MirrorAsset(BaseModel):
    name: str
    url_path: str | None = None                  # explicit path
    discover: Literal["link-css", "img-src"] | None = None   # or discover from a page
    max_bytes: int = 2_000_000
    expect_content_type: str | None = None

class MirrorSpec(BaseModel):
    target_host: HttpUrl
    bypass_header: tuple[str, str] | None = None     # from env; never committed
    user_agent: str = "eventkit-mirror/0.1 (+https://github.com/pu-sherrerd/eventkit)"
    assets: list[MirrorAsset]
    discover_from: list[str] = []                    # pages to scrape <link rel=stylesheet>

def mirror(spec: MirrorSpec, dest: Path, *, force: bool = False) -> "MirrorReport":
    """Content-type + size validated, atomic writes, manifest.json with sha256."""
```

CLI-only, run in the Docker build so the image is reproducible and startup is offline, plus a
scheduled Action that runs it and opens a PR when a hash changes. `discover: link-css` finds the
asset-injector CSS by parsing the page instead of hardcoding content hashes like
`align_header_text-bed7c47f…css` (`download_assets.py:17-19`), which break silently on any upstream
edit. Bypass header from `MIRROR_BYPASS_HEADER`/`MIRROR_BYPASS_VALUE` with **no defaults**. App
startup calls `eventkit.ui.assert_assets_present(dir)` and falls back to the shipped theme with one
WARNING, so the app is always renderable offline. Mirroring is opt-in; the default posture is "use
the shipped theme" — which also resolves the consent question of re-serving another site's CSS from
your origin, past its bot filter, on every boot.

## C. `eventkit.eventprofile` — the de-CAARMS-ification layer

The highest-leverage module: everything the 5 apps hardcode today reads from one validated YAML,
loaded once, exposed to the browser as public JSON.

### C.1 Pydantic models

```python
class EventInfo(BaseModel):
    name: str                              # full conference name
    short_name: str                        # "CAARMS"
    year: int
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,48}$")   # "caarms-2026"
    site_url: HttpUrl
    registration_form_url: HttpUrl
    contact_email: EmailStr | None = None
    @property
    def title(self) -> str: return f"{self.short_name} {self.year}"

class CheckinDay(BaseModel):
    key: str = Field(pattern=r"^[0-9a-z][0-9a-z\-]{2,32}$")   # NO slashes, NO bare "6/28"
    date: date | None = None
    label: str | None = None
    kind: Literal["day", "event"] = "day"
    icon: str | None = None
    @model_validator(mode="after")
    def _label(self): ...        # default label from date rendered in profile timezone

class Schedule(BaseModel):
    timezone: str = "America/New_York"       # validated via zoneinfo.ZoneInfo
    start_date: date
    end_date: date
    checkin_days: list[CheckinDay] = []
    @model_validator(mode="after")
    def _unique_keys_and_in_range(self): ...
    def tzinfo(self) -> ZoneInfo: ...

class SwagOption(BaseModel):
    key: str                                 # Drupal option key, e.g. "USML"
    label: str
    short: str | None = None                 # "S" — for the dense check-in table
    counts_toward_inventory: bool = True
    sort: int = 0

class Swag(BaseModel):
    enabled: bool = False
    kind: str = "t-shirt"
    drupal_field: str = "t_shirt_size"
    allow_replacement: bool = True
    options: list[SwagOption] = []
    def keys(self) -> list[str]: ...
    def label(self, key: str | None) -> str: ...
    def short(self, key: str | None) -> str: ...
    def inventory_keys(self) -> list[str]: ...

class Match(BaseModel):
    email_domain_suffix: list[str] = []
    field_equals: dict[str, str] = {}
    default: bool = False

class TicketTier(BaseModel):
    key: str
    label: str
    discount_code_env: str | None = None     # env var NAME, not the code itself
    price_cents: int | None = None
    match: Match = Match()

class Ticketing(BaseModel):
    vendor: Literal["eventbrite", "none"] = "eventbrite"
    exempt_field: str | None = "tickets_sold_separately"
    exempt_means: Literal["unchecked_is_exempt", "checked_is_exempt"] = "unchecked_is_exempt"
    event_url_template: str = "https://www.eventbrite.com/e/{slug}-tickets-{event_id}"
    slug: str | None = None                  # was hardcoded in an f-string
    prefer_destination_url_discount: bool = True
    tiers: list[TicketTier] = []
    status_order: list[str] = []             # replaces the hardcoded dict at main.py:568-576
    def resolve_tier(self, *, email: str, fields: Mapping[str, Any]) -> TicketTier | None: ...
    def purchase_url(self, *, event_id: str, discount_code: str | None) -> str: ...

class Role(BaseModel):
    key: str                                 # "Speaker" — the Drupal option key
    label: str; plural: str
    badge_class: str | None = None
    color: str | None = None                 # for the PDF badge
    sort: int = 0

class Roles(BaseModel):
    drupal_field: str = "attendee_status"
    options: list[Role] = []
    default: str | None = None

class AffiliationRules(BaseModel):
    drupal_field: str = "home_institution_or_organization"
    placeholder_values: list[str] = ["", "n/a", "na", "none", "null", "-", "--"]
    domain_map: dict[str, str] = {}          # "princeton.edu" -> "Princeton University"
    def normalize(self, *, email: str, declared: str | None) -> str | None: ...

class LodgingVocab(BaseModel):
    gender_identity_options: list[str] = []
    roommate_preference_options: list[str] = []
    room_gender_options: list[str] = ["Any", "Man", "Woman", "Non-binary", "Mixed"]
    room_categories: list[str] = []
    default_capacity: int = 2
    capacities: list[int] = [1, 2, 3, 4]

class LodgingRule(BaseModel):
    code: str                                # "OVER_CAPACITY", "ROOM_GENDER_MISMATCH", …
    severity: Literal["error", "warning", "info"] = "warning"
    enabled: bool = True

class Lodging(BaseModel):
    enabled: bool = False
    drupal_field: str = "lodging"
    vocab: LodgingVocab = LodgingVocab()
    rules: list[LodgingRule] = []

class Branding(BaseModel):
    site_name: str
    slogan: str | None = None
    theme: str = "neutral"                   # eventkit.ui theme id
    brand_color: str = Field(default="#e77500", pattern=r"^#[0-9a-fA-F]{6}$")
    brand_color_dark: str | None = None
    logo_url: str | None = None
    logo_stacked_url: str | None = None
    favicon_url: str | None = None
    event_image_url: str | None = None
    css_override_url: str | None = None      # adopter's theme.override.css
    footer_html: str | None = None           # sanitized subset

class Nametags(BaseModel):
    avery_template: Literal["5392", "74541", "5395", "custom"] = "5392"
    show_role_badge: bool = True
    show_affiliation: bool = True
    primary_logo_url: str | None = None
    sponsor_logo_url: str | None = None

class DrupalConfig(BaseModel):
    webform_schema: Path | None = None
    field_map: FieldMap | None = None
    join_key: Literal["uuid", "email"] = "uuid"
    @model_validator(mode="after")
    def _need_one(self): ...

class NotifyConfig(BaseModel):
    transport: Literal["log","smtp","resend","acs"] = "log"
    from_name: str | None = None
    default_recipients: list[EmailStr] = []
    events: dict[str, bool] = {}
    template_dir: Path | None = None

class LinkTemplate(BaseModel):
    label: str
    url: str
    param_style: Literal["fragment", "query"] = "fragment"
    sensitivity: Literal["low", "pii", "bearer"] = "low"
    fragment_params: dict[str, str] = {}
    query_params: dict[str, str] = {}
    query_params_from_env: dict[str, str] = {}
    prefill: dict[str, str] = {}             # "{full_name}", "{email}", "{token}"
    roles: list[str] = []                    # which roles see this link

class EventProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    event: EventInfo
    schedule: Schedule
    branding: Branding
    drupal: DrupalConfig
    roles: Roles = Roles()
    affiliation: AffiliationRules = AffiliationRules()
    ticketing: Ticketing = Ticketing()
    swag: Swag = Swag()
    lodging: Lodging = Lodging()
    nametags: Nametags = Nametags()
    notify: NotifyConfig = NotifyConfig()
    links: dict[str, LinkTemplate] = {}
```

**Per-app required-key validation:** each app declares which sections it needs and fails startup on
*its* missing keys only, so adding a lodging key doesn't break `nametag-press`.

### C.2 Loading and exposure

```python
# load.py
def load_profile(path: str | Path | None = None) -> EventProfile:
    """Resolution: arg -> $EVENT_PROFILE -> ./event-profile.yaml ->
    /home/site/event-profile.yaml. Raises EventProfileError with a human-readable,
    line-numbered validation report."""

@lru_cache(maxsize=1)
def get_profile() -> EventProfile: ...
def profile_dependency() -> Callable[..., EventProfile]: ...

# public.py
PUBLIC_FIELDS: Final[frozenset[str]]   # everything EXCEPT notify.default_recipients,
                                       # drupal.field_map internals, discount_code_env
def to_public_dict(p: EventProfile) -> dict[str, Any]: ...

# routes.py
def make_profile_router(profile: EventProfile, *, prefix: str = "/api",
                        etag: bool = True) -> APIRouter:
    """GET {prefix}/event-profile -> public JSON, ETag'd, Cache-Control: max-age=300.
       GET {prefix}/theme.css     -> generated :root{--pt-*} block from branding."""

# checkin.py — the day-key migration
def legacy_key_aliases(schedule: Schedule) -> dict[str, str]:
    """{'6/28': '2026-06-28', 'banquet': '2026-06-30-banquet', …} from the profile."""
def migrate_checkin_blob(raw: str | None, aliases: Mapping[str, str]) -> str | None: ...
```

Browser side, `eventkit/ui/static/js/eventprofile.js`:

```js
export async function loadProfile(url = "/api/event-profile")  // fetch + sessionStorage cache
export function checkinDays(profile)
export function swagShort(profile, key)   // replaces the duplicated size maps in app.js
export function roleLabel(profile, key)
```

That single module deletes the hardcoded day keys at `ticketed/frontend/app.js:1258-1262`, the four
separate size→label maps (`app.js:672, 1288, 1462, 1541`), the `["USML","UMED",…]` array at
`app.js:1605`, and the role literals across `admin_nametags.html`.

### C.3 `examples/caarms-2026/event-profile.yaml` (sanitized reference)

```yaml
schema_version: 1

event:
  name: "Conference for African American Researchers in the Mathematical Sciences"
  short_name: "CAARMS"
  year: 2026
  slug: caarms-2026
  site_url: "https://caarms.princeton.edu"
  registration_form_url: "https://caarms.princeton.edu/form/registration"
  contact_email: "events@example.edu"          # sanitized in the shipped example

schedule:
  timezone: America/New_York
  start_date: 2026-06-28
  end_date: 2026-07-01
  checkin_days:
    - { key: "2026-06-28", date: 2026-06-28 }
    - { key: "2026-06-29", date: 2026-06-29 }
    - { key: "2026-06-30", date: 2026-06-30 }
    - { key: "2026-06-30-banquet", date: 2026-06-30, kind: event,
        label: "Banquet", icon: utensils }
    - { key: "2026-07-01", date: 2026-07-01 }

branding:
  site_name: "CAARMS 2026 Registration & Ticket Dashboard"
  slogan: "Webform & ticket-vendor reconciliation"
  theme: princeton-orfe
  brand_color: "#e77500"          # Princeton Orange. Resolves the #f58025 conflict.
  logo_url: "/ui/themes/princeton-orfe/assets/logos/pu-logo.svg"
  logo_stacked_url: "/ui/themes/princeton-orfe/assets/logos/pu-logo-stacked.svg"
  favicon_url: "/ui/themes/princeton-orfe/assets/logos/favicon.ico"
  event_image_url: "/static/images/caarms_0.png"
  css_override_url: "/static/theme.override.css"

drupal:
  join_key: uuid
  webform_schema: ./webform-schema.yml
  field_map:
    fields:
      email:                  { key: [email, confirm_email_address], kind: email, required: true }
      name:                   { key: registrant_name, kind: name, required: true }
      sid:                    { key: sid, kind: int }
      serial:                 { key: serial, kind: int }
      uuid:                   { key: uuid, kind: text }
      tickets_sold_separately:{ key: tickets_sold_separately, kind: bool }
      destination_url:        { key: destination_url, kind: url }
      t_shirt_size:           { key: t_shirt_size, kind: select }
      attendee_status:        { key: attendee_status, kind: select }
      student:                { key: student, kind: bool }
      home_institution_or_organization: { key: home_institution_or_organization, kind: text }
      presenting_poster:      { key: presenting_poster, kind: bool }
      poster_title:           { key: poster_title, kind: text }
      faculty_adviser_name:   { key: faculty_adviser_name, kind: text }
      poster_presentation_abstract: { key: poster_presentation_abstract, kind: text }
      lodging:                { key: lodging, kind: bool }
      gender_identity:        { key: gender_identity, kind: select_other }
      roommate_preference:    { key: roommate_preference, kind: select }
      identified_roommate:    { key: identified_roommate, kind: text }

roles:
  drupal_field: attendee_status
  default: Attendee
  options:
    - { key: Speaker,   label: Speaker,   plural: Speakers,   badge_class: role-speaker,   color: "#e77500", sort: 1 }
    - { key: Organizer, label: Organizer, plural: Organizers, badge_class: role-organizer, color: "#1a1a1a", sort: 2 }
    - { key: Attendee,  label: Attendee,  plural: Attendees,  badge_class: role-attendee,  color: "#6c757d", sort: 3 }

affiliation:
  drupal_field: home_institution_or_organization
  placeholder_values: ["", "n/a", "na", "none", "null", "-", "--"]
  domain_map:
    princeton.edu: "Princeton University"

ticketing:
  vendor: eventbrite
  slug: caarms-2026
  exempt_field: tickets_sold_separately
  exempt_means: unchecked_is_exempt
  prefer_destination_url_discount: true
  event_url_template: "https://www.eventbrite.com/e/{slug}-tickets-{event_id}"
  status_order: [Unmatched, Pending, Paid, Complete, Waived, Exempt, Refunded, Cancelled]
  tiers:
    - key: pu-affiliate
      label: "Princeton faculty / staff / student"
      discount_code_env: EVENTBRITE_DISCOUNT_PU_AFFILIATE
      match: { email_domain_suffix: ["princeton.edu"] }
    - key: general
      label: "General admission"
      discount_code_env: EVENTBRITE_DISCOUNT_GENERAL
      match: { default: true }

swag:
  enabled: true
  kind: t-shirt
  drupal_field: t_shirt_size
  allow_replacement: true
  options:
    - { key: NONE, label: "No T-Shirt, Thanks!", short: "—", counts_toward_inventory: false, sort: 0 }
    - { key: USML, label: "Unisex - Small",  short: "S",   sort: 1 }
    - { key: UMED, label: "Unisex - Medium", short: "M",   sort: 2 }
    - { key: ULRG, label: "Unisex - Large",  short: "L",   sort: 3 }
    - { key: U1XL, label: "Unisex - 1XL",    short: "1XL", sort: 4 }
    - { key: U2XL, label: "Unisex - 2XL",    short: "2XL", sort: 5 }
    - { key: U3XL, label: "Unisex - 3XL",    short: "3XL", sort: 6 }

lodging:
  enabled: true
  drupal_field: lodging
  vocab:
    gender_identity_options: ["Woman", "Man", "Non-binary", "Other", "Prefer not to say"]
    roommate_preference_options:
      ["No Preference", "Prefer Same Gender", "Require Same Gender", "Single Room Requested"]
    room_gender_options: ["Any", "Man", "Woman", "Non-binary", "Mixed"]
    room_categories: ["Speaker Room", "Student Room"]
    default_capacity: 2
    capacities: [1, 2, 3, 4]
  rules:
    - { code: OVER_CAPACITY,                severity: error }
    - { code: ROOM_GENDER_MISMATCH,         severity: error }
    - { code: REQUIRE_SAME_GENDER_VIOLATED, severity: error }
    - { code: PREFER_SAME_GENDER_VIOLATED,  severity: warning }
    - { code: ROOMMATE_NOT_REGISTERED,      severity: warning }
    - { code: ROOMMATE_ELSEWHERE,           severity: warning }
    - { code: ROOMMATE_ONE_SIDED,           severity: warning }
    - { code: SINGLE_ROOM_SHARED,           severity: warning }

nametags:
  avery_template: "5392"
  show_role_badge: true
  show_affiliation: true
  primary_logo_url: "/ui/themes/princeton-orfe/assets/logos/pu-logo.svg"

notify:
  transport: log            # adopters opt into smtp/resend/acs
  from_name: "CAARMS 2026 Registration"
  default_recipients: ["events@example.edu"]
  events:
    unmatched_payment: true
    completed_payment: true
    pending_payment: false
    exempt_registration: false
    sync_failed: true

links:
  reimbursement:
    label: "Travel reimbursement form"
    url: "https://orfe.princeton.edu/forms/reimbursement"
    param_style: fragment
    sensitivity: pii
    fragment_params:
      Business_Purpose: "CAARMS 2026"
      Departure_date: "2026-06-28"
      Return_Date: "2026-07-01"
    prefill:
      Signer_name: "{full_name}"
      Signer_email: "{email}"
  media_release:
    label: "Media release (DocuSign PowerForm)"
    url: "https://na3.docusign.net/Member/PowerFormSigning.aspx"
    param_style: query
    sensitivity: pii
    query_params_from_env: { PowerFormId: DOCUSIGN_MEDIA_RELEASE_FORM_ID }
    query_params:
      Event_Title: "CAARMS 2026"
      Event_Dates: "2026-06-28"
      Event_Location: "Princeton University"
    prefill:
      Signer_UserName: "{full_name}"
      Signer_Email: "{email}"
  slide_upload:
    label: "Upload your slides"
    url: "https://caarms.princeton.edu/form/slide-upload"
    param_style: query
    sensitivity: pii
    prefill: { email: "{email}", first_name: "{first_name}", last_name: "{last_name}" }
    roles: [Speaker]
  speaker_prefill:
    label: "Edit your bio & talk submission"
    url: "https://caarms.princeton.edu/form/speaker-bios-talks"
    param_style: query
    sensitivity: bearer
    prefill: { token: "{token}" }
    roles: [Speaker]
```

Note the discount codes carry the **env var name**, not `CODE_AFFILIATE`/`CODE_GENERAL`.
`resolve_tier()` returns the tier; the app reads `os.environ[tier.discount_code_env]`. Codes live in
App Service settings. `links.reimbursement` is the entire content of
`posted/frontend/admin_reimbursement.html:289` turned into data.

## D. UI kit detail

### D.1 Keep / delete / relocate

**Keep:** `tokens/tokens.json` (well-structured W3C tokens), `tokens/tokens.css`, `tokens/fonts.css`,
`components/{layout,buttons,forms,cards,header,footer,hero}.css`, `main.js`, `hero.js`,
`assets/logos/*`.

**Delete:**
- `components/cards.html` — fabricated ORFE news headlines ("Professor Jane Doe elected to the
  National Academy of Sciences") attributed to a real department, plus `jdoe@example.edu` at line 90.
- `components/footer.html`'s address line (`Sherrerd Hall, Charlton Street, Princeton, NJ 08544 ·
  609-258-0100`, line 14) → `{{ branding.footer_html }}`.
- `components/hero.html`'s fake news links/images.

**Replace with:** one `examples/kitchen-sink.html` per theme with obviously-synthetic content
(`Item title`, `owner@example.edu`, `Example Hall, Example City`), used as the visual-regression
target. Component demos are useful; the fabricated content isn't.

**Relocate:** the Princeton bar, ORFE logo, and SEAS lockup markup (`header.html:11-35`) into
`themes/princeton-orfe/partials/chrome.html`; the neutral theme's chrome has one site-logo slot.

### D.2 Cascading layers

```
tokens/base.css                :root { --pt-* }  brand-neutral (grayscale brand ramp)
themes/<id>/theme.css          :root { --pt-brand-70: #e77500; … } + theme-only components
<generated>/theme.css          :root from profile.branding.brand_color
<adopter>/theme.override.css   last word, mounted from /home, never in the image
```

```python
# eventkit/ui/__init__.py
def static_path() -> Path: ...
def theme_ids() -> list[str]: ...
def theme_path(theme_id: str) -> Path: ...
def render_theme_vars(profile: EventProfile) -> str:
    """Returns a :root{--pt-brand-70:…;} block with the ramp derived from
    branding.brand_color (OKLCH lighten/darken) so an adopter sets ONE hex."""
def vendor(dest: Path, *, theme: str, hashed: bool = False) -> "VendorManifest": ...
def assert_assets_present(dir: Path) -> None: ...
```

Two themes at v0.1: `neutral`, `princeton-orfe`. A third-party adopter sets
`branding.theme: neutral` + `brand_color` and gets a coherent look with zero CSS.

`#e77500` wins the orange conflict — it's Princeton's official orange and what `tokens.json:15`
already declares. `#f58025` (older SEAS orange) appears ~40× via inline
`style="background-color: #f58025"` (`ticketed/frontend/index.html:209, 684, 836, 879, 896, 941,
961, 985`) and once at `main.py:166`; all become `var(--pt-brand-70)`.

### D.3 Shared JS modules

```js
// esc.js
export function escapeHtml(s);
export function attr(s);                 // attribute contexts
export function html(strings, ...values) // tagged template, auto-escapes interpolations

// fetchx.js
export async function getJSON(url, opts);
export async function postJSON(url, body, opts);
export async function postForm(url, formData, opts);
// 401 -> location.assign('/.auth/login/aad?post_login_redirect_url=' + here)
// 403 -> toast.error(detail); 409 -> reload toast; non-2xx -> throws ApiError{status, detail}

// toast.js
export const toast = { info(msg), success(msg), warn(msg), error(msg, {sticky}) };

// table.js  — declarative, delegated listeners, NO inline onclick
export function createTable(root, {
  columns,          // [{key, label, sortable, sortValue, render(row), className, width}]
  rows, rowKey, defaultSort, empty,
  actions,          // {actionName: (row, ev) => void}  -> data-action="actionName"
}) => ({ setRows, getRows, sort, destroy });

// filters.js
export function createFilterBar(root, {
  search: {fields, placeholder, debounceMs = 150},
  selects: [{key, label, options, all: 'All'}],   // options from the event profile
  onChange,
}) => ({ apply(rows), reset, state });

// chrome.js
export async function mountChrome(root, {profile, active, nav, principal});

// backup-panel.js
export function mountBackupPanel(root, {
  backupUrl = '/api/admin/db-backup', restoreUrl = '/api/admin/db-restore',
  validateUrl = '/api/admin/db-restore/validate',
  enableRestore,        // from profile/settings, not a {{ }} string replace
  confirmPhrase = 'RESTORE' });

// checkin.js
export function createCheckinCells(profile, {onToggle});   // day keys from profile
export const CHECKIN_STATES = {UNRECORDED:0, CHECKED_IN:1, UNSURE:2, ABSENT:3};

// lodging-rules.js  — PURE. No DOM. No fetch. Testable.
export function validateRoomState(room, occupants, ctx) -> {warnings: Warning[]}
export function checkRoommateRequest(registrant, ctx) -> {found, mutual, roommateId, roommateRoomId}
export function findByName(name, registrants) -> registrant|null|AMBIGUOUS
export const RULE_MESSAGES;   // code -> template; overridable per locale/profile

// qr.js — vendored MIT QR encoder; renders SVG locally, no network
export function qrSvg(text, {size = 250, ecc = 'M'}) -> SVGElement
```

`Warning` shape: `{code, severity, message, subjects[]}`. Codes come from `profile.lodging.rules`, so
severity is per-event and **tests assert codes, not prose**.

CSS additions: `table.css`, `toast.css`, `modal.css`, `chrome.css`, `badge.css` (the
`.badge-gender`/`.role-*`/status badges currently inline at `admin_lodging.html:190-201`).

**Also eliminate string-interpolated inline handlers.** `ticketed/frontend/app.js` builds
`onclick="openLinkModal('${row.payment_id}', '${escapeHtml(row.first_name)}', …)"` (lines 472, 497,
502, 505, 539, 1278-1317) — HTML escaping applied to a JavaScript string-literal context, so a name
containing `\'` or `</script>` breaks out. `table.js` uses `data-action` + one delegated listener.

## E. Testing (`eventkit` itself)

```
tests/
├── conftest.py
├── unit/
│   ├── drupal/{test_coerce,test_schema_inference,test_parse,test_parity}.py
│   │       # test_parity: webhook path == importer path on the same fixture
│   ├── eventprofile/{test_valid,test_invalid,test_ticketing,test_affiliation,
│   │                 test_checkin_migration}.py
│   │       # test_invalid: ~15 cases — dup checkin key, key with '/', bad tz, slug
│   │       # regex, extra field, no field_map, date out of range, bad hex, bad avery
│   ├── eventbrite/{test_aggregate,test_client,test_sync}.py
│   │       # aggregate: paid-beats-refunded, multi-paid summing, latest-wins identity,
│   │       # no-email skip, unknown status, malformed costs
│   │       # client (respx): continuation pagination, 429, 500, timeout, max_pages
│   ├── notify/{test_policy,test_render,test_transports}.py
│   ├── test_identity.py  test_webhook.py  test_auth.py  test_backup.py  test_logging.py
│   │       # auth: header matrix; dev bypass refused on App Service; redirect vs 401
│   │       # by path; ws ticket expiry/tamper
│   └── ui/test_ui_packaging.py    # static_path() is a real dir in an INSTALLED wheel
├── integration/
│   ├── test_db_sqlite.py          # pragmas applied; TRUNCATE not WAL
│   ├── test_migrations.py         # empty -> head; legacy-stamped -> head; concurrent
│   │                              # upgrade blocked by filelock; failure does NOT
│   │                              # leave the app running
│   ├── test_backup_router.py      # full round trip through TestClient
│   └── test_reference_app.py      # ~120-line app wiring EVERY module together —
│                                  # the contract test for the 5 apps
└── js/
    ├── lodging-rules.test.js      # all 8 rule codes + shared golden fixtures
    └── table.test.js  filters.test.js  esc.test.js  eventprofile.test.js
```

`test_reference_app.py` matters: it's the executable version of "here's how you build an app on
eventkit," and it catches API breaks unit tests miss.

**Golden Drupal fixtures** (`src/eventkit/testing/fixtures/drupal/`), shipped in the wheel so apps
reuse them. All sanitized to `ada@example.edu` / `Example University`, derived from the shapes in
`posted/tests/test_submissions.json` and the two validators but carrying no real data:

```
registration_flat.json                 flat keys, no `data` wrapper
registration_wrapped.json              Drupal Remote Post {"data": {...}}
registration_composite_name.json       registrant_name: {first, last}
registration_bare_name.json            registrant_name: "Ada Lovelace"
registration_one_word_name.json        registrant_name: "Prince"
registration_email_confirm.json        email: {mail_1, mail_2}
registration_select_other.json         gender_identity: {select:"_other_", other:"…"}
registration_select_normal.json        gender_identity: {select:"Woman", other:""}
registration_checkbox_variants.json    "1"/"Yes"/"on"/true/0/""/null
registration_serial_only.json          serial but no sid
registration_sid_only.json
registration_missing_email.json        must be rejected
registration_empty_strings.json        "" vs null vs missing
poster_yes.json  poster_no.json  poster_missing_details.json
lodging_full.json  lodging_write_in.json
```

### E.1 The exported pytest plugin

Entry point `pytest11 = "eventkit.testing.plugin"` — installing `eventkit[test]` gives every app
these with no import:

```python
@pytest.fixture
def eventkit_env(monkeypatch) -> Callable[..., None]:
    """set(**kwargs) -> monkeypatch.setenv, uppercased. Safe because settings are lazy
    (get_settings.cache_clear() on teardown)."""

@pytest.fixture
def sqlite_engine(tmp_path) -> Engine:
    """File-backed temp SQLite with eventkit's pragmas — file-backed, not :memory:,
    so migration tests are realistic."""

@pytest.fixture
def memory_engine() -> Engine:
    """sqlite:///:memory: + StaticPool + check_same_thread=False.
    The dance both repos hand-roll today, once."""

@pytest.fixture
def make_database(memory_engine) -> Callable[[type[DeclarativeBase]], Database]: ...
@pytest.fixture
def db_session(make_database) -> Iterator[Session]: ...
@pytest.fixture
def make_client() -> Callable[..., TestClient]:
    """make_client(app, db=session, principal='admin@example.edu') wires
    dependency_overrides for get_db and auth.require, returns a TestClient."""
@pytest.fixture
def principal() -> Principal: ...                 # admin@example.edu
@pytest.fixture
def as_anonymous(make_client): ...                # asserts 401/403 paths
@pytest.fixture
def webhook_headers() -> dict[str, str]: ...
@pytest.fixture
def bad_webhook_headers() -> dict[str, str]: ...
@pytest.fixture
def event_profile() -> EventProfile:
    """Minimal, valid, brand-neutral ('Example Conference 2030')."""
@pytest.fixture
def caarms_profile() -> EventProfile:
    """The shipped examples/caarms-2026 profile — regression parity tests."""
@pytest.fixture
def drupal_payload() -> Callable[[str], dict]:
    """drupal_payload('registration_wrapped') -> the golden fixture dict."""
@pytest.fixture
def mail_outbox(monkeypatch) -> list[Message]:    # forces MemoryTransport
@pytest.fixture
def eventbrite_mock() -> "EventbriteMock":
    """respx-backed. .add_attendees([...]); .set_pages(n); .fail_with(429).
    Also asserts no real network call escaped."""
@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """AUTOUSE: patches socket.socket to raise on connect unless a test opts out via
    @pytest.mark.allow_network. Would have caught download_assets' startup fetch."""
@pytest.fixture
def frozen_now() -> Callable[[str], None]: ...    # freezegun wrapper
```

Result — each app's `conftest.py` shrinks to:

```python
import pytest
from myapp.main import create_app          # factory, not a module-level `app`
from myapp.db import Base

@pytest.fixture
def app(eventkit_env, event_profile, make_database):
    eventkit_env(drupal_webhook_token="t"*32, authorized_principals="admin@example.edu")
    return create_app(database=make_database(Base), profile=event_profile)

@pytest.fixture
def client(app, db_session, make_client):
    return make_client(app, db=db_session)
```

The `create_app()` factory is the precondition for the plugin being useful — it's a per-app refactor.

### E.2 Docker test target (templated into all 5 apps)

```dockerfile
FROM python:3.11-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd -m -u 10001 app
WORKDIR /app

FROM base AS deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM deps AS runtime
COPY --chown=app:app src/ ./src/            # NO tests/ in the runtime image
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["uvicorn","myapp.main:create_app","--factory","--host","0.0.0.0","--port","8000"]

FROM deps AS test
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm \
    && rm -rf /var/lib/apt/lists/*
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN pip install --no-cache-dir -e ".[test]"
CMD ["./scripts/test.sh"]
```

Fixes vs today: no `tests/` in the runtime image, non-root, healthcheck, dependency-layer caching,
no `build-essential`+`libpq-dev` in the shipped image (use `psycopg[binary]`).
`scripts/test.sh` runs `pytest -q --cov` then `npx vitest run` with a single exit code;
`docker compose run --rm test` and `./run_tests.sh` both target it so local and CI are identical.

### E.3 Getting the lodging rules engine under test

The engine is `admin_lodging.html:1572-1650` inside a `<script>` tag reading page globals
(`activeWarningsCount`, `allRegistrants`, `allRooms`) — untestable by construction, and
`posted/tests/test_lodging.py` (473 lines) covers only HTTP endpoints.

1. Move to `lodging-rules.js` as pure functions; pass `ctx = {registrants, rooms, rules}` explicitly.
2. Return `{code, severity, subjects}`; render prose from `RULE_MESSAGES[code]` at the call site.
3. **Shared golden fixtures** `{rooms, registrants, expected: [{room_id, codes:[…]}]}` — one file per
   scenario: over-capacity; gender-constrained room violated; mixed room with a `Require Same Gender`
   occupant; mixed room with only `Prefer Same Gender`; roommate not registered; roommate in another
   room; one-sided request; mutual request satisfied (expect zero warnings); unspecified gender
   treated as its own value (`uniqueGenders` is built from `o.gender_identity || "Unspecified"` at
   `admin_lodging.html:1588`, so blanks count as a distinct gender for rules 3/4); empty room.
4. `tests/js/lodging-rules.test.js` iterates the fixtures with vitest.
5. Python `rules.py` runs the *same* rule set server-side over the same fixtures, with a test
   asserting identical code sets. Worth the duplication because assignment decisions have **no**
   server-side validation today: `dropToRoom` (`admin_lodging.html:1694-1720`) does a client-side
   capacity check and comments that "backend also checks", but the assignment endpoint doesn't
   enforce gender rules at all.
6. One snapshot test on `RULE_MESSAGES` prose so wording changes are visible in review.

## F. `eventkit azure` — bootstrap toolkit detail

*Note: the subagent design for this section was partly lost to output truncation. The
recommendations, flags, and build order below are verbatim; the lib/verb/gate/app.conf structure is
reconstructed from them plus the source inventory. Treat the `az` predicates as needing verification
against the installed CLI version.*

### F.1 `lib/` decomposition

Both `ticketed/deploy/` (~870 lines) and `posted/deploy/` (~605) are 70–90% the same boilerplate:
identical ANSI colour blocks, identical `log_info`/`log_success`/`log_warning`/`log_error`/`log_step`,
the same prompt-with-default pattern, the same `.env.deploy` rewrite-after-every-prompt.

```
lib/boot.zsh      ek_boot           # strict mode, trap, EVENTKIT_AZURE_LIB resolution
lib/color.zsh     ek_color_init     # respects NO_COLOR, non-TTY, TERM=dumb
lib/log.zsh       ek_info ek_ok ek_warn ek_err ek_step ek_step_count ek_die
lib/prompt.zsh    ek_ask ek_ask_secret ek_ask_choice ek_confirm ek_ask_validated
lib/state.zsh     ek_state_init ek_state_get ek_state_set ek_step_status
                  ek_step_begin ek_step_done ek_step_skip ek_step_fail ek_state_history
lib/name.zsh      ek_name           # deterministic, length-clamped resource naming
lib/az.zsh        ek_az ek_az_json ek_az_exists ek_az_version_check ek_ensure_rg
                  ek_ensure_acr ek_ensure_plan ek_ensure_webapp ek_ensure_mi_acrpull
lib/gh.zsh        ek_gh_var_set ek_gh_secret_set ek_gh_repo_path ek_gh_auth_check
lib/secrets.zsh   ek_gen_secret     # openssl rand -hex 32
                  ek_appsetting_set ek_appsetting_get ek_secret_rotate
lib/manual.zsh    ek_await_manual_step
lib/verify.zsh    ek_verify_<gate>  # one predicate function per gate
lib/conf.zsh      ek_conf_load      # parse app.conf
lib/steps.zsh     ek_run_steps      # ordered step dispatch honouring the ledger
```

`ek_az` wraps every call so `--dry-run` prints instead of executing, every invocation is appended to
the ledger's `history`, and failures carry the command in the error. Prefer `az rest` with a pinned
`api-version` for security-critical surfaces (auth config, federated credentials) — the REST contract
is stabler than the CLI's.

### F.2 Verbs

| Verb | Purpose |
|---|---|
| `deploy` | Provision + configure + ship. **Idempotent and resumable by default.** |
| `resume` | Alias for `deploy --no-reprompt` — replays only `pending`/`failed` steps using stored answers. Recommended over making resume a separate code path: one path means resume is exercised on every run, so it can't rot. |
| `update` | Rebuild image + restart. No provisioning. |
| `teardown` | Delete app-scoped resources; `--purge-group` for the whole RG (guarded). |
| `bootstrap` | Whole-event, dependency-ordered: `--event caarms-2026 --apps a,b,c` |
| `doctor` | Preflight: `az`/`gh`/`zsh`/`openssl` presence and versions, login state, subscription, quota, name availability, `--verify-self` printing the resolved eventkit version + commit |
| `status` | Ledger + live Azure state side by side |
| `adopt` | Import existing resources into the ledger (for the two live deployments) |
| `oidc` | Federated credentials + GitHub variables (split least-privilege identities) |
| `secrets rotate` | Rotate a named app setting with `--append` where the platform supports it |
| `domain` | Custom domain + managed certificate |
| `scale-guard` | Pin `--instance-count 1`, set `WEBSITES_ENABLE_APP_SERVICE_STORAGE=true`, refuse autoscale when `DATABASE_URL` is SQLite |
| `backup` / `restore` | Against the app's own unified format — replaces `db_admin_tool.py` |
| `drift` | Diff live config vs ledger; auto-remediate the firewall-IP case |
| `gate ack` | `--until <date> --reason <ticket>` for tracked, time-boxed skipped gates |
| `logs` / `open` / `eject` | Convenience |

Global flags: `--dry-run`, `--yes`/`--non-interactive`, `--no-reprompt`, `--app`, `--event`,
`--postgres`, `--verbose`. Every prompt validates at entry: ACR name alphanumeric-only 5–50
lowercase, web app global uniqueness (`az webapp list-consumption-locations` / name-availability
check), RG naming, email lists.

### F.3 The manual-step gate

```zsh
ek_await_manual_step \
  --id easy-auth \
  --title "Configure the Entra ID identity provider (Easy Auth)" \
  --risk critical \
  --portal "https://portal.azure.com/#@$TENANT/resource$APP_ID/authentication" \
  --checklist-file "$EVENTKIT_AZURE_LIB/../docs/gates/easy-auth.md" \
  --verify ek_verify_easy_auth \
  --interval 10 --timeout 1800
```

Behaviour: prints a numbered copy-pasteable checklist plus the portal deep link; polls the predicate
on `--interval` with a spinner, elapsed time, and a line naming *what* it is waiting for; succeeds
the instant the predicate passes; accepts `[s]kip / [r]etry now / [o]pen portal / [q]uit and resume
later` keypresses while waiting; on quit records `pending` with the gate id so `resume` re-enters
exactly there; on timeout prints the checklist again plus `eventkit azure resume --app X` rather than
hanging; under `--yes` fails fast with the checklist and a non-zero exit rather than blocking CI.

Skipped gates with `risk: critical` surface in nightly `drift`. `gate ack --until <date> --reason
<ticket>` suppresses one until the date, then resurfaces loudly — because a permanently-failing
nightly alert is how the `&>/dev/null` habit gets established.

**Gates and their predicates** (verify against your `az` version):

| Gate | Manual action | Predicate |
|---|---|---|
| `az-login` | `az login` | `az account show --query id -o tsv` |
| `gh-auth` | `gh auth login` | `gh auth status` exit 0 |
| `provider-reg` | none (auto), but can lag | `az provider show -n Microsoft.DBforPostgreSQL --query registrationState -o tsv` == `Registered` |
| `entra-app` | Create the App Registration (or have OIT create it) | `az ad app list --display-name "$SP_NAME" --query "[0].appId" -o tsv` non-empty |
| `easy-auth` | Add the identity provider in Portal → Authentication | `az webapp auth show -g $RG -n $APP --query "identityProviders.azureActiveDirectory.registration.clientId" -o tsv` non-empty **and** `--query "platform.enabled"` true |
| `auth-enabled-guard` | — | `az webapp config appsettings list … --query "[?name=='WEBSITE_AUTH_ENABLED'].value"` — CRITICAL log if the app is hosted and this isn't True |
| `dns-cname` | Create the CNAME with your DNS admin | `dig +short <fqdn> CNAME` contains `<app>.azurewebsites.net` |
| `domain-cert` | — (scripted, but preview CLI) | `az webapp config hostname list -g $RG --webapp-name $APP --query "[?name=='<fqdn>'].sslState" -o tsv` == `SniEnabled`. **Read this back rather than trusting `az webapp config ssl create`'s return — it's marked preview and its output shape can change.** |
| `acrpull` | — | `az role assignment list --assignee <mi-principal-id> --scope <acr-id> --query "[?roleDefinitionName=='AcrPull']" -o tsv` non-empty |
| `eventbrite-token` | Fetch the private token + event id from the Eventbrite UI | `curl -sf -H "Authorization: Bearer $TOK" https://www.eventbriteapi.com/v3/users/me/` **and** `…/v3/events/$EVENT_ID/` both 200 |
| `webhook-handler` | Create the Remote Post handler in Drupal admin | poll the app's own `GET /api/webhook/status` until `authenticated_submissions_total > 0` (see below) |

`GET /api/webhook/status` is a new, safe app-side signal: it returns only counters and timestamps —
`{received_total, authenticated_total, rejected_total, last_received_at, unmapped_keys[]}` — no
attendee data, so it can be reachable with the webhook token or a principal without leaking PII. It
doubles as the live-rename detector (`unmapped_keys`).

### F.4 State ledger

Replaces `.env.deploy` as the *structural* record. Committed at `./.eventkit/state.json`:

```json
{
  "schemaVersion": 1,
  "event": "caarms-2026",
  "app": "ticket-reconciler",
  "azure": { "subscriptionId": "…", "tenantId": "…", "location": "eastus" },
  "names": { "resourceGroup": "…", "acr": "…", "plan": "…", "webApp": "…", "dbServer": null },
  "datastore": { "kind": "sqlite", "path": "/home/ticket-reconciler.db" },
  "steps": [
    { "id": "rg",        "status": "done",    "at": "…", "resourceId": "/subscriptions/…" },
    { "id": "acr",       "status": "done",    "at": "…", "resourceId": "…" },
    { "id": "easy-auth", "status": "skipped", "at": "…", "risk": "critical",
      "ackUntil": "2026-09-01", "reason": "OIT ticket #12345" },
    { "id": "domain",    "status": "pending" }
  ],
  "history": [ { "at": "…", "azVersion": "2.88.0", "eventkit": "0.1.0+9f2c1ab", "verb": "deploy" } ]
}
```

**Secrets never enter it.** App Service app settings are the source of truth and are read back on
resume; local working values live in a gitignored `.env.deploy`. Committing the ledger makes it a
supply-chain surface — anyone who can merge to `main` could repoint `names.resourceGroup` at a
different RG, including for a teardown — so: `CODEOWNERS` on `.eventkit/**` requiring a second
reviewer, plus a CI check that `names.*` and `azure.subscriptionId` are unchanged unless the PR
carries an `infra-change` label.

**Drift handling.** For each step, compare the live resource against the recorded shape:
- absent → run the step;
- present and matching → mark `done`, skip;
- present but *different* (SKU, location, tags) → **warn and refuse by default**, with `--adopt` to
  record it as-is and `--reconcile` to bring it to spec. Silently adopting a mismatched resource is
  how you end up deploying into someone else's app.
- **partially** created (web app exists, managed identity not assigned) → steps are decomposed
  finely enough that each has its own predicate, so the MI assignment is its own step with its own
  `az role assignment list` check.

### F.5 Multi-app orchestration and naming

**One resource group per event**, all apps inside — a whole event tears down in one operation, which
is what actually happens after a conference. **One App Service Plan per event**, shared (B1 hosts
several small apps comfortably; five plans for a one-week event is waste). **One ACR per org**,
since images are event-independent and ACR has a per-registry cost floor.

```
rg:      ek-<event>-rg                       # ek-caarms-2026-rg
plan:    ek-<event>-plan
acr:     ek<org><rand6>                      # alnum only, lowercase, 5-50
app:     ek-<event>-<app>-<rand6>            # globally unique; <app> abbreviated if needed
db:      ek-<event>-<app>-db-<rand6>
storage: ek<event><rand6>                    # alnum, <=24 chars — clamp hard
sp:      ek-<event>-<app>-deployer
fic:     github-actions-oidc-<env>
```

`ek_name` clamps each segment against the per-type Azure limit (storage 24, ACR 50, web app 60) and
records the final name in the ledger, so a truncated name is stable across runs. No `orfe-`
hardcoding anywhere; the `ek-` prefix is itself configurable via `--prefix`.

`bootstrap` runs shared resources first (RG → ACR → plan → storage), then each app's steps in
parallel-safe order, then per-app gates. A single app still deploys standalone by finding-or-creating
the shared resources.

### F.6 Per-app contract — `deploy/app.conf`

The toolkit reads this and generates the whole flow, so a sixth app is a config file, not a script.

```toml
# ticket-reconciler/deploy/app.conf
name        = "ticket-reconciler"
image       = "ticket-reconciler"
health_path = "/healthz"
easy_auth   = true            # admin-only app; Easy Auth is mandatory
needs_db    = true
db_default  = "sqlite"        # "postgres" to flip the default
custom_domain = "optional"
gates       = ["easy-auth", "eventbrite-token", "webhook-handler"]

[[setting]]
name = "DATABASE_URL"; type = "computed"; required = true
[[setting]]
name = "AUTHORIZED_PRINCIPALS"; type = "list"; required = true
prompt = "Comma-separated admin emails (or @domain.edu). Empty means DENY ALL."
[[setting]]
name = "DRUPAL_WEBHOOK_TOKEN"; type = "secret"; required = true; generate = "hex32"
[[setting]]
name = "EVENTBRITE_API_TOKEN"; type = "secret"; required = true; gate = "eventbrite-token"
[[setting]]
name = "EVENTBRITE_EVENT_ID"; type = "string"; required = true; gate = "eventbrite-token"
[[setting]]
name = "EVENTBRITE_DISCOUNT_PU_AFFILIATE"; type = "secret"; required = false
[[setting]]
name = "EVENTBRITE_DISCOUNT_GENERAL"; type = "secret"; required = false
[[setting]]
name = "ENABLE_AUTO_SYNC"; type = "bool"; default = "True"
[[setting]]
name = "AUTO_SYNC_INTERVAL_MINUTES"; type = "int"; default = "60"; min = 3; max = 1440
[[setting]]
name = "ENABLE_RESTORE"; type = "bool"; default = "False"
[[setting]]
name = "NOTIFY_TRANSPORT"; type = "choice"; choices = ["log","smtp","resend","acs"]; default = "log"
[[setting]]
name = "WEBSITES_PORT"; type = "fixed"; value = "8000"
[[setting]]
name = "WEBSITES_CONTAINER_START_TIME_LIMIT"; type = "fixed"; value = "600"
```

```toml
# poster-gallery/deploy/app.conf
name = "poster-gallery"; image = "poster-gallery"; health_path = "/healthz"
easy_auth = "admin-routes-only"    # public gallery + RSS stay anonymous
needs_db  = true; db_default = "sqlite"
custom_domain = "recommended"      # posters.<event>.example.edu
gates = ["easy-auth", "dns-cname", "domain-cert", "webhook-handler"]
[[setting]] name = "DRUPAL_WEBHOOK_TOKEN"; type = "secret"; required = true; generate = "hex32"
[[setting]] name = "AUTHORIZED_PRINCIPALS"; type = "list"; required = true
[[setting]] name = "ENABLE_RESTORE"; type = "bool"; default = "False"
[[setting]] name = "WEBSITES_PORT"; type = "fixed"; value = "8000"
```

```toml
# link-forge/app.conf — stateless
name = "link-forge"; image = "link-forge"; health_path = "/healthz"
easy_auth = true; needs_db = false
gates = ["easy-auth"]
[[setting]] name = "AUTHORIZED_PRINCIPALS"; type = "list"; required = true
[[setting]] name = "SPEAKER_LINK_TOKENS"; type = "secret"; required = false
             # JSON {email: token}; prefer Key Vault reference
[[setting]] name = "DOCUSIGN_MEDIA_RELEASE_FORM_ID"; type = "string"; required = false
[[setting]] name = "RECONCILER_ROSTER_URL"; type = "string"; required = false
```

### F.7 Provisioning steps

`00 preflight` · `10 rg` · `15 storage + file share` (SQLite only) · `20 acr` · `25 build+push` ·
`30 plan` · `35 webapp` · `40 mi + acrpull` · `45 container config` · `50 db` (Postgres only:
provider reg → `--public-access None` → db create → firewall from outbound IPs) · `55 app settings`
(single writer) · `60 scale-guard` · `65 gate: easy-auth` · `70 gate: eventbrite-token` ·
`75 oidc` · `80 domain` (`dns-cname` → `domain-cert`) · `85 gate: webhook-handler` ·
`90 verify` · `95 print Remote Post config block`.

### F.8 Fixes carried in (beyond the plan body)

- `az postgres flexible-server create --public-access None` (verified available on az 2.88), then add
  only the App Service outbound IPs. The current `--public-access 0.0.0.0`-then-narrow leaves a real
  exposure window for no reason. Private endpoint documented as the hardening path but not the
  default (needs a VNet, delegated subnet, and Private DNS — real complexity for a departmental app).
- **App Service outbound IPs are not stable.** They change when the plan is scaled or moved, and the
  DB then silently becomes unreachable. `drift` compares live `possibleOutboundIpAddresses` against
  the firewall rule set and **re-adds missing ones automatically** — one of the few cases where drift
  should remediate rather than report, because the failure mode is an outage.
- **Easy Auth client secrets expire.** The current approach creates one with `--years 2`; in two
  years admin login breaks with a 500 and no warning. `drift` alerts at <60 days; `secrets rotate`
  uses `--append` so there's no window. Most likely "the site broke and nobody knows why" event on a
  two-year horizon, and nothing today would catch it.
- **Pin `az`.** `authV2` is an extension whose surface has changed across versions;
  `az webapp config ssl create` is preview; `az acr repository show-manifests` is deprecated in favour
  of `az acr manifest list-metadata`. `doctor` enforces a minimum (`az >= 2.60`) and warns above a
  tested maximum; the version goes into the ledger `history` so a regression correlates with an
  upgrade.
- **`BYPASS_HEADER_VALUE="true"` (`posted/deploy.sh:238`) is a WAF bypass with no secret in it.** The
  header name is `x-waf-bypass`, in a public repo. Get a real shared secret from WDS and treat
  it as `type = "secret"`, or drop the mechanism.

### F.9 GitOps / CI

Reusable `workflow_call` workflows in `pu-sherrerd/.github`:

- **`test.yml`** — `docker compose build test && docker compose run --rm test`, matrixed
  sqlite/postgres. Delete the postgres service container from the job that then sets
  `DATABASE_URL=sqlite:///:memory:`.
- **`deploy.yml`** — OIDC login, **one build path**, ship image, `azure/webapps-deploy` pinned to the
  SHA tag. **CI never writes app settings** — the toolkit is the sole writer. They're defined twice
  today (`deploy.sh` + `deploy.yml`) and have already drifted three ways on the admin-principal list
  (7 in `posted/backend/config.py:25`, 7 in `deploy.yml:100`, 4 in `deploy.sh:137`). Never
  interpolate `secrets.*` into `if [ -n "…" ]` shell tests.
  **Build path: `docker buildx` in CI, `az acr build` for local `update`.** CI already has a Docker
  daemon, buildx gives layer caching and reproducible multi-arch, and the `az acr build` path stays
  for operators without local Docker. Document that these are the two supported paths and that they
  produce equivalent images — today they silently differ.
- **`admin-task.yml`** — replaces `clear_data.yml`. **No unauthenticated HTTP route.** OIDC-federate
  into Azure and run the operation as a one-shot container command against the app's own CLI; HMAC
  task token (`eventkit.admin`: `HMAC-SHA256(secret, path|sha256(body)|ts)`, ±300s, single-use nonce
  table, audit row) kept as the documented fallback.
- **`backup.yml`** — scheduled daily snapshot → Storage Account with lifecycle rules.
  **Mandatory, not optional:** Postgres Flexible Server gives 7–35 days of automated backups free;
  SQLite on `/home` gives nothing automatic. "SQLite by default" otherwise silently means "no backups
  by default", which is exactly the `db_admin_tool.py` failure mode being eliminated.
- **`drift.yml`** — scheduled; diffs live Azure config against the ledger, auto-remediates firewall
  IPs, alerts on cert expiry and unacknowledged critical skipped gates, opens an issue.
- **OIDC identities** — split the single all-powerful deployer into least-privilege identities
  (build/push vs deploy vs infra). Federated-credential subjects add `environment:production`, not
  just `ref:refs/heads/main`. **FIC subjects do not support wildcards**, so tag-triggered deploys need
  the preview flexible-FIC path or one credential per pattern. Note `posted/deploy/setup-oidc.sh`
  grants **Contributor on the resource group** — narrow that.

### F.10 Migrating the two live deployments

1. `eventkit azure adopt --app <name>` against each live deployment: read RG, ACR, plan, web app, DB,
   settings, and write the ledger without touching anything. Reconcile the drifted RG names —
   `ticketed/deploy/teardown.sh:21` defaults to `orfe-reconciler-rg` while
   `.github/workflows/teardown.yml:28` hardcodes `orfe-dept-azure-wmassey-group-caarms-reconciler-rg`,
   so a CI teardown and a local teardown currently target different resource groups.
2. Verify and document the `easy-auth` gate against the live `posted` app — the configuration exists
   (the admin tools demonstrably worked) but was done by hand in the portal and is unscripted and
   undocumented, so it isn't reproducible.
3. `alembic stamp` the two live databases at the initial revision so Alembic adopts them without
   re-running DDL.
4. Rotate every secret via `secrets rotate`, then re-point the three Drupal Remote Post handlers to
   the new tokens.
5. Replace `db_admin_tool.py` with `eventkit azure backup`/`restore` against the app's own unified
   format. Its dumps cover only `registrants` + `payments`, so restoring one silently wipes
   `saved_groups` and `shirt_inventory`.

### F.11 Testing the shell toolkit

`shellcheck` (with a zsh-compatible ruleset) on every script, plus `bats-core` for the pure helpers:
`ek_name` clamping and determinism, `ek_state_*` transitions, prompt validation, colour disabling
under `NO_COLOR`/non-TTY.

Then a **mock `az` on `PATH`** that records every invocation to a log and replays canned JSON from a
fixture directory keyed by the argument vector, so the whole `deploy` flow runs end to end with no
Azure account. Same for `gh`, `dig`, and `curl`.

```bash
@test "deploy is resumable: interrupting at the easy-auth gate leaves it pending" {
  export AZ_MOCK_FIXTURES="$BATS_TEST_DIRNAME/fixtures/greenfield"
  export EK_GATE_INTERRUPT="easy-auth"          # mock manual.zsh quits at this gate
  run eventkit azure deploy --app poster-gallery --yes
  [ "$status" -ne 0 ]
  run jq -r '.steps[] | select(.id=="easy-auth") | .status' .eventkit/state.json
  [ "$output" = "pending" ]
  run jq -r '.steps[] | select(.id=="acr") | .status' .eventkit/state.json
  [ "$output" = "done" ]

  unset EK_GATE_INTERRUPT
  export AZ_MOCK_FIXTURES="$BATS_TEST_DIRNAME/fixtures/easy-auth-configured"
  run eventkit azure resume --app poster-gallery --yes
  [ "$status" -eq 0 ]
  # acr must NOT be re-created on resume
  run grep -c 'acr create' "$AZ_MOCK_LOG"
  [ "$output" = "0" ]
}
```

Runs in the Docker `test` target so `docker compose run --rm test` covers Python, JS, and shell with
one command locally and in CI.

### F.12 Build order (independently useful phases)

| Phase | Deliverable | Unblocks |
|---|---|---|
| 0 | `lib/{boot,color,log,prompt,state,name,az}.zsh` + bats harness + mock `az` + Docker `test` target | everything — and the phase to test hardest, since every later phase trusts `ek_name` and `ek_state_*` |
| 1 | `lib/manual.zsh` + `lib/verify.zsh` + the `easy-auth` and `acrpull` gates | lets the live-deployment auth posture be verified and documented **before** any provisioning code exists |
| 2 | `conf.zsh` + `steps.zsh` + steps `00`–`60` + `deploy`/`resume`/`status`/`doctor` + `poster-gallery`'s `app.conf` | one app fully managed end to end |
| 3 | App-side: `/healthz`, `/api/webhook/status`, dual-token, `/api/admin/tasks`, unified backup, Alembic, startup auth assertion | the `webhook` gate, `admin-task.yml`, `backup`/`restore` |
| 4 | `update`/`teardown`/`drift`/`adopt` + reusable `test.yml`/`deploy.yml`/`admin-task.yml`/`drift.yml` + `oidc` with split identities | the live migration (F.10) |
| 5 | `bootstrap`, `domain`, `secrets rotate`, `eject`, `logs`, `open`, remaining `app.conf` files | the other four apps; a sixth app becomes one TOML file |

**Phase 1 before phase 2.** The manual-step gate is the stated requirement, and it's also what lets
the undocumented Easy Auth configuration get verified and written down this week.

### F.13 Additional flag from this design

**`pipx run --spec <github tarball>` has no integrity story.** A mutable git tag over TLS with no
signature and no hash pin: anyone with push access can retag `v0.1.0` and every adopter's next
`deploy` runs new code with Azure credentials in the environment. Mitigations, in order of
preference: publish to PyPI and pin `==`; `--pip-args='--require-hashes'` where feasible; sign
releases (Sigstore / `gh attestation verify`); in CI pin by **commit SHA**, not tag; and
`doctor --verify-self` prints the resolved version + commit so a human can see what's running.

## G. Application repos — detail

### G.1 `ticket-reconciler`

Reconciles Drupal registrations against Eventbrite sales; runs front-desk check-in, swag, waivers.

```
ticket-reconciler/
  pyproject.toml  Dockerfile  docker-compose.yml  package.json  vitest.config.js
  event-profile.yaml  webform-schema.yml  .env.example
  README.md  LICENSE  CONTRIBUTING.md  SECURITY.md  CODEOWNERS
  .github/workflows/{ci.yml,deploy.yml,teardown.yml}
  deploy/app.conf
  alembic.ini  migrations/versions/{0001_init.py,0002_checkin_key_isodate.py}
  src/ticket_reconciler/
    __init__.py app.py (create_app) settings.py models.py schemas.py deps.py cli.py
    reconcile.py            # PURE: build_report(registrants, payments, profile) -> [ReportRow]
    routers/{reports,checkin,swag,groups,waivers,sync,webhook,export}.py
    sync.py                 # SyncPorts impl over eventkit.eventbrite
    static/{index.html,js/{dashboard,report-table,checkin,swag,groups,stats,export}.js}
  tests/{conftest.py,test_reconcile.py,test_checkin.py,test_swag.py,test_sync.py,
         test_webhook.py,test_migrations.py,fixtures/}
  tests/js/{reconcile-sort.test.js,checkin-cycle.test.js,export.test.js}
```

**Models** (from `ticketed/backend/models.py:4-25`)

`Registrant(IdentityMixin)`: `person_key` PK · `first_name`/`last_name` · `email_address` uniq ·
`tickets_sold_separately` bool · `drupal_sid` int · `serial_number` int · `registered_at` ·
`linked_payment_id` FK→payments.id · `destination_url` · `resolved_tier` str (new — cached
`resolve_tier()` output) · `checkin_status` JSON (`{ISO date: int 0-3}`) · `manually_exempt` bool ·
`waived` bool · `waiver_justification` · `swag_size` (renamed from `t_shirt_size`) ·
`replacement_swag_size` · `swag_checked_in` bool · `refund_override_attending` bool · `row_version` int.

`Payment`: `id` PK · `email` **indexed, not unique** (dropping `unique=True` from `models.py:29` —
one purchaser buying for two people is a 500 today; the HEAD commit worked around it by aggregating,
but the constraint is the bug) · names · `eventbrite_order_id`/`attendee_id` · `status` · `paid_at` ·
`gross_amount`/`net_amount` int cents.

`SyncLog` · `SavedGroup(id, name uniq, names JSON)` — a real JSON column, not a serialized string ·
`SwagInventory(size PK, total_count)`.

**Routes** (all `Depends(auth.require)` unless noted)

| M | Path | Auth | Purpose |
|---|---|---|---|
|GET|`/`, `/static/*`|user|dashboard shell|
|POST|`/api/drupal-webhook`|webhook token|upsert registrant|
|GET|`/api/reports/registrations`|user|reconciliation rows|
|GET|`/api/reports/stats`|user|stat cards incl. gross cents|
|GET|`/api/reports/export.xlsx`|user|server-side Excel|
|POST|`/api/reports/link` · `/unlink`|user|manual payment linkage|
|POST|`/api/sync`|user|Eventbrite pull|
|GET|`/api/sync/log`|user|last N syncs|
|POST|`/api/checkin`|user|`{person_key, day_key (ISO), state}`|
|GET/POST|`/api/swag/inventory`|user|counts|
|POST|`/api/swag/checkin`|user|mark issued|
|PATCH|`/api/registrants/{k}/swag-replacement`|user|size swap|
|POST|`/api/registrants/{k}/exemption` · `/waive` · `/refund-override`|user|three toggles|
|GET/POST/DELETE|`/api/groups[/{name}]`|user|saved filters|
|GET|`/api/changes?since=<cursor>`|user|`eventkit.realtime` poll feed|
|WS|`/ws/changes?ticket=`|HMAC ticket|opt-in push|
|—|`/api/backup/*`|user|mounted `eventkit.backup` router|
|GET|`/api/webhook/status`|token or user|counters only, no PII|
|GET|`/healthz`|none|liveness only|

**What moves:** report engine `main.py:448-579` → `reconcile.py` as a pure function taking lists +
profile (no `Session`, no `settings`); stats `:583-656`; link/unlink `:657-694`; check-in `:773-801`;
swag `:802-867`; toggles `:895-1026`; groups `:1027-1086`; backup/restore `:1124-1316` →
`eventkit.backup`; `backend/eventbrite.py` → `eventkit.eventbrite`; auto-sync loop `:47-75` →
`create_app` lifespan task; `backend/notifications.py` → `eventkit.notify`;
`backend/schema_parser.py` (incl. the 55-line embedded `DEFAULT_SCHEMA_YAML`) →
`eventkit.drupal.FieldMap` + repo-local `webform-schema.yml`.

**Deleted as CAARMS-specific:** `main.py:499-511` (`CODE_AFFILIATE`/`CODE_GENERAL` fallbacks, the
`princeton.edu` email branch, the `caarms-2026-tickets-{id}` slug) → `profile.ticketing.resolve_tier`;
hardcoded `status_order` dict `:568-576` → `profile.ticketing.status_order`; the `"6/28"…"7/1"` day
keys (`app.js:1258-1262`); `site_name`/`site_slogan` defaults (`config.py:8-9`);
`frontend/images/caarms_0.png` (1.0 MB, served unoptimized); `.github/workflows/clear_data.yml`;
**`POST /api/admin/clear` (`main.py:1087-1123`) deleted outright.**

**Migration `0002_checkin_key_isodate`:** rewrite every `checkin_status` JSON key `"M/D"` → the
profile's ISO day key **by position, not by parsing** — `"6/28"` is ambiguous across years and both
`"7/1"` and `"07/01"` appear. Fail the migration loudly if a key is neither in the legacy set nor
already ISO. This is exactly the migration the hand-rolled migrator cannot express.

**Realtime:** polling replaces WebSocket as the default. Today four `broadcast_*` helpers
(`main.py:713-772`) iterate a module-global `active_checkin_sockets` list, which breaks the moment
Azure runs 2 instances or recycles the worker, and silently swallows every send error. Default is
`GET /api/changes?since=` at 3s while the check-in tab is focused, 30s when blurred. WS is opt-in via
`profile.realtime.websocket`, and even then polling stays the fallback so a dropped socket can't
strand the front desk mid-registration.

**Excel:** keep SheetJS in-browser *and* add `/export.xlsx`. The browser path exports only filtered
rows (what staff want); the server path is the auditable full dump. Vendor SheetJS with SRI.

**Tests.** pytest: `build_report` truth table — manual link wins over email match; a
manually-claimed payment must NOT also match its own email-owner (the `main.py:471-474` guard) nor a
different registrant; each of the 7 derived statuses; leftover `status=="paid"` → `Unmatched` while
leftover `refunded` is suppressed; sort stability; `resolve_tier` across all 5 legacy slugs plus
anon/PU/student/domain cases; check-in cycle 0→1→2→3→0 with ISO keys; migration 0002 round-trip on a
fixture DB containing both `"6/28"` and `"07/01"`; swag inventory going negative returns 400 not a
negative count; webhook rejects absent/bad token; `/api/admin/clear` returns 404.
vitest: report-table sort matches server order; `esc()` on a registrant named `<img onerror>`; day
columns rendered from profile keys; SheetJS export column set and currency cells.

**README outline:** What it reconciles → screenshots → quickstart (docker compose, seeded profile) →
`event-profile.yaml` ticketing section → Drupal Remote Post setup (with the `headers:` nesting
warning) → Eventbrite token scopes → Azure deploy → backup/restore → check-in day-key migration →
troubleshooting (unmatched rows, duplicate emails) → security notes.

### G.2 `lodging-planner`

```
lodging-planner/
  <shared skeleton>  event-profile.yaml  webform-schema.yml  deploy/app.conf
  migrations/versions/{0001_init.py,0002_row_version.py}
  src/lodging_planner/
    app.py settings.py models.py schemas.py deps.py cli.py
    rules.py        # pure engine, mirrors the client 1:1
    names.py        # normalized name matching (replaces the substring test)
    routers/{rooms,assignments,registrants,rules,webhook}.py
    static/{index.html,js/{board,grid-view,list-view,room-form,rules-panel,writein}.js}
  tests/{test_rules.py,test_rooms.py,test_bulk_create.py,test_concurrency.py,test_names.py}
  tests/js/{board-dnd.test.js,rules-parity.test.js,room-reorder.test.js}
  tests/fixtures/rules-cases.json     # shared by pytest AND vitest
```

**Models.** `Attendee(IdentityMixin)`: `person_key` PK · names · `email_address` uniq ·
`home_institution` · `attendee_status` · `student` bool · `lodging` bool · `gender_identity` ·
`roommate_preference` · `identified_roommate` · `room_id` FK · `is_write_in` bool ·
`drupal_sid`/`serial_number` · `registered_at` · `row_version` int.
`Room`: `id` PK uuid · `name` uniq · `capacity` int · `room_gender` · `held_by` · `comments` ·
`category` · `sort_order` int · `created_at` · `row_version` int.
`RuleWaiver` (new): `id` · `room_id` · `rule_code` · `justification` · `waived_by` · `waived_at` — so
a planner acknowledges "yes, this couple shares a mixed-gender room" once instead of living with a
permanent warning.

**Routes** (all authed)

| M | Path | Purpose |
|---|---|---|
|GET|`/`|board|
|GET/POST|`/api/rooms`|list / create|
|POST|`/api/rooms/bulk`|zero-pad-preserving auto-increment, count 1–20|
|PATCH/DELETE|`/api/rooms/{id}`|edit (occupied-lock) / delete + unassign|
|POST|`/api/rooms/reorder`|`[{id, sort_order}]` + board-level etag|
|POST|`/api/assignments`|`{person_key, room_id\|null, row_version}`|
|GET|`/api/attendees?lodging=yes\|no\|all`|roster|
|POST|`/api/attendees`|write-in|
|PATCH|`/api/attendees/{k}/lodging`|promote / demote|
|DELETE|`/api/attendees/{k}`|write-ins only|
|GET|`/api/rules`|**server-side** findings for all rooms|
|POST|`/api/rules/waivers`|acknowledge a finding|
|GET|`/api/changes?since=`|board change feed|
|POST|`/api/drupal-webhook`|token; upsert|
|—|`/api/backup/*`, `/api/webhook/status`, `/healthz`||

**What moves:** `Room` + lodging fields `posted/backend/models.py:21-57`; room CRUD `main.py:384-601`
(bulk-create zero-pad `:438-447`, occupied lock `:512-524`, reorder `:557-571`, assign + capacity
`:572-602`); roster/write-in/promote `:603-733`; all of `frontend/admin_lodging.html` split into ES
modules; rules engine `admin_lodging.html:1571-1650` → `rules.py`.

**Deleted:** hardcoded gender vocab and `"Speaker Room"`/`"Student Room"` → `profile.lodging.vocab`;
hardcoded rule severities → `profile.lodging.rules[].severity`; the literal capacity dropdown
(`:804-809`) and the 20 literal `<option>` elements for bulk count (`:831-852`); CAARMS title/logo;
the shared-DB coupling to nametags and posters. Also fix the CSS-class derivation at `:1216`/`:1337`,
which uses a non-global `.replace(" ", "-")` so multi-word values only get their first space replaced.

**Three fixes, in detail:**

1. **Optimistic concurrency.** No version check exists anywhere in `main.py:384-733`. Every mutating
   route takes `row_version`, returns 409 + the fresh entity on mismatch; the client shows "Someone
   else moved Alice — reload?" and re-fetches. Room reorder sends the whole ordered list with a
   single board-level etag (max `row_version` across rooms) because per-row versions can't express an
   ordering conflict.
2. **Server-side rules as source of truth.** `rules.py` owns the logic; `GET /api/rules` returns
   findings; the client module is a thin renderer plus optimistic local re-evaluation for drag
   feedback. `tests/fixtures/rules-cases.json` is committed once and consumed by both suites, with
   `rules-parity.test.js` asserting identical finding codes.
3. **Name matching.** `admin_lodging.html:1550` and `:1562-1569` use bidirectional `includes()`, so
   `"Bob"` matches `"Bobby Jones"` and `find()` silently picks the first of two Joneses. Replace with:
   exact normalized (casefold, strip accents, collapse whitespace, drop punctuation) → last-name +
   first-initial → token-set. Multiple hits return `AMBIGUOUS` with a candidate list rather than
   guessing. Roommate reciprocity compares resolved `person_key`s, not strings.

**Datastore: keep SQLite default.** Lodging is a pre-event batch activity with 2–4 concurrent
planners and single-digit writes per minute. The reason people reach for Postgres here is
concurrent-write safety, and that is solved by `row_version`, not by the engine. Test
`postgresql+psycopg://` in CI and document switching at >5 simultaneous planners or when PITR is
required. (This overrides an earlier "Postgres recommended for lodging" recommendation.)

**Tests.** pytest: each rule code fires/doesn't across a matrix; severity honoured from profile;
waiver suppresses; capacity boundary (`==`, `>`); bulk create `"Room 007"`→`"Room 008"` width
preserved and `"Butler"`→`"Butler 1"`, duplicate-name rejection listing all collisions; occupied-room
name/capacity/gender edit → 400 while comments/held_by/category → 200; delete room unassigns; assign
beyond capacity → 400; stale `row_version` → 409; name matcher — `"Bob"` must NOT match
`"Bobby Jones"`; `"bob jones"`, `"Jones, Bob"`, `"BOB JONES "` must; two Joneses → `AMBIGUOUS`.
vitest: drop handler sends version and handles 409 with toast + refetch; grid↔list parity; reorder
produces contiguous `sort_order`.

**README outline:** What it does → board tour → quickstart → rules reference table (code, severity,
meaning, how to waive) → name-matching semantics → concurrency model → required webform fields →
SQLite vs Postgres → deploy → backup → **privacy** (gender identity and roommate requests are
sensitive; who can see this app).

### G.3 `nametag-press`

```
nametag-press/
  <shared skeleton>  event-profile.yaml  webform-schema.yml  deploy/app.conf
  src/nametag_press/
    app.py settings.py models.py schemas.py deps.py cli.py
    layout.py        # LAYOUTS: dict[str, Layout] — SINGLE source of geometry
    render_pdf.py    # ReportLab, consumes Layout
    fit.py           # pure autoshrink: fit_text(text, max_pt, min_pt, width) -> pt
    branding.py      # logo resolution + svglib
    routers/{badges,registrants,logos,webhook}.py
    static/{index.html,js/{roster,filters,tallies,preview}.js,layouts.json}
  tests/{test_layout.py,test_fit.py,test_render_pdf.py,test_logos.py,test_roles.py}
  tests/js/{layouts-parity.test.js,tallies.test.js,preview.test.js}
```

**Models.** `Registrant(IdentityMixin)`: `person_key` PK · names · `email_address` uniq ·
`home_institution` · `attendee_status` · `student` · `presenting_poster` ·
`drupal_sid`/`serial_number` · `registered_at`.
`BrandingAsset`: `slot` PK (`primary`|`sponsor`) · `filename` · `content_type` · `bytes` LargeBinary ·
`uploaded_at` — **in the DB, not the filesystem.** Today uploads land in
`frontend/static/images/badge_{slot}_custom.{ext}` (`main.py:342-370`), which is not the Azure Files
mount, so they vanish on container restart.

**Routes** (authed except `/healthz`): `GET /` · `GET /api/registrants` · `GET /api/layouts` ·
`GET /api/badges.pdf?template=&keys=&sort=` · `GET /api/badges/blank.pdf?template=&sheets=` ·
`GET/PUT/DELETE /api/branding/{slot}` · `POST /api/drupal-webhook` (token) · `/api/backup/*` ·
`/api/webhook/status` · `/healthz`.

**Avery geometry** (currently duplicated between `main.py:937-976` and `admin_nametags.html:213-330`):

| Template | Card | Grid | Sheet padding / gaps | Name / affiliation pt |
|---|---|---|---|---|
| `74541` (default today) | 4.0 × 3.0 in | 2 × 3 = 6-up | pad 1.0 in top/bottom, 0.25 in sides; gap 0 | 22 / 11–12 |
| `5392` (= 74536) | 4.0 × 3.0 in | 2 × 3 = 6-up | identical to 74541 | 22 / 12 |
| `5395` | 3.375 × 2.33 in | 2 × 4 = 8-up | pad 0.5 in top/bottom, 0.75 in sides; gaps 0.1 × 0.25 in | 16 / 9–10 |

`.badge-sheet` is fixed at 8.5 × 10.95 in with `page-break-after: always`. `74541` cards use a solid
`1px #000` border while `5392`/`5395` use `1px dashed #ccc`; 6-up cards use `margin-right/-bottom:
-1px` to collapse shared borders.

**What moves:** `generate_nametags_pdf_bytes` `main.py:911-1152` → `render_pdf.py`; geometry
`:937-976` → `layout.py`; `get_role_details` `:1041-1049` → `profile.roles[].{label,color}`;
`draw_card` `:1051-1118`; PDF routes `:1155-1231`; logo upload `:342-370`; roster route `:334-341`;
roster table/filters/tallies `admin_nametags.html:600-946`.

**Deleted:** `caarms_0.png`/`pu-logo.svg` as *defaults* (profile-supplied instead), the literal
`"CAARMS 2026"` at `admin_nametags.html:968` and `main.py:1068`, the hardcoded `#f58025`/`#1a1a1a`
role colours, and the role→label mapping duplicated at `getRoleLabel`/`getRolePrintClass`
(`:1203-1214`) and `get_role_details` (`main.py:1041-1049`).

**Drop the browser-print path.** Geometry and card content are defined twice, and the CSS version
can't reproduce ReportLab's per-line autoshrink, so a long name prints differently in the two paths —
which is precisely the failure that ruins a sheet of Avery stock. Keep one renderer and replace
browser printing with an in-browser PDF preview (`<iframe src="/api/badges.pdf#toolbar=0">`) plus the
browser's own PDF print dialog. Staff keep "see it before you print"; you keep one geometry.
`layouts.json` is generated from `layout.py` and asserted equal in CI so JS can still draw the
on-screen selection grid without owning dimensions.

**Swag: `ticket-reconciler` owns it exclusively.** `t_shirt_size` on `posted`'s `Registrant`
(`models.py:29`) is stored, backed up, and never rendered anywhere — dead weight. Inventory,
replacement, and issuance all belong where the check-in desk is. If an event wants size on the badge,
that's `profile.nametags` referencing a roster value, not a second inventory system. Two apps
counting shirts is how you oversell mediums.

**Tests.** pytest: `LAYOUTS["74541"]` and `["5392"]` identical, 2×3, 4.0×3.0 in, margins 0.25x/1.0y,
gaps 0; `["5395"]` 2×4, 3.375×2.33, 0.75x/0.5y, gaps 0.25x/0.1y; every layout fits letter
(`margin*2 + cols*w + (cols-1)*gap <= 8.5in`); `fit_text` monotonic, floors at 12pt from a 22 start
and 10pt from 16, affiliation floors at 8pt; page count = `ceil(n / cards_per_page)`; blank sheets
1..N; SVG sponsor logo parses and a corrupt SVG degrades without a 500 (today `main.py:1038-1039` is
a bare `except: pass` that hides this); role→colour from profile; unknown role → neutral; PDF output
starts `%PDF` with the expected page count via pypdf.
vitest: `layouts.json` deep-equals a fixture exported from `layout.py`; role tallies; filter chips;
preview iframe URL encodes selected keys.

**README outline:** Purpose → supported Avery stock table → **print calibration guide** ("print one
blank sheet on plain paper, hold it to the light against a real sheet") → quickstart → branding logos
→ role labels/colours in the profile → webform fields → deploy → why there is no browser-print mode.

### G.4 `link-forge`

**Decision: its own repo, stateless, no database.** Not a route inside `ticket-reconciler` — the
audience is event and finance staff who should not hold an authorization that also exposes payment
amounts and gross revenue, and reimbursement links get used for weeks after the reconciler is torn
down; it's also the only app with zero schema, so bolting it onto a migration-bearing app taxes it
forever. Not a bare static page either, because the tokenized speaker links are bearer credentials
that must not be baked into a committed HTML file — `links-for-speakers.html` is exactly that mistake
— and staff need auth in front of the roster.

```
link-forge/
  <shared skeleton minus alembic/migrations>  event-profile.yaml  app.conf
  src/link_forge/
    app.py settings.py deps.py
    templating.py   # render(template, person) with a strict placeholder allowlist
    roster.py       # source: reconciler API | pasted CSV | uploaded CSV (in-memory only)
    tokens.py       # Key Vault / env-backed token map; never logged, never persisted
    routers/{links,roster}.py
    static/{index.html,js/{roster,link-cards,copy,csv-paste}.js}
  tests/{test_templating.py,test_roster.py,test_tokens.py,test_no_pii_logs.py}
  tests/js/{copy.test.js,csv-paste.test.js}
```

No models. `SPEAKER_LINK_TOKENS` is a JSON env var / Key Vault secret mapping lowercased email →
Drupal token, loaded at startup into memory.

**Routes** (all authed): `GET /` · `GET /api/roster` · `POST /api/roster/paste` (CSV body →
normalized rows, no persistence) · `GET /api/links?email=&kinds=` · `GET /api/link-kinds` ·
`GET /healthz`.

**What moves:** `posted/frontend/admin_reimbursement.html:289` — the entire feature, one URL template
with `Business_Purpose=CAARMS%202026&Departure_date=2026-06-28&Return_Date=2026-07-01` — becomes
`profile.links.reimbursement`; roster table `:150-287`. From Drupal: the media-release PowerForm and
slide-upload deep links (`registration-receipt-email-body.html`, the Speaker-only block) and the
tokenized prefill pattern from `links-for-speakers.html` → `profile.links.speaker_prefill` with
`{token}` resolved from the secret map.

**Deleted:** the CAARMS dates/purpose in `:289`, the `orfe.princeton.edu` host, the
`caarms.princeton.edu` host in token links, and the committed token values. Also the **fourth** copy
of the Princeton affiliation rule at `admin_reimbursement.html:230-238`.

**Fragment vs query, enforced not just documented.** `#`-fragment params (reimbursement) are never
sent to a server: not in access logs, not in `Referer`, not in a CDN. `?`-query params (slide upload,
`?token=`, the DocuSign PowerForm) **are** sent, and land in Drupal's webserver log, App Service
logs, and any proxy between. So each kind declares `param_style` and `sensitivity`; link-forge
**refuses** to render a `bearer` kind with `param_style: fragment` (a fragment defeats prefill
anyway) and shows a warning banner on any `query` + `pii` kind — the slide-upload link puts a
participant's email in log lines today. Recommend the Drupal side switch slide-upload prefill to
POST-then-redirect or a short-lived opaque token. link-forge logs only `kind` plus a SHA-256 prefix of
the email, never a rendered URL; `test_no_pii_logs.py` asserts no `@` reaches caplog.

**Tests.** pytest: templating substitutes and URL-encodes name/email/dates; an unknown placeholder
raises rather than silently blanking; `sensitivity: bearer` with no token for that email → 404, never
an unsigned link; fragment+bearer rejected; CSV paste tolerates `"Last, First"`, quoted commas, and a
BOM; roster proxy failure degrades to paste mode; log scrubbing.
vitest: clipboard success and the `document.execCommand` fallback for non-TLS origins; per-kind card
visibility by role; paste parser parity with the Python fixture.

**README outline:** What it makes → link kinds table → `profile.links` reference → fragment vs query
and why it matters for PII → where speaker tokens come from and how to rotate them → quickstart → no
database, no backups → deploy → **post-event: keep this one running.**

### G.5 `poster-gallery`

```
poster-gallery/
  <shared skeleton>  event-profile.yaml  webform-schema.yml  deploy/app.conf
  src/poster_gallery/
    app.py settings.py models.py schemas.py deps.py
    feed.py    # RSS 2.0 builder
    theme.py   # local theme bundle resolution
    routers/{public,admin,webhook}.py
    cli.py     # `poster-gallery import <file>` (replaces import_existing.py)
    static/{index.html,admin.html,js/{directory,presenter-detail,mathjax-boot,admin-table}.js,
            theme/{host-theme.css,tokens.css}}
  tests/{test_public_schema.py,test_feed.py,test_webhook_visibility.py,test_import.py}
  tests/js/{directory-filter.test.js,detail-route.test.js,escaping.test.js}
```

**Model.** `Presenter(IdentityMixin)`: `person_key` PK · `email_address` uniq · names ·
`poster_title` · `faculty_adviser_name` · `abstract` Text · `drupal_sid`/`serial_number` ·
`is_visible` bool · `registered_at` · `updated_at`.

**Routes**

| M | Path | Auth | Purpose |
|---|---|---|---|
|GET|`/`|none|directory (MathJax 3)|
|GET|`/api/presenters`|**none**|`PublicPresenter` — no email|
|GET|`/api/presenters/{person_key}`|none|single, public schema|
|GET|`/feed.xml`, `/rss.xml`|none|RSS 2.0|
|GET|`/admin`|user|admin table|
|GET|`/api/admin/presenters`|user|`AdminPresenter` — email, `is_visible`, submission URL|
|PATCH|`/api/admin/presenters/{k}`|user|toggle visibility, fix typos|
|POST|`/api/drupal-webhook`|token|upsert / soft-hide|
|—|`/api/backup/*`, `/api/webhook/status`, `/healthz`|||

**What moves:** `Presenter` `posted/backend/models.py:5-19`; RSS `main.py:68-124` → `feed.py`; public
list `:126-136`; webhook + `is_visible` soft-delete `:137-217`; `import_existing.py` → `cli.py` on
`eventkit.importer`; public page `frontend/index.html`.

**PII fix.** `GET /api/presenters` is unauthenticated and `PresenterResponse`
(`posted/backend/schemas.py:78-92`) includes `email_address`, `drupal_sid`, `serial_number`, and
`is_visible` — every presenter's address is scrapable and internal submission IDs leak. Split
`PublicPresenter{person_key, first_name, last_name, poster_title, faculty_adviser_name, abstract}`
from `AdminPresenter`. Add a **trip-wire test** that fails if `PublicPresenter.model_fields` ever
gains a field outside an explicit allowlist — the original bug is one careless `response_model` reuse.

**Deleted:** `download_assets.py` in full; `frontend/static/css/*` (Bootstrap plus five mirrored
Drupal files, vendored copies of someone else's site — note they're 0-byte or near-empty in this
checkout, i.e. the mirror had already partly failed); `"CAARMS 2026 Poster Presenters"` and the feed
description at `main.py:88-90`; the `/poster-presenters` back-link hardcoded at `index.html:272,282`.

**Replacing the Drupal CSS mirror:** `eventkit.ui` Paper Tiger tokens are the baseline, plus an
optional **committed** `static/theme/host-theme.css` an adopter writes once by setting ~20 CSS custom
properties (`--pt-color-brand`, `--pt-font-heading`, header/footer slots). Ship
`examples/caarms-2026/host-theme.css` as the worked example plus a "how to extract these values from
your Drupal theme with devtools" recipe. "Look like our Drupal site" is a design-token problem; a
build-time mirror of a CMS you don't control produces silent visual breakage on every upstream theme
release and a supply-chain path from their CDN into your page. For adopters who insist, document an
*optional offline* `eventkit ui vendor-theme <url>` that writes into the repo and is reviewed in a
PR — never at runtime.

**Tests.** pytest: the public payload has exactly the allowlisted keys and no `@` anywhere in the
JSON; hidden presenters excluded from list, detail (404), and feed; `presenting_poster` falsy values
(`""`, `no`, `No`, `0`, `off`) all soft-hide while `1/true/yes/on/checked` all show
(`main.py:160-162`); soft-hidden then re-submitted → visible again with updated fields; feed validates
as RSS 2.0 (guid `isPermaLink="false"`, RFC-822 `pubDate`, `atom:link` self); an abstract containing
`$\alpha < \beta$` and `]]>` survives XML escaping; importer idempotent on re-run, dedupes by
`person_key`.
vitest: MathJax typesets *after* async render (a race the current page has), `?presenter=<uuid>` deep
link expands the right card, `esc()` on `<script>` in a poster title, filter/search.

**README outline:** Public gallery → RSS → quickstart → theming your gallery (token table + devtools
recipe) → webform fields and the presenting toggle → importing an existing roster → **public vs admin
data (emails are never public)** → deploy → backup.

## H. Content repos

### H.1 `drupal-event-forms`

```
drupal-event-forms/
  README.md  LICENSE(CC-BY-4.0)  CONTRIBUTING.md  CODEOWNERS
  webforms/{registration.yaml,group-registration-router.yaml,travel-agency.yaml,
            poster-presenter.yaml,speaker-bios-talks.yaml}
  handlers/remote-post/{ticket-reconciler,lodging-planner,nametag-press,poster-gallery}.yaml
  emails/{registration-receipt.html.twig,registration-receipt-settings.md}
  docusign/media-release-powerform.md
  contracts/{registration,poster,lodging,nametag}.fieldmap.yml
  docs/{IMPORT.md,CONDITIONAL-TICKETING.md,TOKENS.md,PRIVACY.md,KNOWN-ISSUES.md,CHANGELOG.md}
  tools/{validate_yaml.py,check_fieldmap_sync.py,redact.py}
  .github/workflows/validate.yml
```

**The conditional-ticketing decision table** (from `registration.yaml`'s `computed_twig`
`destination_url`, which is the single most valuable piece of documented logic in the whole stack):

1. If `[current-page:query:destination_url]` is present, pass it through — this is how the group
   codes `CODE_GROUP2_STUDENT` / `CODE_GROUP_STUDENT` / `CODE_GROUP_FACULTY` arrive.
2. Exclusion gate: `attendee_status` of `Organizer` or `Speaker` → emit empty (no ticket).
3. Otherwise cascade: `CODE_GENERAL` (attendee, entire event, not logged in) · `CODE_STUDENT_NOPOSTER`
   (non-PU student, not presenting) · `CODE_STUDENT_POSTER` (non-PU student, presenting) · `CODE_BANQUET`
   (non-student, banquet only, not logged in) · `CODE_AFFILIATE` (logged-in PU non-student).

`ticket_price` map: `CODE_GENERAL` $160 · `CODE_GROUP_FACULTY` $420 · `CODE_BANQUET` $110 · `CODE_AFFILIATE` $110
· `CODE_STUDENT_NOPOSTER` $85 · `CODE_STUDENT_POSTER` $60 · `CODE_FAMILY` $60. Note `CODE_FAMILY` has a
price but is never produced by `destination_url`, and `CODE_GROUP_STUDENT`/`CODE_GROUP2_STUDENT` have no price entry
(free). The slug is not a URL — the receipt email turns it into
`https://caarms.princeton.edu/confirmation/next-steps/{slug}`.
Also: lodging is offered to **non-Princeton students only** (`lodging` is visible when
`student == Yes` AND the email does *not* match `princeton.edu`).

**Sanitization, enforced by `tools/redact.py` in CI:** no `X-Drupal-Webhook-Token` values (the three
`.txt` recipes carry truncated ones — replace with `${WEBHOOK_TOKEN}`), no `?token=` bearer strings,
no live `azurewebsites.net` hostnames, no `sites/g/files/toruqf4381` paths, no WAF bypass header
name/value, no real attendee data in the receipt sample. Discount codes stay in `registration.yaml`
because the Twig genuinely needs them — flag in the README that they are **semi-public by nature**,
since the browser receives the computed value, so don't document them as secrets.

**Import path.** `drush webform:import` imports *submissions*, not definitions — document the two
real paths: (1) config import via `drush cim --partial --source=webforms/` with each file renamed
`webform.webform.<id>.yml` and wrapped in the full config envelope (`tools/` provides the wrapper,
since these exports are element-only `elements:` bodies); (2) manual UI import — Webform UI → Build →
**Source** tab, paste, save, then re-create handlers and emails by hand from `handlers/` and
`emails/`. **Lead with path 2** — it's what CAARMS actually did, and path 1 requires matching Drupal +
Webform module versions and will happily overwrite unrelated config. Required contrib: Webform, CAS
(for `[cas:attribute:*]`), Captcha, and the Twig-enabled `computed_twig` element.

**FieldMap sync contract.** `contracts/*.fieldmap.yml` is the normative artifact: per app, a
`webform_key → canonical_field` list with `required` and `type`. Each app declares
`FIELDMAP_CONTRACT_VERSION` and ships its own `webform-schema.yml` derived from it. Three enforcement
layers: (a) `check_fieldmap_sync.py` asserts every contract key exists in the referenced webform YAML
and errors on orphans — catching the "someone renamed an element in Drupal" class; (b) each app's CI
pulls the contract at a pinned tag (`drupal-event-forms@v2026.1`) and asserts superset compatibility;
(c) at runtime `eventkit.drupal` logs `unmapped_keys` per webhook and the app exposes them at
`/api/webhook/status`, so a live rename surfaces as a warning within one submission instead of as
silently dropped data. Versions are `YYYY.N`; breaking renames bump major and require a PR touching
both repos.

**The travel form: publish with a warning and a redesign note.** It collects passport number, country
of issue, expiry, DOB, and gender and emails them in an HTML body to an agency. `docs/PRIVACY.md` must
state plainly: this is a passport-data collection form; Drupal webform submissions persist in the DB
and in mail logs; the emailed body is plaintext over SMTP. Ship it with `#results_disabled: true`
guidance, mandatory purge-after-N-days, restricted `#access` on results, and an explicit "prefer the
travel agency's own secure portal; use this only if you must" banner. Publishing the *structure* is
safe and useful; the repo must not imply the *pattern* is safe.

**Bugs to fix and regression-assert** in `tools/validate_yaml.py` (no doubly-nested `#states`; every
`#states` comparison value must appear in the referenced element's `#options` keys):

1. `registration.yaml`'s `actions` has `'#states': {'#states': {disabled: …}}`. Drupal sees an
   unrecognized inner key, so the intended "disable submit for an un-logged-in Princeton
   email/institution" rule never fires — meaning PU affiliates could and did submit unauthenticated
   registrations that invalidate their exemption. Unwrap one level.
2. `morgan-state-university-group-registration-form.yaml`'s `chair_notice` `#states` matches
   `…tickets-1986024760521?discount=CODE_GROUP2_CHAIR` while the actual radio option key is
   `…tickets-1993012012580?discount=CODE_GROUP2_CHAIR`, so the chair never sees the "purchase on behalf of
   the group" instruction. Match the option key exactly — better, key the radios on short slugs and
   compute the URL in Twig so they can't drift. Also reconcile the student-count discrepancy: the form
   copy says 8 students + 1 faculty, `administrative-utilities.html` says the $950 chair ticket
   unlocks **14** Morgan State student spots.

Note also that `morgan-state-…yaml` uses **full URLs as radio option keys**, which is what made bug 2
possible.

### H.2 `event-stack`

```
event-stack/
  README.md  LICENSE  CODEOWNERS  CONTRIBUTING.md
  docs/
    ARCHITECTURE.md  QUICKSTART.md  EVENT-PROFILE-SPEC.md  COMPATIBILITY.md
    CHOOSING-TOOLS.md  RUNBOOK.md  SECURITY-PRIVACY.md  DATA-RETENTION.md
    AZURE.md  GLOSSARY.md
    ADR/{0001-independent-databases.md,0002-sqlite-default.md,
         0003-polling-first-realtime.md,0004-one-parser-in-eventkit.md,
         0005-link-forge-stateless.md,0006-single-pdf-renderer.md,
         0007-alembic-over-hand-rolled.md,0008-no-bundler.md}
  diagrams/{stack.mmd,dataflow.mmd,timeline.mmd}
  examples/caarms-2026/{event-profile.yaml,host-theme.css,webform-schema.yml,README.md}
  scripts/{provision-event.sh,verify-stack.sh,collect-versions.sh,whois-person.sh}
  compose/docker-compose.all.yml
  .github/workflows/{docs-link-check.yml,profile-validate.yml}
```

**Architecture diagram**, three bands (committed Mermaid so it renders on GitHub):
*Top — Drupal (host CMS)*: registration webform, group router, travel form, speaker-bios form,
receipt email; four Remote Post arrows leave it, each labeled with its own token and target path.
*Middle — the apps*, each a box with its **own** DB cylinder beneath it, visually separate (that's
the point), all sitting on a shared `eventkit` bar spanning the band; `link-forge` has no cylinder.
*Right edge — externals*: Eventbrite (bidirectional, to `ticket-reconciler` only), DocuSign PowerForm
(dashed — link-only, no API), SMTP/Resend (from `eventkit.notify`), Azure Files (dashed to each
SQLite cylinder). *Bottom — humans*: Registrant → Drupal; Front desk → `ticket-reconciler`; Lodging
planner → `lodging-planner`; Print staff → `nametag-press`; Finance → `link-forge`; Public →
`poster-gallery`. Second diagram: a T-8-weeks → T+2-weeks timeline swimlane.

**`provision-event.sh`** — 8 idempotent, re-runnable steps: scaffold the profile from the example →
validate → choose apps interactively → `eventkit azure bootstrap` (RG, plan, storage, Files shares,
Key Vault) → per app: web app, `/home` mount, settings, minted webhook token, and a printed Remote
Post config block ready to paste → deploy via GitHub OIDC → `verify-stack.sh` → print the runbook
checklist with real URLs filled in. Target: 30 minutes to a working stack.

**`verify-stack.sh`** — `/healthz` on each app; post a synthetic webform submission to each webhook
and assert a row appears; assert anonymous access is denied on admin routes; assert no `@` appears in
`GET /api/presenters`.

**`EVENT-PROFILE-SPEC.md`** — normative: every key, type, default, which app reads it, required-per-app;
the "secrets are env var *names* only" rule; the JSON Schema that validates it; migration notes
between profile versions; three worked examples (single-day workshop, multi-day conference with
lodging, virtual event).

**`COMPATIBILITY.md`** — matrix of `eventkit` minor × app version × `drupal-event-forms` contract
version × Python/Drupal/Webform-module versions, plus a "known-good sets" table (the thing adopters
actually read) and a support window of the last two contract versions.

**`CHOOSING-TOOLS.md`** — a question tree (paid or conditional tickets? → `ticket-reconciler`;
housing attendees? → `lodging-planner`; printing badges? → `nametag-press`; per-person
forms/reimbursements/releases? → `link-forge`; public poster or talk directory? → `poster-gallery`),
three named bundles (Minimal = `link-forge` only; Conference = all five; Symposium = reconciler +
nametag-press), and an explicit **"you do not need all five"** up front.

**`RUNBOOK.md`** — what staff actually did, with owners, timing, the exact route used, and a "how you
know it worked" check per phase: registration opens (Drupal, T-8w) → conditional Eventbrite purchase,
`Pending` rows reviewed daily and chased at T-3w → Eventbrite sync verified, `Unmatched` triaged
weekly → rooms created and bulk-generated at T-3w, assignments finalized T-5d with the rules panel
clean or explicitly waived → nametags printed the day prior (blank-sheet calibration first, print by
role, spares for walk-ins) → front-desk check-in per day key, swag issuance, on-site waivers with
justification → post-event: reimbursement and media-release links sent, refund overrides processed,
poster gallery stays up, backups pulled, apps torn down except `link-forge` and `poster-gallery`.

**`SECURITY-PRIVACY.md`** — a data inventory table (field → app → sensitivity → retention → consent
note): emails everywhere; gender identity and roommate requests (`lodging-planner`, sensitive, delete
at T+30d); dietary restrictions; passport number / DOB / country of issue (**Drupal only, never in
any app DB**, purge at T+30d); payment amounts (`ticket-reconciler`; **no card data ever touches
these apps** — Eventbrite holds it). Plus: Easy Auth + allow-list is the only auth; no public write
endpoints except token-verified webhooks; token rotation procedure; the fragment-vs-query logging
rule; log scrubbing; **a backup download is a full PII export, treat it as one**; an incident
checklist; a deletion-request procedure spanning all databases (the real cost of independent DBs —
name it, don't bury it); and a threat-model note that Remote Post tokens are bearer secrets sitting in
Drupal config, visible to any Drupal admin.

Also document the **single-container all-in-one compose deployment** as a first-class option
(separate DB files, separate ASGI apps behind one proxy). Five Azure Web Apps for a one-week event is
real money and five things to patch; the per-app repos are right, but per-app *hosting* shouldn't be
mandatory.

## I. Cross-cutting

### I.1 Shared per-repo skeleton

Identical in all five app repos (`link-forge` omits alembic/migrations):

`pyproject.toml` (hatchling, `src/` layout, pinned FastAPI/SQLAlchemy/Alembic/pydantic-settings/
`eventkit-core`, `[project.optional-dependencies] dev`) · `Dockerfile` (multi-stage per E.2) ·
`docker-compose.yml` (app + optional postgres profile) · `.dockerignore` · `package.json` +
`vitest.config.js` (jsdom) · `alembic.ini` + `migrations/` · `event-profile.yaml` ·
`webform-schema.yml` · `.env.example` (every var, no values) · `.gitignore` · `.editorconfig` ·
`ruff.toml` · `.pre-commit-config.yaml` · `deploy/app.conf` · `.github/workflows/ci.yml` (matrix
sqlite + postgres; ruff, pytest with coverage gate, vitest, `docker build --target test`,
contract-sync check, `pip-audit`) · `deploy.yml` + `teardown.yml` (OIDC) · `README.md` · `LICENSE` ·
`CONTRIBUTING.md` · `SECURITY.md` · `CODEOWNERS` · `CHANGELOG.md` ·
`.github/{ISSUE_TEMPLATE/,pull_request_template.md,dependabot.yml}` ·
`src/<pkg>/{app.py,settings.py,deps.py}` with `create_app()` and **no import-time side effects** ·
`tests/conftest.py` (one line: `pytest_plugins = ["eventkit.testing"]`).

### I.2 Licensing, ownership, contribution

- **MIT** for the six code repos. `ticketed/LICENSE.md` is already MIT (`Copyright (c) 2026 Princeton
  University`) — carry the copyright line forward, adding "The Trustees of Princeton University".
- **CC-BY-4.0** for `drupal-event-forms` — it's content and configuration, not software, and adopters
  will fork and edit it.
- **CC-BY-4.0 for `event-stack` docs, MIT for its scripts**, with a dual-license note in the README.
- `CODEOWNERS`: `* @pu-sherrerd/event-stack-maintainers`, plus `/migrations/
  @pu-sherrerd/db-reviewers`, `/.eventkit/ @pu-sherrerd/event-stack-maintainers`, and `/contracts/
  @pu-sherrerd/event-stack-maintainers @pu-sherrerd/drupal-admins`.
- One canonical `CONTRIBUTING.md` lives in `event-stack` and is **copied** (not linked) into each
  repo with a header pointing back — GitHub only renders the local file — with a CI diff check
  catching drift. It covers: DCO sign-off, conventional commits, **"no CAARMS-specific values in
  code — profile it or it doesn't ship"**, the sanitization checklist for anything touching
  `drupal-event-forms`, and a hard rule that no PR may add a real attendee record, webhook token,
  speaker prefill token, or discount code to a fixture.

### I.3 Duplication map (the seams that justify the split)

Highest-value first, for tracking that the extraction actually removed them:

1. Drupal payload parser — `posted/schemas.py:16-76` ≈ `:111-193`, plus `ticketed`'s two.
2. Nametag geometry + card content + role mapping — ReportLab vs print CSS/JS.
3. Princeton affiliation normalization — **6 copies** (4 Python, 2 JS).
4. Admin auth — 18 inline `is_admin_authorized` guards instead of one `Depends`.
5. Page chrome — CAARMS/PU header block, font/CSS `<link>` set, `.control-panel`/`.btn-brand-*`,
   `escapeHtml`, sortable-table + search/role-filter logic, across 5 HTML files.
6. Backup/restore — identical panel markup, `downloadDbBackup`/`restoreDbBackup`, and the
   `{{ ENABLE_RESTORE }}` placeholder in both admin pages plus `ticketed`.
7. Grid vs list room renderers — `admin_lodging.html:1288-1420` ≈ `:1421-1532`.
8. Azure app settings — `deploy.sh` vs `deploy.yml`, already drifted.
9. Deploy scripts — ~90% shared between the two repos.
10. Swag size vocabulary — 5+ places across Python, JS, tests, and startup seeding.

### I.4 Orphaned machinery worth knowing about

`ticketed/backend/main.py:251-254` and `:262-265` string-replace `{{ SITE_NAME }}`,
`{{ SITE_SLOGAN }}`, `{{ EVENT_SITE }}`, and `{{ REG_FORM }}` into `index.html`, but **no matching
placeholders remain in the HTML** — the header text was hardcoded to CAARMS instead, so those four
settings are dead as far as the UI is concerned. Only `{{ ENABLE_RESTORE }}` still resolves.
Restoring real templating there is the cheapest possible de-CAARMS-ification win if a stopgap is ever
needed before the full extraction.

Also: `GET /` and `GET /index.html` are byte-for-byte duplicate handlers in both repos;
`ticketed/docker-compose.yml` omits the required `EVENT_SITE`/`REG_FORM`, so `docker-compose up` as
documented in its README fails at import with a pydantic `ValidationError`;
`posted/tests/test_submissions.json` is referenced by no test (it's sample input for
`import_existing.py`); and `posted/requirements.txt` pins `resend` and `PyYAML` while importing
neither — leftovers from the shared lineage.
