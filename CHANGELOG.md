# Changelog

## 0.1.0 — unreleased

First extraction from `ticketed` and `posted`. Fresh history, no import.

### Added

- **`eventkit.identity`** — `person_key(uuid, email)` preferring the Drupal
  submission uuid. **Frozen and versioned contract**; `PERSON_KEY_VERSION = 1`.
  Also `normalize_email`, `diff_populations` (powering `identity-drift`), and a
  lazily-resolved `IdentityMixin` so the module imports without SQLAlchemy.
- **`eventkit.drupal`** — one parser replacing three. Total coercion primitives
  (`coerce.py`), configurable `FieldMap` / `WebformSchema` with best-effort
  inference that warns on every heuristic, and `parse_submission()` used by both
  the webhook and the importer (asserted by `test_parity.py`).
- **`eventkit.eventprofile`** — validated per-event YAML, public JSON projection
  with a trip-wire test, and the legacy check-in key migration.
- **`eventkit.webhook`** — `compare_digest` verification, `assert_strong()`,
  HMAC-over-body with timestamp, `deferred()`.
- **`eventkit.logging`** — `RedactFilter` installed as a log **record factory**.
- **`eventkit.eventbrite`** — typed `Attendee`/`AggregatedPayment` and the pure
  `aggregate_by_email()`.
- **`eventkit.testing`** — pytest plugin (`pytest11` entry point) with autouse
  `_no_network`, and 15 sanitized golden Drupal payloads shipped in the wheel.
- **`eventkit.db`** — `Database` (engine, session factory, SQLite pragma
  wiring), `declarative_base()` with the naming convention Alembic's
  `render_as_batch` needs to name-and-drop SQLite constraints, `sqlite_url_for()`,
  and `AZURE_FILES_PRAGMAS` (`TRUNCATE` journal mode, not WAL — SMB has no
  shared-memory mmap). `eventkit.db.migrate` adopts Alembic: `init_migrations`,
  `upgrade_to_head` (filelock-serialised, snapshots the SQLite file first,
  raises rather than logs-and-continues), `current_revision`, `assert_at_head`,
  `stamp` (for adopting a database the predecessor's hand-rolled migrator
  already created), `lifespan_migrations` (a drop-in FastAPI `lifespan`), and
  `ensure_columns` (a documented hotfix-only escape hatch). Replaces the
  341-line try/except migrator in `ticketed`/`posted`'s `database.py`, which
  left an app serving against a schema it believed it had after a failed
  `ALTER TABLE`. `eventkit db init/upgrade/stamp/current` on the CLI.
- **`eventkit.cli`** — `profile validate` / `profile public` /
  `profile checkin-keys` / `fieldmap check` / `db init` / `db upgrade` /
  `db stamp` / `db current`. Unbuilt verbs report that honestly.
- `examples/caarms-2026/` — the sanitized reference event.
- CI: leak greps for institutional addresses, placeholder tokens, discount codes,
  bearer tokens in URLs, and the WAF bypass header; plus gitleaks, a 3.11–3.13
  matrix, a Docker `test` target, a wheel package-data assertion, and a
  minimal-install job proving the import-weight contract.

### Behaviour deliberately preserved from the predecessors

Extraction is meant to be behaviour-preserving. Where the original was
surprising, the original wins and the surprise is documented:

- `split_full_name` uses `split(None, 1)`, so `"Ada B Lovelace"` becomes
  `("Ada", "B Lovelace")`. Wrong for multi-word given names, but the live
  databases were populated with it.
- An **unrecognised** Eventbrite status maps to `refunded`, not `unknown`,
  because the original status chain ends in a bare `else: status = "refunded"`.
  Changing it would silently reclassify existing rows on the next sync.
- In aggregation, a paid record superseding a non-paid one **replaces** amounts
  rather than summing them.
- Check-in state integers `0..3` are frozen; renumbering them would reclassify
  every recorded check-in.

### Deliberate behaviour changes

- **No embedded default field map.** The predecessor's `DEFAULT_SCHEMA_YAML`
  always won, because no `webform-schema.yml` shipped in the image. Absent
  configuration is now a startup failure with a copy-pasteable stub.
- **Unresolved Drupal tokens are rejected as identities.** A literal
  `"[webform_submission:uuid]"` would otherwise give every registrant the same
  `person_key` and collapse the roster onto one row.
- **Explicit `first_name`/`last_name` rules beat a `name` composite.** `posted`
  had the opposite precedence, but no shipped field map declares both.
- **Empty string and absent normalise identically** to `None`.
- Booleans are coerced at the ingest boundary, rather than stored as
  `"Yes"`/`"yes"`/`None` strings queried with `(col == "Yes") | (col == "yes")`.

### Known gaps

- The test suite has **not been executed** — the extraction environment had no
  approval to run Python. Run `docker-compose run --rm test` before relying on
  any of this. See the handover notes.
- `eventkit-core`'s availability on PyPI is unverified (no network access). The
  bare `eventkit` name is taken; v0.1 installs from a GitHub tarball, so the
  distribution name is not yet load-bearing.
- Not built: `auth`, `backup`, `notify`, `realtime`, `importer`, `mirror`,
  `admin`, `eventbrite.client`, `eventbrite.sync`, `ui`, and the `azure` toolkit.
