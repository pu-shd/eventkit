# eventkit

Reusable core for the Sherrerd event-management stack. Five applications sit on
it: [`ticket-reconciler`], [`lodging-planner`], [`nametag-press`], [`link-forge`]
and [`poster-gallery`].

It exists because those five were originally two repositories that shared roughly
ten thousand lines of near-duplicated logic: three Drupal payload parsers that
disagreed with each other, six copies of one affiliation rule, two hand-written
backup formats, and eighteen imperative authorization checks any one of which
could be forgotten by the next handler someone added.

**Status: v0.1.0, in extraction.** The modules below are built and tested. The
rest are listed under [Not yet built](#not-yet-built) with nothing stubbed — an
absent module is better than one that imports and misbehaves.

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
| `eventkit.db` | a 341-line try/except migrator | `Database`, `declarative_base()`, Alembic wiring (`init_migrations`/`upgrade_to_head`/`stamp`/`assert_at_head`/`lifespan_migrations`), `AZURE_FILES_PRAGMAS` for SQLite on an SMB mount |
| `eventkit.auth` | 18 imperative `is_admin_authorized()` call sites | `EasyAuth` as a `Depends`, not a function call — allow-list, dev bypass refused on Azure, themed access-denied page, HMAC WebSocket tickets |
| `eventkit.backup` | 2 hand-written 55-line field lists | `dump()`/`restore()` driven by `sqlalchemy.inspect(model).columns`, `make_backup_router()` (`GET db-backup`, `POST db-restore(/validate)`), whole-payload validation before the first `DELETE`, restore disabled by default |
| `eventkit.realtime` | a module-global socket list | Polling-first: `ChangeLogMixin` + `record_change()`/`poll_changes()`, `make_changes_router()` (`GET /api/changes?since=<cursor>`). WebSocket push (`ChangeBroadcaster`, `make_changes_ws_route()`) is opt-in and instance-local; a full or dead subscriber is dropped without affecting any other connection |
| `eventkit.notify` | a hardcoded Resend `if/elif` chain | `Notifier`/`NotifyPolicy`/`Renderer`, `LogTransport` default (never blocks a deploy), `SmtpTransport` recommended real transport, `ResendTransport`/`AcsTransport` behind extras — every blocking SDK call wrapped in `anyio.to_thread`. Five shipped templates, adopter/profile/default `ChoiceLoader` precedence |
| `eventkit.eventbrite.client` | a per-call `httpx.AsyncClient()` with no injectable transport | `EventbriteClient.fetch_attendees()`/`iter_attendees()`, a `transport=` seam for `respx`, a `max_pages` runaway guard; `EventbriteMock` + the `eventbrite_mock` fixture drive it in tests with zero network |
| `eventkit.eventbrite.sync` | a 190-line function mixing HTTP paging, aggregation, writes and email | `run_sync(client, ports)` against a `SyncPorts` protocol — testable with a fake `ports` and zero database; `SqlAlchemySyncPorts` is the batteries-included impl. Fires `unmatched_payment`/`completed_payment` (unchanged from the predecessor) and, new, `sync_failed` when a sync attempt fails |

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

## Not yet built

Listed so nobody looks for them: `importer`, `mirror`,
`admin`, `ui`, and the `azure` zsh toolkit.

The CLI **is** built, for the parts that exist:

```sh
eventkit profile validate event-profile.yaml   # OK  CAARMS 2026  slug=caarms-2026  …
eventkit profile public event-profile.yaml     # the browser-safe JSON projection
eventkit profile checkin-keys event-profile.yaml
eventkit fieldmap check event-profile.yaml     # resolve and print the field map
eventkit db init --package myapp               # scaffold migrations/ + alembic.ini
eventkit db upgrade --url sqlite:///./app.db --migrations-dir migrations
eventkit db current --url sqlite:///./app.db
```

`eventkit azure`, `ui`, `mirror` and `import` are declared but report that they
are not built in v0.1.

## Licence

MIT. Copyright (c) 2026 The Trustees of Princeton University.

[`ticket-reconciler`]: https://github.com/pu-shd/ticket-reconciler
[`lodging-planner`]: https://github.com/pu-shd/lodging-planner
[`nametag-press`]: https://github.com/pu-shd/nametag-press
[`link-forge`]: https://github.com/pu-shd/link-forge
[`poster-gallery`]: https://github.com/pu-shd/poster-gallery
