# CI/CD workflows

Six templates ship as package data. Copy the ones you need:

```zsh
TPL="$(python -c 'import eventkit.azure as a; print(a.templates_path())')"
mkdir -p .github/workflows
cp "$TPL"/workflows/{test,deploy,backup}.yml .github/workflows/
```

| Workflow | Trigger | Does |
|---|---|---|
| `test.yml` | push, PR | Docker `test` target on SQLite and Postgres, plus hygiene greps |
| `deploy.yml` | push to `main`, manual | Build, push, repoint the web app, wait for health |
| `backup.yml` | nightly | Pull the unified backup to a storage account |
| `admin-task.yml` | manual, reviewed | Destructive and maintenance operations |
| `drift.yml` | nightly | Compare live config to the ledger |
| `teardown.yml` | manual, reviewed | Delete the event's resource group |

They read the repository variables `eventkit azure deploy` writes. Nothing to
paste into Settings, no secret to store.

Every step is a shell command or one of GitHub's own actions, plus
`azure/login` for the OIDC exchange. No marketplace actions, no third-party
scanners.

## Two rules

**CI never writes application settings.** The toolkit is the only writer. A
setting change is `eventkit azure deploy`, which is idempotent.

**No stored credential.** Every workflow authenticates with the federated
managed identity. `id-token: write` is what lets a job request the token.

## deploy

Concurrency-grouped, so two pushes never race for one web app. Images are tagged
with the commit SHA *and* `latest`, so rolling back is re-running the workflow
with `image_tag` set to an earlier SHA — no rebuild.

After repointing the container it polls the health path for ten minutes and
fails if it never answers 200.

Optional variables: `IMAGE_NAME` (defaults to the repository name),
`HEALTH_PATH` (defaults to `/healthz`).

## backup

**Not optional with SQLite.** Postgres Flexible Server gives 7–35 days of
automated backups; `/home` gives nothing.

Pulls the application's own backup, refuses to store a file with no row counts,
uploads with `--overwrite false`. Failure opens an issue.

Put the storage account **outside** the event's resource group, or teardown
takes the backups with it. Set `BACKUP_STORAGE_ACCOUNT`.

> A backup file is a full export of everyone's personal data.

## admin-task

Runs operations inside the container over `az webapp ssh` — no inbound route, no
unauthenticated endpoint. Takes a snapshot first, even for read-only tasks, and
requires the web app name typed exactly for `clear-data`. Point it at an
environment with required reviewers. Set `PACKAGE_NAME`.

## drift

Three things break over two years with nobody touching the code:

1. **The plan is scaled and Postgres stops admitting it.** The job re-adds the
   outbound addresses automatically — the one case it remediates rather than
   reports, because the failure is an outage.
2. **The Easy Auth client secret expires.** Warned at 60 days;
   `eventkit azure secrets rotate` uses `--append`, so there is no window.
3. **A skipped gate was never revisited.** Critical ones resurface here.

Opens one issue labelled `drift` and closes it when clean. Set `EVENTKIT_SPEC`,
`APP_NAME`, and for Postgres `DATASTORE` and `AZURE_DB_SERVER`.

## teardown

Requires the resource group typed exactly, an explicit "I have a backup"
checkbox, and a reviewed environment. The group holds every application for the
event, so running it from one repository removes the others too.

## Variables

Written by `eventkit azure deploy`: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`,
`AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`, `AZURE_WEBAPP_NAME`, `ACR_NAME`.

Set by hand as needed: `IMAGE_NAME`, `HEALTH_PATH`, `BACKUP_STORAGE_ACCOUNT`,
`BACKUP_CONTAINER`, `PACKAGE_NAME`, `EVENTKIT_SPEC`, `APP_NAME`, `DATASTORE`,
`AZURE_DB_SERVER`.
