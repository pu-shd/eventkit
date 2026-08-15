# Status

As of 2026-07-31. Read this before picking anything up.

## Done and live

**The security fixes are merged and deployed**, and were verified against
production rather than trusted from a green workflow — a deploy can succeed while
the container fails to boot on newly-required settings.

| Repo | Merge | Verified |
|---|---|---|
| `pubino/ticketed` | `e381801` (PR #2) | `POST /api/admin/clear` → **401** anonymous |
| `pubino/posted` | `37461d8` (PR #2) | `GET /api/presenters` → 20 records, 6 allow-listed fields, no `@` |

Eight issues fixed with regression tests: the unauthenticated database wipe, the
webhook token logged at INFO on every call, the public PII leak, placeholder
secret defaults, seven committed netIDs, `enable_restore=True`, the
header-spoofable dev-admin bypass, and the embedded CAARMS schema that always won.

Both predecessor repos are tagged `pre-extraction` (`11f0a10`, `ea5b3da`).

**This repository** is public with CI green across eight jobs. Built and tested:
`identity`, `drupal`, `eventprofile`, `webhook`, `logging`, `eventbrite.models`,
`eventbrite.aggregate`, `testing`, `cli`. 503 tests, 86% line coverage.

**Documentation** for building the next event's Drupal form and wiring it to
deployed apps: [`../drupal/`](../drupal/).

## The predecessors are gone

**2026-08-15: both Azure deployments were backed up and torn down.** The event is
over and the stack that replaced them is published, so the resource groups
`…-caarms-reconciler-rg` and `…-posted-rg` were deleted, taking two B1 plans, two
container registries, a Postgres Flexible Server and two web apps with them. The
subscription is now empty.

The final backup is local, outside any repository, and was verified rather than
assumed: `posted.db` passes `pragma integrity_check`, and the reconciler's
`pg_dump` was **restored into a scratch PostgreSQL 18 container with zero errors
and matching row counts**. Contents: posted 106 rows (20 presenters, 63
registrants, 23 rooms); reconciler 2,014 rows (42 payments, 62 registrants, 2
saved groups, 7 shirt-inventory rows, 1,901 sync logs).

Worth noting: the predecessor's own `db_admin_tool.py` dumped only `registrants`
and `payments`. A restore from one of its backups would have silently discarded
1,910 of those 2,014 rows.

## Still needs a human, outside Azure

Tracked in `HANDOVER-URGENT.md` in the working directory (not committed — it
contains operational detail about what was a live deployment). What remains:

1. **Rotate the two third-party credentials.** Teardown invalidated the App
   Service settings, so the webhook tokens and the Easy Auth client secrets died
   with the apps. Two credentials belong to other vendors and remain valid until
   revoked **in their consoles**: `EVENTBRITE_API_TOKEN` and `RESEND_API_KEY`.
   Both were written to the log stream on every webhook call for the life of the
   deployment, so both should be treated as compromised. Two Entra app
   registrations also survive the resource groups and can be deleted separately.

2. ~~**Set `ALLOWED_ADMIN_PRINCIPALS` on `posted`.**~~ **Moot** — the deployment
   is gone.

## Decisions taken, so they are not re-opened

**The WAF bypass header stays as it is.** WDS operates one shared bypass header
and there is no per-consumer secret to be had, so `x-wdsoit-bot-bypass` is the
only mechanism available. Accepted as a known limitation rather than treated as
an open question.

Its practical consequences are unchanged and already enforced: the header value
comes from an environment variable with **no default**, asset mirroring is
build-time and opt-in rather than something that runs on every application boot,
and `drupal-event-forms`' `tools/redact.py` rejects the header **name** — because
a bypass whose only secret is its name is published the moment the name is.

**The nine speaker prefill tokens were not rotated; the copies were destroyed.**
No event is running, so there is nothing for a token to protect and nowhere it
needs to work. Three local files carrying 26 real bearer links were deleted:
`links-for-speakers.html`, `administrative-utilities.html` and
`speaker-bios-talks.html`. The tokens remain valid inside Drupal against
submissions for a finished event; regenerate them there if that form is ever
reused.

**The `posted` rollback is resolved** — see phase 8 above. The two `big-agenda`
commits were recovered from unreferenced objects and pushed as
`recovered/big-agenda` before archiving.

## The roadmap is merged

All nine roadmap documents are on `main`. Phases 1–8 are **plans of work, not built
code** — see [`README.md`](README.md) for the ordering and
[`PLAN.md`](PLAN.md) for the design detail behind each.

Two PR numbers were superseded during the merge (#2 → #11, #3 → #10). GitHub closes
a pull request when its base branch is deleted, and a PR closed that way can be
neither reopened nor retargeted. Nothing was lost; the lesson for the next stack is
to retarget every PR to `main` *before* merging any of them, and to delete branches
only at the end.

## Phase 1 is complete

Every library module is built and tested: `identity`, `drupal`, `eventprofile`,
`webhook`, `logging`, `eventbrite` (models, aggregate, client, sync), `db`, `auth`,
`backup`, `realtime`, `notify`, `importer`, `admin`, `ui`, `mirror`, `testing`, `cli`.

**833 tests, released as v0.2.0.** Applications currently pin:

```
eventkit-core[app] @ https://github.com/pu-shd/eventkit/archive/refs/tags/v0.3.0.tar.gz
```

## Phase 2 is complete

`eventkit azure` is built: `lib/{boot,color,log,prompt,state,name,az,gh,secrets,manual,verify,conf,steps}.zsh`,
fifteen numbered step files, and one dispatcher with the verbs `deploy`, `resume`,
`update`, `teardown`, `status`, `doctor`, `adopt`, `drift`, `gate`, `logs`, `open`
and `eject`. Documented in [`../azure/`](../azure/README.md).

The manual-step gate was built before the provisioning steps, as planned: it is
the stated requirement, and it is what let the previously undocumented Easy Auth
configuration be written down as a checklist plus a verifiable predicate.

**36 bats tests against a mock `az`** covering naming determinism and clamping,
ledger round-trips, the no-secrets-in-the-ledger invariant, dry run, idempotence
and resume, all five gate behaviours, managed identity, the settings-reach-one-call
guard, the SQLite pins, Postgres `--public-access None`, and every other verb.
`./run_tests.sh` runs pytest, shellcheck and bats together.

Six CI/CD workflow templates ship as package data: `test`, `deploy`, `backup`,
`admin-task`, `drift`, `teardown`. All authenticate with the federated managed
identity; none writes an application setting.

### Defects found by running the toolkit

Three were invisible to inspection, and all three are the same shape as the bug
class this whole extraction exists to remove.

- **`(( EK_DRY_RUN )) && print …` as a function's last statement.** In zsh,
  `(( expr ))` exits non-zero when the expression evaluates to 0, so with dry-run
  off `ek_print_plan` returned 1, and under `setopt err_return` that aborted the
  caller. A plain `deploy` died silently immediately after printing its plan,
  while `--dry-run` worked perfectly. The same hazard had already been fixed once
  for `(( i++ ))` with `i=0`; it is now an explicit `if` in both places, with the
  reasoning in a comment in `lib/log.zsh`.
- **`local … status …`** in `ek_steps_run`. `status` is read-only in zsh, so
  the declaration failed and the function returned before doing anything.
- **Invalid TOML in all five shipped `deploy/app.conf` files** — setting tables
  written as `name = "X"; type = "computed"` on one line. TOML has no statement
  separator, so the toolkit could read no settings at all. Already published to
  five repositories, caught only when the toolkit first tried to parse one. Fixed
  in all five plus the scaffold, and each repository now carries
  `tests/test_deploy_conf.py` so it cannot recur.

Two of the test doubles were also lying: a mock `openssl` that printed nothing
(so generated webhook tokens were empty, and the deploy fell through to
"prompt the operator"), and a mock `az` whose reads did not reflect prior writes
(so the identity step could not find the principal it had just assigned). Both
now model the real thing.

## Phase 8 is complete — the extraction is finished

[`drupal-event-forms`](https://github.com/pu-shd/drupal-event-forms) and
[`event-stack`](https://github.com/pu-shd/event-stack) are published, and both
predecessors are archived with pointer READMEs.

**The `posted` rollback is resolved.** The two `big-agenda` commits that passed
CI against `main` on 2026-06-26 were never ancestors of `main` — it had been
rolled back. GitHub still held the objects as unreferenced, so they were fetched
by full SHA and pushed as `recovered/big-agenda` before archiving, which would
otherwise have left them to garbage collection. The work is a kiosk agenda
dashboard: ~1,800 lines, never reviewed, never deployed, and it contains speaker
photographs. `posted` is private, so the branch exposes nothing.

Both structural bugs in the Drupal forms are fixed with regression fixtures
built from the original production YAML, and all three validators are proved to
reject rather than merely to exist.

### One defect found by phase 8, in the applications

Pointing `event-stack`'s `verify-stack.sh` at a locally built runtime image
booted one for the first time — and **no application container could start**.
The runtime `CMD` ran `uvicorn`, which appeared in no dependency list. Nothing
caught it: the test stage installs the same dependencies but never starts the
server the way production does, so a green suite said nothing about whether the
shipped image runs.

Fixed in all five repositories, each of which now has a `runtime-boots` CI job
that builds `--target runtime`, starts it with the environment App Service
supplies, waits for `/healthz`, and checks an admin route still refuses an
anonymous caller.

`verify-stack.sh` itself contributed two more zsh lessons, both found by running
it rather than reading it: `local path` blanks `PATH` (the same family as
`local status`), so a healthy application reported uniform "did not answer"
failures; and curl writes `000` on a connection failure *and* exits non-zero, so
a `|| print 000` fallback produced `000000`, which matched no case arm and let a
dead host pass four of six checks.

## Defects found by actually running things

Worth reading, because each was invisible to inspection and three were found only
by executing something for the first time.

**The test suite had never been run.** Two harness bugs stopped collection
outright: the plugin was registered twice (once via the `pytest11` entry point,
once via `pytest_plugins` in `conftest.py`), and coverage could not write because
`COPY` left `/app` root-owned while the container runs as `app` — which failed
*after* every test had passed.

Of the four test failures, two were real defects:

- `coerce_name(0)` returned `Name('0', None)`, against its own documented contract
  of "None/other → (None, None)". A junk value would have created a registrant
  named `0`.
- `TicketTier.discount_code_env` accepted `"2030EXAMPLEGA"` — a string shaped
  exactly like a pasted discount code. The validator checked `isupper()` and
  `isalnum()`, which a real code satisfies, so the one field whose entire purpose
  is "this holds a variable name, never a code" did not enforce it.

**The most consequential defect was found while writing documentation.**
`WebformSchema` read only top-level elements, but Drupal nests fieldset children in
the export while submission data is flat. Against the *actual* CAARMS export that
lost six fields — `gender_identity`, `roommate_preference`,
`identified_roommate`, `poster_title`, `faculty_adviser_name`,
`poster_presentation_abstract`: the entire lodging and poster halves of the form —
each reported as "could not infer an element", with empty columns as the only
symptom. The shipped example schema had been hand-flattened, which is why no test
caught it. Inference against the real export went from 9 to 15 of 18 fields.

**The remaining three are `uuid`, `sid` and `serial`, and the real registration
export defines no such elements at all.** Those values arrive only as Remote Post
submission properties. The earlier claim that "the webform emits a submission
uuid" came from the *embedded* schema in `ticketed/backend/schema_parser.py`,
which differs from the form actually in production. Since `uuid` is the join key
the whole independent-database design rests on, verify it on the first test
submission of every new event.

## Open decisions

**PyPI distribution name.** `pyproject.toml` declares `eventkit-core`; the bare
`eventkit` name is taken by an unrelated library. Whether `eventkit-core` is free
is unverified. Blocks nothing at v0.1 — consumption is via GitHub codeload
tarball — but check before the first upload. The import name is `eventkit` either
way.

**GitHub org.** This repository is `pu-shd`. `PLAN.md` says `pu-sherrerd`
throughout, written before the org was named. The two predecessor repos are under
`pubino`. Read `pu-sherrerd` in `PLAN.md` as `pu-shd`.

**No third-party CI tooling.** `PLAN.md` phase 0 called for `gitleaks` on every
PR. It is gone, along with every marketplace action — `gitleaks/gitleaks-action`
(which also wants a paid licence on an organization-owned repository),
`astral-sh/ruff-action`, `docker/setup-buildx-action`, `docker/build-push-action`.
What runs now is shell plus GitHub's own `actions/*` and Microsoft's
`azure/login`, which performs the OIDC exchange against the deployment target.

Secret scanning is `grep` over six credential shapes: private keys, cloud access
key ids, provider tokens, connection strings, and client-secret and password
literals. It was verified in both directions — it fires on planted secrets, and
it stays quiet on `password = os.environ[...]` and on placeholders. The named
checks for the values this codebase has actually leaked are unchanged.

**Three deviations from `PLAN.md`,** all deliberate:

1. v0.1 ships one distribution, not three. A codeload tarball builds whatever the
   root `pyproject.toml` declares, so the three-way split only becomes real at
   PyPI publication, which the plan puts at v0.2.
2. `EmailStr` is avoided in the profile models — it requires `email-validator`,
   which pulls in `dnspython`, breaking the import-weight contract that
   `tests/unit/test_import_weight.py` enforces. A pattern-constrained `str` is
   used instead; addresses from Drupal still go through `coerce_email`.
