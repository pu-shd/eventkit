# eventkit

Reusable core for the Sherrerd event-management stack. Five applications sit on
it: [`ticket-reconciler`], [`lodging-planner`], [`nametag-press`], [`link-forge`]
and [`poster-gallery`].

It exists because those five were originally two repositories that shared roughly
ten thousand lines of near-duplicated logic: three Drupal payload parsers that
disagreed with each other, six copies of one affiliation rule, two hand-written
backup formats, and eighteen imperative authorization checks any one of which
could be forgotten by the next handler someone added.

**Status: v0.3.0.** Every module below is built and tested, and so is the
`eventkit azure` deployment toolkit. Nothing is stubbed: an absent verb is
better than one that half-provisions a subscription and reports success.

---

## What is here

| Module | Replaces | Notes |
|---|---|---|
| `eventkit.identity` | email-as-join-key | `person_key(uuid, email)` preferring the Drupal submission uuid the webform already emits and nobody read. **Frozen and versioned.** |
| `eventkit.drupal` | 3 near-identical parsers | `coerce` primitives + configurable `FieldMap` + one `parse_submission()` used by **both** the webhook and the importer |
| `eventkit.eventprofile` | ~all the event-specific hardcoding | One validated YAML per event, exposed to the browser as public JSON |
| `eventkit.webhook` | `!=` string compares | `compare_digest`, `assert_strong()`, HMAC-over-body, `deferred()` |
| `eventkit.logging` | leaky debug lines | `RedactFilter` installed as a **log record factory**, so it protects handlers eventkit does not own |
| `eventkit.eventbrite` | an 80-line untestable loop | Pure `aggregate_by_email()`, table-tested |
| `eventkit.testing` | 2 hand-rolled conftests | pytest plugin + golden Drupal fixtures, shipped in the wheel |
| `eventkit.db` | 341 lines of hand-rolled `ALTER TABLE` | `Database`, Alembic wiring, Azure Files pragmas (`TRUNCATE`, not WAL) |
| `eventkit.auth` | 18 imperative guards + 90 lines of inline HTML | Easy Auth `Depends`, allow-list that denies when empty, themed denial page, HMAC WS tickets |
| `eventkit.backup` | 2 hand-written dump/restore pairs | Mountable router; columns from `inspect()`, not hand-maintained lists |
| `eventkit.realtime` | a module-global socket list | Polling change feed, correct on N instances; WS opt-in |
| `eventkit.notify` | Resend welded to 4 templates | Pluggable transports, log-only default, nothing blocking the event loop |
| `eventkit.importer` | `import_existing.py` | Any model, any source, with `--dry-run` |
| `eventkit.admin` | an unauthenticated wipe endpoint | HMAC destructive-op task tokens |
| `eventkit.ui` | 5 drifting copies of the same chrome | Paper Tiger tokens + ES modules, no bundler, SheetJS/MathJax vendored |
| `eventkit.mirror` | a startup-time site scraper | Build-time CLI, content-validated, opt-in |
| `eventkit.db` | a 341-line try/except migrator | `Database`, `declarative_base()`, Alembic wiring (`init_migrations`/`upgrade_to_head`/`stamp`/`assert_at_head`/`lifespan_migrations`), `AZURE_FILES_PRAGMAS` for SQLite on an SMB mount |
| `eventkit.auth` | 18 imperative `is_admin_authorized()` call sites | `EasyAuth` as a `Depends`, not a function call — allow-list, dev bypass refused on Azure, themed access-denied page, HMAC WebSocket tickets |
| `eventkit.backup` | 2 hand-written 55-line field lists | `dump()`/`restore()` driven by `sqlalchemy.inspect(model).columns`, `make_backup_router()` (`GET db-backup`, `POST db-restore(/validate)`), whole-payload validation before the first `DELETE`, restore disabled by default |
| `eventkit.realtime` | a module-global socket list | Polling-first: `ChangeLogMixin` + `record_change()`/`poll_changes()`, `make_changes_router()` (`GET /api/changes?since=<cursor>`). WebSocket push (`ChangeBroadcaster`, `make_changes_ws_route()`) is opt-in and instance-local; a full or dead subscriber is dropped without affecting any other connection |
| `eventkit.notify` | a hardcoded Resend `if/elif` chain | `Notifier`/`NotifyPolicy`/`Renderer`, `LogTransport` default (never blocks a deploy), `SmtpTransport` recommended real transport, `ResendTransport`/`AcsTransport` behind extras — every blocking SDK call wrapped in `anyio.to_thread`. Five shipped templates, adopter/profile/default `ChoiceLoader` precedence |
| `eventkit.eventbrite.client` | a per-call `httpx.AsyncClient()` with no injectable transport | `EventbriteClient.fetch_attendees()`/`iter_attendees()`, a `transport=` seam for `respx`, a `max_pages` runaway guard; `EventbriteMock` + the `eventbrite_mock` fixture drive it in tests with zero network |
| `eventkit.eventbrite.sync` | a 190-line function mixing HTTP paging, aggregation, writes and email | `run_sync(client, ports)` against a `SyncPorts` protocol — testable with a fake `ports` and zero database; `SqlAlchemySyncPorts` is the batteries-included impl. Fires `unmatched_payment`/`completed_payment` (unchanged from the predecessor) and, new, `sync_failed` when a sync attempt fails |
| `eventkit.importer` | a script hardcoding one model and one parser, commit-or-nothing | `iter_records()` reads `.tar.gz`/`.tgz`/a directory/`.json`/`.jsonl`/`.csv`; `run_import(parse, upsert, session_factory)` never raises for a bad record (`ImportOutcome.INVALID` + an error, not a crash) and adds the missing `--dry-run`. `add_import_arguments()` gives every app's own importer CLI the same flags |

### The two contracts worth reading before you change anything

**`person_key` is frozen.** Five applications keep their own database. The same
human is a row in up to four of them, and `person_key` is the only thing tying
those rows together. Changing its derivation does not error — it silently orphans
every existing row, and the same person is re-created alongside their own
history. `tests/unit/test_identity.py` pins the algorithm by independent
recomputation and will fail loudly if you touch it.

**Import weight.** `eventkit.identity`, `eventkit.drupal`, `eventkit.eventprofile`,
`eventkit.logging`, `eventkit.webhook` and `eventkit.eventbrite.aggregate` import
with **only** pydantic, PyYAML, Jinja2 and the standard library. FastAPI,
SQLAlchemy and httpx are imported lazily inside the functions that need them.
That is what keeps `link-forge` (no database) and `nametag-press` (no outbound
HTTP) light, and `tests/unit/test_import_weight.py` enforces it in a subprocess
where those packages are made unimportable.

---

## Quickstart

```bash
docker-compose run --rm test          # everything, same command as CI
```

Or locally:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[test]"
.venv/bin/python -m pytest -q
```

### Using it

```python
from eventkit.drupal import parse_submission, resolve_field_map
from eventkit.eventprofile import get_profile
from eventkit.identity import person_key

profile = get_profile()                       # ./event-profile.yaml, or $EVENT_PROFILE
field_map = resolve_field_map(profile, want=["email", "name", "attendee_status"])

submission = parse_submission(request_body, field_map)
if submission.person_key is None:
    raise HTTPException(400, "submission carries no usable identity")
```

`resolve_field_map` takes the logical fields *your* app needs, so
`nametag-press` boots without lodging keys configured. It raises
`FieldMapError` at startup — with a copy-pasteable YAML stub in the message — if
the profile cannot supply them. There is deliberately no built-in default field
map: the predecessor fell back to a hardcoded CAARMS mapping, which meant any
other event silently parsed every registration into empty columns with no error
anywhere.

### The event profile

One validated YAML per event replaces the hardcoded dates, discount codes,
t-shirt vocabularies, role labels, Avery geometry and affiliation rules that were
previously spread across Python, HTML, JavaScript, shell and CI — often in five
places at once.

`examples/caarms-2026/` is the worked reference: the most complicated event this
stack runs, sanitized. Two rules it demonstrates:

- **Secrets are never in the profile.** Ticket tiers carry `discount_code_env` —
  the *name* of an environment variable. Validation rejects a value that looks
  like a code rather than a variable name.
- **Check-in day keys are ISO dates.** The front end hardcoded `"6/28"`,
  `"banquet"`, `"7/1"`: year-less keys that collide across events and are
  ambiguous to parse, since both `"7/1"` and `"07/01"` appear in the live data.
  `eventkit.eventprofile.checkin` migrates them **by position against the
  schedule**, and refuses to drop a key it does not recognise.

### Testing

Each app's `conftest.py` can be **empty**. The `pytest11` entry point registers
the plugin automatically for anything that has eventkit installed.

Do *not* add `pytest_plugins = ["eventkit.testing.plugin"]` — naming it again
registers the same module under a second name and pytest aborts collection with
`ValueError: Plugin already registered under a different name`. This repo's own
`tests/conftest.py` documents it, because it is the obvious thing to reach for.

The highest-value fixture is autouse `_no_network`, which makes remote
connections raise. `posted`'s suite currently makes real outbound HTTPS requests
to a production Drupal site on every run — carrying a Cloudflare bypass header
from a developer laptop — because asset mirroring is invoked from the app's
`lifespan`. An autouse socket guard would have caught that on day one.

---

## Documentation

**[Roadmap](docs/roadmap/)** — the remaining work as a stack of pull requests, plus
the committed plan and current status. Start at
[`docs/roadmap/STATUS.md`](docs/roadmap/STATUS.md) if you are picking this up on a
new machine.

**[Drupal forms → eventkit apps](docs/drupal/)** — how to design the registration
webform for a new event, build it from the YAML templates, and wire it to a
deployed set of applications. Covers the element vocabulary and composites,
`#states` conditional logic, the two real Drupal import paths, Remote Post
handler setup, the field-map contract, conditional ticketing, an end-to-end
runbook, and troubleshooting.

The templates in [`docs/drupal/templates/`](docs/drupal/templates/) are verified
against this library's own parser by `tests/unit/drupal/test_doc_templates.py`,
so they cannot drift from the code that reads them.

## The rest of the stack

The library and the Azure toolkit are complete. The rest lives in its own
repositories:

| | |
|---|---|
| The five applications | Their own repositories, listed above |
| Webform exports, Remote Post recipes, field-map contracts | [`drupal-event-forms`](https://github.com/pu-shd/drupal-event-forms) |
| Architecture, runbook, security and privacy for the whole stack | [`event-stack`](https://github.com/pu-shd/event-stack) |

Start at [`event-stack`](https://github.com/pu-shd/event-stack) — architecture,
runbook, the security and privacy inventory, and eight decision records.

The CLI:

```sh
eventkit profile validate event-profile.yaml   # OK  CAARMS 2026  slug=caarms-2026  …
eventkit profile public event-profile.yaml     # the browser-safe JSON projection
eventkit profile checkin-keys event-profile.yaml
eventkit fieldmap check event-profile.yaml     # resolve and print the field map
eventkit ui vendor --dest ./vendor --theme neutral
eventkit mirror --spec mirror.yaml --dest ./vendor/site
```

## Deploying to Azure

`eventkit azure` provisions and maintains one event's applications: an
interactive, colourful, **resumable** bootstrap with `deploy`, `resume`,
`update` and `teardown`, plus `status`, `doctor`, `adopt`, `drift`, `gate ack`,
`logs`, `open` and `eject`.

```zsh
eventkit azure deploy --event caarms-2026 --dry-run   # print every az command, run none
eventkit azure deploy --event caarms-2026
```

Steps that cannot be scripted — the Entra ID identity provider, a DNS CNAME, an
Eventbrite token, the Drupal Remote Post handler — become **gates**: a numbered
checklist and a portal deep link, then a poll of a read-only predicate that
succeeds the moment you are done. It never asks you to confirm; it checks. Under
`--yes` a gate fails fast with the checklist instead of blocking a CI job.

State lives in a committed `.eventkit/state.json` step ledger, so `resume` picks
up exactly where an interruption left it — on a different machine if need be. No
secret is ever written to it.

There are no passwords anywhere: the web app pulls from the registry with a
system-assigned managed identity, and GitHub Actions authenticates with a
user-assigned identity and federated credentials.

Six CI/CD workflow templates ship as package data — test, deploy, backup, drift,
admin-task and teardown.

- [`docs/azure/`](docs/azure/README.md) — the toolkit
- [`docs/azure/ci-cd.md`](docs/azure/ci-cd.md) — the workflows
- [`docs/azure/gates.md`](docs/azure/gates.md) — every gate and its predicate
- [`docs/azure/adding-an-application.md`](docs/azure/adding-an-application.md)
- [`docs/azure/troubleshooting.md`](docs/azure/troubleshooting.md)

36 bats tests exercise the whole flow against a mock `az` — no subscription, no
network, no credentials:

```zsh
docker-compose run --rm test bats tests/azure/toolkit.bats
```

## Licence

MIT. Copyright (c) 2026 The Trustees of Princeton University.

[`ticket-reconciler`]: https://github.com/pu-shd/ticket-reconciler
[`lodging-planner`]: https://github.com/pu-shd/lodging-planner
[`nametag-press`]: https://github.com/pu-shd/nametag-press
[`link-forge`]: https://github.com/pu-shd/link-forge
[`poster-gallery`]: https://github.com/pu-shd/poster-gallery
