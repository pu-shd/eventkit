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

## Still needs a human with console access

Tracked in `HANDOVER-URGENT.md` in the working directory (not committed — it
contains operational detail about a live deployment). The four open items:

1. **Rotate every live secret.** Now unblocked: the logging lines are deployed, so
   a new token no longer lands in the log on the next call. Assume every current
   value is compromised — they were written to the App Service log stream and Log
   Analytics on every webhook call for the life of the deployment.
   Rotating a webhook token has a gap: between setting the app setting and updating
   the Drupal handler, submissions get 403 and **Drupal does not retry**. Do it
   outside a registration window.
2. **Set `ALLOWED_ADMIN_PRINCIPALS` as a repo secret or variable on `posted`.** CI
   no longer writes a hardcoded fallback; the last deploy logged a warning and left
   the existing app setting alone. The allow-list is currently unmanaged.
3. **Decide on the WAF bypass value.** Get a real shared secret, or drop the
   mechanism. The header is now omitted when unset, so asset mirroring degrades.
4. **Regenerate the nine speaker prefill tokens.** They are bearer credentials and
   leaked through a saved HTML page.

Also unresolved: two `big-agenda` commits passed CI against `posted`'s `main` on
2026-06-26 but `main`'s tip is the commit before them, so `main` was rolled back.
That work is not in `origin` and not covered by the `pre-extraction` tag. If the
rollback was not deliberate it is only recoverable from a local clone.

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

**833 tests, released as v0.2.0.** Applications pin it as:

```
eventkit-core[app] @ https://github.com/pu-shd/eventkit/archive/refs/tags/v0.2.0.tar.gz
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

What remains outside this repository: the five applications (phases 3–7).

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

**Two deviations from `PLAN.md`,** both deliberate:

1. v0.1 ships one distribution, not three. A codeload tarball builds whatever the
   root `pyproject.toml` declares, so the three-way split only becomes real at
   PyPI publication, which the plan puts at v0.2.
2. `EmailStr` is avoided in the profile models — it requires `email-validator`,
   which pulls in `dnspython`, breaking the import-weight contract that
   `tests/unit/test_import_weight.py` enforces. A pattern-constrained `str` is
   used instead; addresses from Drupal still go through `coerce_email`.
