# CI/CD for an event deployment

Six workflow templates ship as package data under
`src/eventkit/azure/templates/workflows/`. Copy the ones an application needs:

```zsh
mkdir -p .github/workflows
cp "$(python -c 'import eventkit.azure as a; print(a.templates_path())')"/workflows/{test,deploy,backup}.yml \
   .github/workflows/
```

`eventkit azure eject --dest ./deploy` copies the whole toolkit including the
templates, for an application that needs to diverge.

Every step is either a shell command or one of GitHub's own actions
(`actions/checkout`, `actions/github-script`) plus Microsoft's `azure/login`,
which is what performs the OIDC token exchange. No marketplace actions, no
third-party scanners, nothing to install.

| Workflow | Trigger | What it does |
|---|---|---|
| [`test.yml`](#test) | push, PR | The Docker `test` target, sqlite and postgres, plus hygiene checks |
| [`deploy.yml`](#deploy) | push to `main`, manual | Build, push, repoint the web app, wait for health |
| [`backup.yml`](#backup) | nightly | Pull the unified backup to a storage account |
| [`admin-task.yml`](#admin-task) | manual, gated | Destructive and maintenance operations |
| [`drift.yml`](#drift) | nightly | Compare live configuration to the ledger |
| [`teardown.yml`](#teardown) | manual, gated | Delete the event's resource group |

They read the repository variables `eventkit azure deploy` writes. There is
nothing to paste into Settings, and no secret to store anywhere.

---

## Two rules

**CI never writes application settings.** The toolkit is the sole writer. In the
predecessors they were defined in both the deploy script and the deploy workflow
and had already drifted three ways on the admin allow-list alone: seven
addresses in the application's config default, seven in the workflow, four in
the script. A setting change is `eventkit azure deploy`, which is idempotent and
will change only that.

**There is no stored credential.** Every workflow authenticates with
`azure/login@v2` using the federated managed identity. `id-token: write` is what
lets a job request the OIDC token; `contents: read` is everything else it gets.

---

## test

Runs `docker build --target test` and then the image, so a green CI run means
the same thing as a green `docker-compose run --rm test` on a laptop.

Matrixed over SQLite and Postgres. Deliberately, the SQLite leg starts no
Postgres service container — the predecessor started one and then pointed the
suite at an in-memory SQLite anyway.

A second job greps for institutional addresses outside `examples/`, for
committed bearer tokens, and for the generic shapes of a leaked credential —
private keys, cloud access keys, connection strings. These are public
repositories; the check is cheap and the mistake is not recoverable once pushed.

It uses `grep`, deliberately. A third-party secret scanner is one more thing to
trust, to keep current, and — in the case of the popular one — to pay for on an
organization-owned repository.

## deploy

Concurrency-grouped and cancel-in-progress, so two pushes never race for the
same web app.

The image is tagged with the commit SHA *and* `latest`. Rolling back is
re-running the workflow with `image_tag` set to a previous SHA — the tag still
exists, so the rollback needs no rebuild.

The job builds with plain `docker build`; `eventkit azure update` on a laptop
uses `az acr build`. Both are supported and produce equivalent images — which in
the predecessors was also true but unstated, so nobody knew the two paths were
meant to agree. The runner already has a Docker daemon, so no build action is
needed, and nothing third-party sits between a commit and the image that runs in
production.

After repointing the container it polls the health path for ten minutes and
fails the job if it never answers 200. A deploy that "succeeded" while the
container crash-loops is the failure this prevents.

Optional variables: `IMAGE_NAME` (defaults to the repository name), `HEALTH_PATH`
(defaults to `/healthz`).

## backup

**Mandatory, not optional.** Postgres Flexible Server gives 7–35 days of
automated backups for free. SQLite on `/home` gives nothing, so "SQLite by
default" otherwise quietly means "no backups by default" — precisely the failure
mode of the predecessor's hand-rolled dump tool, whose dumps covered two of four
tables, so restoring one silently wiped saved groups and swag inventory.

The job pulls the application's own unified backup, refuses to store a file that
carries no row counts, and uploads with `--overwrite false` so a bug cannot
destroy yesterday's good copy. Failure opens (or comments on) an issue labelled
`backup`.

Put the storage account **outside** the event's resource group. Otherwise
teardown takes the backups with it.

Set `BACKUP_STORAGE_ACCOUNT`, and optionally `BACKUP_CONTAINER`.

> A backup file is a full export of everyone's personal data. Access to that
> storage account is access to the attendee list.

## admin-task

Replaces the predecessor's `clear_data.yml`, which worked by curling
`POST /api/admin/clear` — a route left deliberately unauthenticated so that the
workflow could reach it. Anyone who knew the path could post
`{"target":"both","confirm":"DESTROY"}` and erase every registration and
payment.

That route is gone. Operations now run *inside* the container over `az webapp
ssh`, reached through Azure with the federated identity, so invoking one
requires a role assignment on the resource. There is no inbound path.

It takes a snapshot before every task, including the read-only ones, and
requires the web app name typed exactly for `clear-data`. Point it at an
environment with required reviewers (`production-admin`) so a second person
approves.

Set `PACKAGE_NAME` to the application's Python package.

## drift

Three things go wrong over two years with nobody touching the code:

1. **The plan is scaled, its outbound IP addresses change, and Postgres stops
   admitting it.** The application is simply down. This is the one case the job
   *remediates* rather than reports, because the failure is an outage and the
   fix is unambiguous.
2. **The Easy Auth client secret expires.** Admin sign-in starts returning 500
   and nothing says why. Warned at 60 days; `eventkit azure secrets rotate` uses
   `--append`, so there is no window. This is the most likely "the site broke and
   nobody knows why" event on a two-year horizon, and nothing in the
   predecessors would have caught it.
3. **A gate was skipped during a rush and never revisited.** Critical skipped
   gates resurface here until met or acknowledged with a date and a reason.

Findings open one issue labelled `drift` and comment on it thereafter; a clean
run closes it. Set `EVENTKIT_SPEC`, `APP_NAME`, and for Postgres `DATASTORE` and
`AZURE_DB_SERVER`.

## teardown

Requires the resource group typed exactly *and* an explicit "a backup has been
pulled and verified" checkbox, and runs in a reviewed environment.

The predecessors had a teardown script defaulting to one resource group and a
teardown workflow hardcoding a different one, so a local teardown and a CI
teardown destroyed different things. There is now one name and it comes from the
ledger.

The group holds every application deployed for the event, not only this one.
That is the point — but it means running this from one application's repository
removes the others too.

---

## Variables reference

Written by `eventkit azure deploy` (its `oidc` step):

| Variable | |
|---|---|
| `AZURE_CLIENT_ID` | user-assigned identity used by Actions |
| `AZURE_TENANT_ID` | |
| `AZURE_SUBSCRIPTION_ID` | |
| `AZURE_RESOURCE_GROUP` | |
| `AZURE_WEBAPP_NAME` | |
| `ACR_NAME` | |

Set by hand, per workflow:

| Variable | Used by | Default |
|---|---|---|
| `IMAGE_NAME` | deploy | repository name |
| `HEALTH_PATH` | deploy | `/healthz` |
| `BACKUP_STORAGE_ACCOUNT` | backup, admin-task | — |
| `BACKUP_CONTAINER` | backup, admin-task | `backups` |
| `PACKAGE_NAME` | admin-task | — |
| `EVENTKIT_SPEC` | drift | — |
| `APP_NAME` | drift | — |
| `DATASTORE` | drift | — |
| `AZURE_DB_SERVER` | drift, Postgres only | — |
