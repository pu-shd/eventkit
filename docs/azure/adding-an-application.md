# Adding an application to the stack

A sixth application is a TOML file, not a new deploy script.

The predecessors had roughly 1,475 lines of shell across two repositories that
were 70–90% the same boilerplate — identical colour blocks, identical logging
helpers, the same prompt-with-default pattern, the same rewrite-the-env-file
dance. The differences that actually mattered came to about a dozen values.
Those dozen values are `deploy/app.conf`.

## Minimum

```toml
name        = "my-app"
image       = "my-app"
health_path = "/healthz"
easy_auth   = true
needs_db    = true
db_default  = "sqlite"
gates       = ["easy-auth"]

[[setting]]
name = "DATABASE_URL"
type = "computed"
required = true

[[setting]]
name = "AUTHORIZED_PRINCIPALS"
type = "list"
required = true
prompt = "Comma-separated admin emails, or @domain.edu. Empty means DENY ALL."
```

The annotated version, with every option, is
`src/eventkit/azure/templates/app.conf.example`.

## It is genuinely TOML

`name = "X"; type = "computed"` on one line is a syntax error — TOML has no
statement separator, so everything after the semicolon is garbage and the
toolkit reads no settings at all. This shipped once, to five repositories, and
nothing caught it because nothing outside the toolkit parsed the file.

Every application repository now carries `tests/test_deploy_conf.py`, which
parses `deploy/app.conf`, checks each setting has a known type, and asserts no
secret carries a committed value. Copy it.

## Setting types

| `type` | Behaviour |
|---|---|
| `computed` | Derived by the toolkit. `DATABASE_URL` follows the datastore choice. |
| `secret` | Read from `.env.deploy`, else from the live app, else generated (`generate = "hex32"`), else prompted. Written to App Service and to the gitignored `.env.deploy`; never to the ledger. |
| `fixed` | Always `value`. Not asked about. |
| `list` | Comma-separated. Validated as email addresses when the name contains `PRINCIPALS`. |
| `bool`, `int`, `string`, `choice` | Prompted with `default`, validated at entry. |

`gate = "<id>"` defers a prompt until that gate is satisfied — the Eventbrite
token is not worth asking for before the token has been proven to work.

Re-running never rotates a secret behind your back: an existing value in the
live application wins over generating a new one.

## What the application must provide

**`GET <health_path>` answering 200 anonymously.** Liveness only — no counts, no
version, no configuration. It is reachable from the internet.

**`GET /api/webhook/status`** if the app declares the `webhook` gate. Counters
and timestamps only:

```json
{"received_total": 12, "authenticated_total": 11, "rejected_total": 1,
 "last_received_at": "2026-06-01T12:00:00Z", "unmapped_keys": []}
```

No attendee data, so it is safe to reach with the webhook token.

**`GET /api/admin/db-backup`** if you want the nightly backup workflow. Mount
`eventkit.backup.make_backup_router`; the column list comes from
`sqlalchemy.inspect`, not from a hand-maintained list that goes stale the first
time someone adds a column.

**A `runtime` and a `test` stage in the Dockerfile.** The deploy workflow builds
`--target runtime`; the test workflow builds `--target test`.

**A `create_app()` factory with no import-time side effects.** Migrations and
`create_all` belong in `lifespan`.

## Then

```zsh
eventkit azure deploy --event caarms-2026 --dry-run    # read what it will do
eventkit azure deploy --event caarms-2026
```

It joins the event's existing resource group, plan and registry, or creates them
if it is the first application for that event. Nothing about a second
application differs from the first.

## Deploying to an existing, hand-built setup

```zsh
eventkit azure adopt --app my-app
```

Reads the live resource group, registry, plan, web app, database and settings
and writes a ledger without touching anything. From then on `deploy` is
idempotent against it: present and matching is recorded and skipped, absent is
created, and present-but-different is **refused** with a warning rather than
silently adopted — quietly adopting a mismatched resource is how you end up
deploying into somebody else's application.
