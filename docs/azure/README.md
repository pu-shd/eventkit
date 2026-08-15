# Deploying to Azure

```zsh
cd poster-gallery
eventkit azure deploy --event my-event-2027 --dry-run   # prints every az command, runs none
eventkit azure deploy --event my-event-2027
```

That is the whole thing. It is idempotent and resumable — re-run it any time, on
any machine with the repository.

- [Verbs](#verbs) · [What it creates](#what-it-creates) · [Manual steps](#manual-steps)
- [Gate reference](gates.md) · [CI/CD workflows](ci-cd.md) · [Troubleshooting](troubleshooting.md)

## Verbs

| | |
|---|---|
| `deploy` | Provision and configure. Safe to re-run |
| `resume` | The same, without re-asking answered questions |
| `update` | Rebuild the image and restart. No provisioning |
| `teardown` | Delete the event's resources. Requires typing the group name |
| `status` | The ledger beside what Azure reports |
| `doctor` | Check tooling, sign-in and gates. Changes nothing |
| `adopt` | Record an existing hand-built deployment |
| `drift` | Where live config has moved away from the ledger |
| `gate ack` | Acknowledge a skipped manual step until a date |
| `logs`, `open`, `eject` | Tail logs; open the app; copy the toolkit locally |

Flags: `--dry-run`, `--yes` (non-interactive; gates fail fast), `--no-reprompt`,
`--app`, `--event`, `--postgres`, `--verbose`.

## What it creates

One resource group per **event**, holding every application for it, so the whole
conference tears down in one operation.

```
ek-<event>-rg
├── ek-<event>-plan              B1 Linux, one instance, shared by all apps
├── ek<event><rand>              container registry, admin account disabled
├── ek-<event>-<app>-<rand>      web app + system-assigned identity
├── ek-<event>-<app>-ci          user-assigned identity for GitHub Actions
└── ek-<event>-<app>-db-<rand>   Postgres, only with --postgres
```

Names are derived once, clamped to Azure's length limits, and read back from the
ledger thereafter, so they stay stable. `--prefix` changes `ek-`.

## Deploying several applications

Each application is a separate `deploy` from its own repository. The first
creates the shared resource group, plan and registry; the rest join them.

```zsh
for app in ticket-reconciler nametag-press poster-gallery; do
  (cd ../$app && eventkit azure deploy --event my-event-2027 --yes)
done
```

`--yes` is right for the second and subsequent runs, once the gates are met.

## Manual steps

Some steps cannot be scripted: an Entra ID identity provider needs tenant
permissions a deployment identity should not hold; a DNS record belongs to
whoever runs DNS; an Eventbrite token is fetched by a human.

Each is a **gate**. It prints a numbered checklist and a portal deep link, then
polls a read-only predicate:

```
[12/15] Easy Auth (manual — needs your tenant)

    1. Open the portal link below (or press [o]).
    2. Add identity provider → Microsoft → Workforce configuration.
    3. Restrict access: Require authentication.
    4. Unauthenticated requests: HTTP 302 redirect.

    https://portal.azure.com/#@<tenant>/resource/<id>/authentication

  ⠹ waiting for the identity provider — 40s   [s]kip [r]etry [o]pen [q]uit
```

It succeeds the moment the predicate passes — it never asks you to confirm. `[q]`
saves your place; `resume` returns to it. Under `--yes` it exits non-zero with the
checklist instead of blocking CI.

Can't do one now?

```zsh
eventkit azure gate ack easy-auth --until 2027-09-01 --reason "OIT ticket 12345"
```

Both flags required; it resurfaces in nightly drift afterwards. Full list:
[gates.md](gates.md).

## Resume

`.eventkit/state.json` records what was created and what is outstanding. Commit
it — it is how the next person continues on another machine.

```json
{
  "event": "my-event-2027",
  "app": "poster-gallery",
  "names": { "resourceGroup": "ek-my-event-2027-rg", "acr": "ekmyevent2027de8d9a" },
  "steps": [
    { "id": "registry",  "status": "done", "resourceId": "/subscriptions/…" },
    { "id": "easy-auth", "status": "pending" }
  ]
}
```

`resume` replays only `pending` and `failed` steps. **No secret is written to the
ledger** — App Service settings hold those, and local working values live in a
gitignored `.env.deploy`.

## No passwords

The web app pulls images with a **system-assigned managed identity** granted
`AcrPull`; the registry's admin account is disabled.

GitHub Actions uses a **user-assigned identity** with federated credentials for
`ref:refs/heads/main` and `environment:production`, holding `AcrPush` and
`Website Contributor` on the one web app. No client secret, nothing to rotate.

Identifiers land as repository **variables**: `AZURE_CLIENT_ID`,
`AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`,
`AZURE_WEBAPP_NAME`, `ACR_NAME`.

## Application settings

Declared in `deploy/app.conf` and written **only** by the toolkit — never also by
a workflow.

```toml
name        = "my-app"
image       = "my-app"
health_path = "/healthz"
easy_auth   = true            # or "admin-routes-only", or false
needs_db    = true
db_default  = "sqlite"        # or "postgres"
gates       = ["easy-auth", "webhook"]

[[setting]]
name = "DRUPAL_WEBHOOK_TOKEN"
type = "secret"
required = true
generate = "hex32"

[[setting]]
name = "AUTHORIZED_PRINCIPALS"
type = "list"
required = true
prompt = "Comma-separated admin emails. Empty means DENY ALL."
```

One key per line — `name = "X"; type = "secret"` is not valid TOML.

| `type` | Behaviour |
|---|---|
| `computed` | Derived (e.g. `DATABASE_URL` from the datastore) |
| `secret` | From `.env.deploy`, else the live app, else generated, else prompted |
| `fixed` | Always `value` |
| `list`, `bool`, `int`, `string`, `choice` | Prompted with `default`, validated |

`generate = "hex32"` mints one instead of asking. `gate = "<id>"` defers the
prompt until that gate passes. Re-running never rotates an existing secret.

Always applied: `WEBSITES_PORT=8000` and
`WEBSITES_CONTAINER_START_TIME_LIMIT=600` (the 230 s default kills a first boot
that runs migrations). With SQLite, also
`WEBSITES_ENABLE_APP_SERVICE_STORAGE=true` and the plan pinned to one instance.
With `--postgres`, the server is created `--public-access None` and then the App
Service outbound addresses are added.

Full annotated example: `src/eventkit/azure/templates/app.conf.example`.

## Testing the toolkit

```zsh
docker-compose run --rm test bats tests/azure/toolkit.bats
```

36 tests against a mock `az` — no subscription, no network, no credentials.
`./run_tests.sh` runs pytest, shellcheck and bats together.
