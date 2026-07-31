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

## Not built

`auth`, `db`, `backup`, `notify`, `realtime`, `importer`, `mirror`, `admin`,
`eventbrite.client`, `eventbrite.sync`, `ui`, and the `azure` zsh toolkit. None
are stubbed — they are absent, and the README says so.

Phase 2 of the stack specifies them.

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
