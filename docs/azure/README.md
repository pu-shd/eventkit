# `eventkit azure` — deploying an event to Azure

A colourful, interactive, resumable bootstrap for one event's applications. Run
it from an application repository:

```zsh
eventkit azure deploy --event caarms-2026
```

That is the whole quickstart. Everything below is what happens, why, and what to
do when it stops.

- [The verbs](#the-verbs)
- [What gets created](#what-gets-created)
- [Manual steps](#manual-steps-the-gate) — the part that waits for you
- [Resuming](#resuming)
- [Managed identity](#managed-identity-there-are-no-passwords)
- [Application settings](#application-settings)
- [CI/CD](ci-cd.md)
- [Adding an application](adding-an-application.md)
- [Gate reference](gates.md)
- [Troubleshooting](troubleshooting.md)

---

## The verbs

| | |
|---|---|
| `deploy` | Provision and configure. Idempotent and resumable — safe to re-run at any point. |
| `resume` | The same, without re-asking questions already answered. |
| `update` | Rebuild the image and restart. No provisioning. |
| `teardown` | Delete what this ledger created. Requires typing the resource group name. |
| `status` | The ledger beside what Azure actually reports. |
| `doctor` | Check tooling, sign-in and gates. Changes nothing. |
| `adopt` | Record an existing, hand-built deployment in a ledger. |
| `drift` | Where live configuration has moved away from the ledger. |
| `gate ack` | Acknowledge a skipped manual step until a date, with a reason. |
| `logs`, `open` | Tail the log; open the app. |
| `eject` | Copy the toolkit into the repository so you can modify it. |

Global flags: `--dry-run` (prints every `az` command and runs none), `--yes`
(non-interactive; manual gates fail fast with instructions rather than blocking),
`--no-reprompt`, `--app`, `--event`, `--postgres`, `--verbose`.

Start with `--dry-run`. It is honest: it prints the exact commands, including
through the manual gates, without creating anything or waiting for anybody.

## What gets created

One resource group per **event**, holding every application for it, so a
conference tears down in one operation. One App Service plan per event, shared —
B1 hosts several small applications comfortably, and five plans for a one-week
event is waste. One container registry.

```
ek-<event>-rg
├── ek-<event>-plan                 B1, Linux, one instance
├── ek<event><rand>                 registry, admin account disabled
├── ek-<event>-<app>-<rand>         web app, system-assigned identity
├── ek-<event>-<app>-ci             user-assigned identity for GitHub Actions
└── ek-<event>-<app>-db-<rand>      Postgres, only with --postgres
```

Names are derived once, clamped to Azure's per-type length limits (24 characters
for storage, 50 for a registry, 60 for a web app) and then read back from the
ledger, so a clamped name is the same clamped name on every subsequent run.
Clamping removes characters from the *middle*, keeping the random suffix that
makes the name unique.

Nothing is prefixed with a department name. `--prefix` changes `ek-`.

## Manual steps: the gate

Some steps cannot be scripted. Creating an Entra ID identity provider needs
permissions in your tenant that a deployment identity should not hold; a DNS
CNAME belongs to whoever runs your DNS; an Eventbrite private token is fetched
by a human from a web page.

The predecessor's answer was to not mention them. The Easy Auth configuration
that its entire admin authorization model depended on was done by hand in the
portal, undocumented, and unreproducible.

Here, each is a **gate**:

```
[12/15] Easy Auth (manual — needs your tenant) (easy-auth)

  This step needs you. Entra ID authentication for ek-caarms-2026-poster-gallery

    1. Open the portal link below (or press [o]).
    2. Choose 'Add identity provider' → Microsoft.
    3. Pick 'Workforce configuration' and let it create a new app registration.
    4. Set 'Restrict access' to 'Require authentication'.
    5. Set 'Unauthenticated requests' to 'HTTP 302 Found redirect'.
    6. Save. This page will notice within 10s.

    https://portal.azure.com/#@<tenant>/resource/<id>/authentication

  ⠹ waiting for the identity provider to appear — 40s   [s]kip [r]etry [o]pen [q]uit
```

It polls a read-only predicate and succeeds the instant that predicate passes.
It never asks you to press a key to say you are done — it checks. `[q]uit`
records the position so `resume` re-enters exactly there. Under `--yes` it
prints the checklist and exits non-zero instead of blocking a CI job forever.

A gate that was skipped in a rush is not forgotten: skipped gates marked
critical surface in the nightly [drift](ci-cd.md#drift) run until they are met
or explicitly acknowledged:

```zsh
eventkit azure gate ack easy-auth --until 2026-09-01 --reason "OIT ticket 12345"
```

Both `--until` and `--reason` are required. A permanent silence is not on offer,
because an alert that is always red is one nobody reads.

See [gates.md](gates.md) for each gate and the predicate behind it.

## Resuming

The ledger at `.eventkit/state.json` records what was created, in what order,
and what is still outstanding:

```json
{
  "schemaVersion": 1,
  "event": "caarms-2026",
  "app": "poster-gallery",
  "names": { "resourceGroup": "ek-caarms-2026-rg", "acr": "ekcaarms2026de8d9a" },
  "steps": [
    { "id": "registry", "status": "done", "at": "…", "resourceId": "/subscriptions/…" },
    { "id": "easy-auth", "status": "pending" }
  ],
  "history": [ { "at": "…", "verb": "deploy", "azVersion": "2.70.0", "eventkit": "0.3.0" } ]
}
```

`resume` replays only what is `pending` or `failed`. It is not a separate code
path — `resume` is `deploy` with re-prompting turned off — so the resume logic is
exercised on every single run and cannot rot.

**No secret is ever written to the ledger.** App Service settings are the source
of truth for those and are read back when needed; local working values live in a
gitignored `.env.deploy`. There is a test that greps the ledger for anything
resembling a credential after a full deploy.

Commit the ledger. It is how the next person, on a different laptop, picks this
up. Because committing it makes it a supply-chain surface — anyone who can merge
could repoint `names.resourceGroup` at a different resource group, including for
a teardown — put `CODEOWNERS` on `.eventkit/**`.

## Managed identity: there are no passwords

The web app gets a **system-assigned** managed identity, granted `AcrPull` on the
registry, and `acrUseManagedIdentityCreds` is turned on. The registry's admin
account is disabled. Any `DOCKER_REGISTRY_SERVER_USERNAME` and
`DOCKER_REGISTRY_SERVER_PASSWORD` left by an older deployment are deleted.

GitHub Actions gets a **user-assigned** managed identity with federated
credentials for `repo:<owner>/<repo>:ref:refs/heads/main` and
`repo:<owner>/<repo>:environment:production`. It holds `AcrPush` on the registry
and `Website Contributor` on the one web app — not Contributor on the resource
group, which is what the predecessor granted. There is no client secret, so
there is nothing to rotate and nothing to leak.

Federated credential subjects do not support wildcards, so a tag-triggered
deploy needs its own credential added by hand.

The identifiers land in GitHub as repository **variables**, not secrets:
`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`,
`AZURE_RESOURCE_GROUP`, `AZURE_WEBAPP_NAME`, `ACR_NAME`. They are not
credentials, and a failed run's log should be readable rather than a wall of
asterisks.

## Application settings

The toolkit is the **only** writer of application settings. Each is declared in
`deploy/app.conf` with a type, so the toolkit knows whether to prompt, generate,
compute or fix it. See [adding-an-application.md](adding-an-application.md) and
the annotated `templates/app.conf.example`.

Two settings are always applied, whatever the application declares:

- `WEBSITES_PORT=8000`
- `WEBSITES_CONTAINER_START_TIME_LIMIT=600` — the 230-second default kills a
  first boot that runs migrations.

With SQLite the toolkit also sets `WEBSITES_ENABLE_APP_SERVICE_STORAGE=true` and
pins the plan to one instance. SQLite over SMB cannot use WAL and cannot take
two writers; the guard is not advice, it is enforced.

With `--postgres`, the server is created with `--public-access None` and then
the App Service outbound addresses are added. The usual `0.0.0.0`-then-narrow
recipe leaves a real exposure window for no reason.

## Testing the toolkit

```zsh
docker-compose run --rm test bats tests/azure/toolkit.bats
```

36 tests against a mock `az` on `PATH` that records every invocation and replays
canned output — no subscription, no network, no credentials. Reads reflect prior
writes, so a greenfield deploy and a re-run are genuinely different scenarios.
`./run_tests.sh` runs pytest, shellcheck and bats together.

This exists because the predecessors' deploy scripts contained a backslash
followed by a blank line, which silently truncated an `az webapp config
appsettings set` command so that everything after a certain point never reached
Azure. There is now a test asserting that every declared setting appears in one
`appsettings set` call.
