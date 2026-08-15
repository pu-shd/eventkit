# eventkit

Shared library and deployment toolkit for the
[event-management stack](https://github.com/pu-shd/event-stack). Five
applications sit on it: [`ticket-reconciler`](https://github.com/pu-shd/ticket-reconciler),
[`lodging-planner`](https://github.com/pu-shd/lodging-planner),
[`nametag-press`](https://github.com/pu-shd/nametag-press),
[`link-forge`](https://github.com/pu-shd/link-forge) and
[`poster-gallery`](https://github.com/pu-shd/poster-gallery).

It is a **library, not a service** — nothing to deploy centrally. It carries the
pieces that must agree across applications: one Drupal parser, one identity
function, one auth dependency, one backup format.

## Install

```
eventkit-core[app] @ https://github.com/pu-shd/eventkit/archive/refs/tags/v0.3.1.tar.gz
```

A codeload tarball, not `git+https`, so `python:3.11-slim` needs no git.
Extras: `web`, `db`, `http`, `resend`, `acs`, `postgres`, `app`, `test`.

## Deploy an application

```zsh
cd poster-gallery
eventkit azure deploy --event my-event-2027 --dry-run
eventkit azure deploy --event my-event-2027
```

Interactive, colourful and resumable. It pauses at steps it cannot do for you —
the Entra identity provider, a DNS record, an Eventbrite token — polls until you
have done them, and picks up where it left off if you quit.

→ **[docs/azure/](docs/azure/README.md)** · [gates](docs/azure/gates.md) ·
[CI/CD](docs/azure/ci-cd.md) · [troubleshooting](docs/azure/troubleshooting.md)

## Modules

| | |
|---|---|
| `identity` | `person_key(uuid, email)` — frozen and versioned; `IdentityMixin` |
| `drupal` | One parser for webhook and importer: coercion primitives, `FieldMap`, `parse_submission()` |
| `eventprofile` | The validated per-event YAML, its public JSON projection, check-in key migration |
| `auth` | Easy Auth as a `Depends`, allow-list, themed denial page, HMAC WebSocket tickets |
| `webhook` | `compare_digest` verification, `assert_strong()`, HMAC-over-body, `deferred()` |
| `db` | `Database`, Alembic wiring, Azure Files pragmas (`TRUNCATE`, not WAL) |
| `backup` | One mountable router; column list read from the schema, not hand-maintained |
| `eventbrite` | Pure `aggregate_by_email()`, client with injectable transport, `SyncPorts` |
| `notify` | Transport protocol; log, SMTP, Resend, ACS |
| `realtime` | Polling change feed over a monotonic cursor; WebSockets opt-in |
| `importer` | Bulk import through the same parser the webhook uses |
| `ui` | Paper Tiger tokens, themes and ES modules — no bundler |
| `testing` | A pytest plugin, so each application's `conftest.py` is one line |
| `azure` | The zsh deployment toolkit, shipped as package data |

`eventprofile` and `ui` import with no FastAPI or SQLAlchemy, so light
applications stay light.

## Build an application on it

```python
from fastapi import Depends, FastAPI
from eventkit.auth import AllowList, EasyAuth
from eventkit.db import Database, declarative_base
from eventkit.db.migrate import lifespan_migrations
from eventkit.eventprofile import load_profile
from eventkit.webhook import WebhookTokens

Base = declarative_base()

def create_app(*, database=None, profile=None) -> FastAPI:
    profile = profile or load_profile()
    db = database or Database(settings.database_url)
    auth = EasyAuth(AllowList.parse(settings.authorized_principals))
    tokens = WebhookTokens.from_settings(registration=settings.drupal_webhook_token)

    app = FastAPI(lifespan=lifespan_migrations(db, migrations_dir=MIGRATIONS))
    app.include_router(admin_router, dependencies=[Depends(auth.require)])
    app.include_router(webhook_router,
                       dependencies=[Depends(tokens.dependency("registration"))])
    return app
```

A factory with **no import-time side effects** — migrations and `create_all`
belong in `lifespan`. That is what makes the test fixtures one line:

```python
pytest_plugins = ["eventkit.testing"]
```

## CLI

```sh
eventkit profile validate event-profile.yaml    # exit 0/1, readable error report
eventkit profile public   event-profile.yaml    # the JSON served at /api/event-profile
eventkit fieldmap check   event-profile.yaml    # resolve and print the field map
eventkit db upgrade                             # migrations
eventkit ui vendor --dest ./vendor --theme neutral
eventkit azure deploy --event my-event-2027
```

## Tests

```zsh
docker-compose run --rm test        # pytest + vitest + shellcheck + bats
```

## Elsewhere

| | |
|---|---|
| [`event-stack`](https://github.com/pu-shd/event-stack) | Architecture, runbook, security and privacy. **Start there** |
| [`drupal-event-forms`](https://github.com/pu-shd/drupal-event-forms) | Webform exports, handler recipes, field-map contracts |
| [`docs/drupal/`](docs/drupal/README.md) | How a submission is parsed on this side |

## Licence

MIT. Copyright (c) 2026 The Trustees of Princeton University.
