# Phase 2 — The Azure bootstrap toolkit

`eventkit azure deploy | resume | update | teardown`, plus the polling manual-step
gate that is the whole point of it.

**Depends on:** nothing in Phase 1 strictly — the shell runs before the app exists.
**Blocks:** every app deployment, and the cutover in Phases 3–7.
**Design detail:** [`PLAN.md`](PLAN.md) appendix §F.

> §F carries a caveat worth repeating: the subagent design for it was truncated,
> so the `lib`/verb/gate/`app.conf` structure there is reconstructed. The
> recommendations, flags and build order are verbatim. **Verify every `az` command
> and `--query` expression against your installed CLI before relying on it.**

## The requirement

> "Local bootstrap script with deploy/resume/update/teardown that's friendly,
> interactive, colorful, and switches to waiting/pausing for a manual step to be
> completed if necessary."

That last clause is the design centre. The predecessor scripts *print* manual
instructions and then exit or barrel on. This must **block, show the exact steps,
poll Azure to detect completion, and resume**.

## Build order

From `PLAN.md` §F.12. Each phase is independently useful.

| Step | Deliverable | Unblocks |
|---|---|---|
| 0 | `lib/{boot,color,log,prompt,state,name,az}.zsh` + `bats` harness + mock `az` + Docker `test` target | Everything. Test this hardest — every later step trusts `ek_name` and `ek_state_*`. |
| 1 | `lib/manual.zsh` + `lib/verify.zsh` + the `easy-auth` and `acrpull` gates | Lets the live deployment's undocumented Easy Auth configuration be verified and written down **before** any provisioning code exists. |
| 2 | `conf.zsh` + `steps.zsh` + steps 00–60 + `deploy`/`resume`/`status`/`doctor` + `poster-gallery`'s `app.conf` | One app fully managed end to end. |
| 3 | App-side: `/healthz`, `/api/webhook/status`, `/api/admin/tasks`, unified backup, Alembic, startup auth assertion | The `webhook` gate, `admin-task.yml`, `backup`/`restore`. |
| 4 | `update`/`teardown`/`drift`/`adopt` + reusable workflows + `oidc` with split identities | The live migration. |
| 5 | `bootstrap`, `domain`, `secrets rotate`, `eject`, `logs`, `open`, remaining `app.conf` files | The other four apps; a sixth app becomes one TOML file. |

**Step 1 before step 2.** The manual-step gate is the stated requirement, and it is
also what lets you verify the live `posted` deployment's auth posture this week
rather than after a toolkit that can provision anything.

## The manual-step gate

`ek_await_manual_step` must:

- print a numbered, copy-pasteable checklist **plus a deep link to the exact Azure
  portal blade**;
- poll a verification predicate on an interval, with a spinner, elapsed time, and a
  line naming *what it is waiting for*;
- succeed the instant the predicate passes;
- accept `[s]kip / [r]etry now / [o]pen portal / [q]uit and resume later` keypresses
  while waiting;
- on quit, persist position so `resume` re-enters exactly there;
- time out with the checklist and a `resume` command rather than hanging;
- under `--yes`, **fail fast with the checklist** rather than blocking a CI job.

Gates and their predicates are tabulated in `PLAN.md` §F.3. The one that matters
most:

```sh
az webapp auth show -g $RG -n $APP \
  --query "identityProviders.azureActiveDirectory.registration.clientId" -o tsv
```

Empty output means every admin route is trusting a header that nothing is setting.
**`posted/deploy/deploy.sh` never configured Easy Auth at all** — it worked because
someone did it by hand in the portal, unscripted and undocumented, so it is not
reproducible.

Skipped gates with `risk: critical` surface in nightly `drift`.
`gate ack --until <date> --reason <ticket>` suppresses one until the date and then
resurfaces it loudly — because a permanently-failing nightly alert is how the
`&>/dev/null` habit gets established.

## Fixes, not ports

The predecessor scripts are 70–90% duplicated boilerplate with real bugs. Do not
port these:

- **`ticketed/deploy/deploy.sh:391`** — a backslash followed by a blank line
  truncates `az webapp config appsettings set`, so everything from
  `EVENTBRITE_API_TOKEN` onward is silently dropped.
- **App settings are written in two places** (`deploy.sh` and `deploy.yml`) and have
  already drifted three ways on the admin-principal list alone: 7 emails in
  `posted/backend/config.py:25`, 7 in `deploy.yml:100`, 4 in `deploy.sh:137`.
  **The toolkit becomes the only writer of app settings; CI only ships images.**
- **Two build paths** — `az acr build` in the shell, local `docker build` in CI.
  Pick `docker buildx` for CI (it already has a daemon, and gets layer caching) and
  keep `az acr build` for operators without local Docker. Document that both exist
  and produce equivalent images; today they silently differ.
- **`RG_NAME` drift** — `teardown.sh:21` defaults to one resource group while
  `.github/workflows/teardown.yml:28` hardcodes a different, real one. A CI teardown
  and a local teardown currently target different resource groups.
- **`--public-access 0.0.0.0` then narrow.** Use `--public-access None` and then add
  only the App Service outbound IPs. There is no reason for the exposure window.
- **`WEBSITES_CONTAINER_START_TIME_LIMIT=600`** for every app with startup
  migrations. The 230s default will kill a first boot.
- **Never interpolate `secrets.*` into `if [ -n "…" ]` shell tests.**
- Delete the postgres service container from the test job that then sets
  `DATABASE_URL=sqlite:///:memory:`.

## Two things nightly `drift` must do

**Auto-remediate the DB firewall.** App Service outbound IPs change when the plan is
scaled or moved, and the database then silently becomes unreachable. This is one of
the few cases where drift should fix rather than report, because the failure mode is
an outage.

**Alert on Easy Auth client secret expiry at < 60 days.** The provisioning creates
one with `--years 2`; in two years admin login breaks with a 500 and no warning.
This is the most likely "the site broke and nobody knows why" event on a two-year
horizon, and nothing today would catch it.

## Backups are mandatory, not optional

Postgres Flexible Server gives 7–35 days of automated backups free. SQLite on
`/home` gives nothing automatic. So "SQLite by default" silently means "no backups
by default" — precisely the `db_admin_tool.py` failure mode being eliminated. Ship a
scheduled snapshot to a Storage Account with lifecycle rules as part of this phase,
not later.

## Destructive operations without an open HTTP route

`clear_data.yml` is deleted and `POST /api/admin/clear` is now authenticated. The
replacement: a workflow that OIDC-federates into Azure and runs the operation as a
one-shot container command. Keep the HMAC task token (`eventkit.admin`) as the
documented fallback, not the primary path.

## Testing shell

`shellcheck` on every script, `bats-core` for the pure helpers, and a **mock `az`
on `PATH`** that records invocations and replays canned JSON — so the whole `deploy`
flow runs end to end with no Azure account. Same for `gh`, `dig`, `curl`. A worked
example test is in `PLAN.md` §F.11; the important one asserts that interrupting at a
gate leaves it `pending` and that `resume` does **not** re-create the ACR.

## Acceptance criteria

- [ ] `deploy` is idempotent and resumable; `resume` replays only `pending` steps.
- [ ] At least one gate blocks, polls, and succeeds on the predicate passing.
- [ ] Interrupt-at-gate then `resume` completes without re-creating resources.
- [ ] `--dry-run` prints every `az` command and executes none.
- [ ] `--yes` fails fast at a gate instead of blocking.
- [ ] `teardown` leaves nothing behind; `adopt` imports the two live deployments
      into the ledger without touching them.
- [ ] `shellcheck` clean; `bats` suite green inside `docker-compose run --rm test`.
- [ ] Nothing in the repo hardcodes a resource name, netID, or subscription.

## Risks

**`az` is a version-sensitive dependency and the toolkit's entire surface.**
`authV2` is an extension whose commands have changed; `az webapp config ssl create`
is marked preview; `az acr repository show-manifests` is deprecated. Pin a minimum
and a tested maximum in `doctor`, record the version in the ledger's `history`, and
prefer `az rest` with a pinned `api-version` for auth config and federated
credentials.

**Federated credential subjects do not support wildcards.** Tag-triggered deploys
need the preview flexible-FIC path or one credential per pattern.

**The committed state ledger is a supply-chain surface.** Anyone who can merge can
repoint `names.resourceGroup` — including for a teardown. `CODEOWNERS` on
`.eventkit/**` plus a CI check that `names.*` and `subscriptionId` are unchanged
without an `infra-change` label.

**`pipx run --spec <tarball>` has no integrity story.** A mutable tag over TLS with
no signature, running with Azure credentials in the environment. Pin by commit SHA
in CI, and add `doctor --verify-self` printing the resolved version and commit.
