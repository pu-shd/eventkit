# Phase 6 — `lodging-planner`

Rooms, the drag-and-drop assignment board, and the gender/roommate rules engine.

**Depends on:** Phase 1, Phase 2, and the `posted` split done in Phase 5.
**Design detail:** [`PLAN.md`](PLAN.md) §G.2.

## The highest-risk item in the whole extraction

The rules engine is the **only** place in the stack where a silent logic change harms
attendees — wrong roommate, wrong gender room — rather than merely annoying staff.

It currently lives at `admin_lodging.html:1572-1650`, inside a `<script>` tag,
reading page globals (`activeWarningsCount`, `allRegistrants`, `allRooms`). It is
untestable by construction and has **zero test coverage**: `posted/tests/test_lodging.py`
is 473 lines covering only the HTTP endpoints.

**Port it test-first, before the board UI.** Build the parity fixture from real
anonymised 2026 assignments so the rewrite is provably behaviour-preserving.

## Scope

Room CRUD with bulk create, the occupied-room edit lock, assignment with capacity
check, the drag-and-drop board in grid and list views with drag-to-reorder,
write-ins and "promote a registered non-lodging attendee", and the advisory rules
engine.

Models, routes, and what moves from which `file:line` are in `PLAN.md` §G.2.

## Three fixes, not refactors

### 1. Optimistic concurrency

There is **no version check anywhere** in `main.py:384-733`. Two planners dragging
the same board silently overwrite each other — in the week before a conference, when
that is most likely.

- Every mutating route takes `row_version`; a mismatch returns **409** plus the fresh
  entity.
- The client shows "Someone else moved Alice — reload?" and re-fetches.
- Room reorder sends the whole ordered list with a single **board-level** etag (max
  `row_version` across rooms), because per-row versions cannot express an ordering
  conflict.

This is why the datastore decision below is not the answer to concurrency.

### 2. Server-side rules as the source of truth

`rules.py` owns the logic. `GET /api/rules` returns findings. The client module is a
thin renderer plus optimistic local re-evaluation for drag feedback.

Findings are `{code, severity, subjects}` — **codes, not prose** — so severity is
configurable per event from the profile and tests assert codes:

`OVER_CAPACITY`, `ROOM_GENDER_MISMATCH`, `REQUIRE_SAME_GENDER_VIOLATED`,
`PREFER_SAME_GENDER_VIOLATED`, `ROOMMATE_NOT_REGISTERED`, `ROOMMATE_ELSEWHERE`,
`ROOMMATE_ONE_SIDED`, `SINGLE_ROOM_SHARED`.

`tests/fixtures/rules-cases.json` is committed **once** and consumed by both the
Python and the JS suite, with a parity test asserting identical code sets.

Note a live quirk to preserve or fix deliberately: `uniqueGenders` is built from
`o.gender_identity || "Unspecified"` (`admin_lodging.html:1588`), so a blank gender
counts as its own distinct gender for the same-gender rules. Decide which behaviour
you want and pin it.

Add a `RuleWaiver` table so a planner can acknowledge "yes, this couple shares a
mixed-gender room" once, rather than living with a permanent warning that trains
everyone to ignore the panel.

### 3. Name matching

`admin_lodging.html:1550` and `:1562-1569` use a bidirectional `includes()`, so
`"Bob"` matches `"Bobby Jones"`, and `find()` silently picks the first of two
Joneses.

Replace with: exact normalised (casefold, strip accents, collapse whitespace, drop
punctuation) → last name + first initial → token set. Multiple hits return
**`AMBIGUOUS`** with a candidate list rather than guessing. Roommate reciprocity
compares resolved `person_key`s, not strings.

## Datastore: keep SQLite as the default

`PLAN.md` flag F2 suggested Postgres here; **this overrides it.** Lodging is a
pre-event batch activity with two to four concurrent planners and single-digit writes
per minute. The reason people reach for Postgres is concurrent-write safety, and that
is solved by `row_version`, not by the engine.

Test `postgresql+psycopg://` in CI and document switching at more than about five
simultaneous planners, or when point-in-time recovery is required. Nightly backups
are mandatory either way (Phase 2).

## What to delete

The hardcoded gender vocabulary and `"Speaker Room"` / `"Student Room"` categories →
`profile.lodging.vocab`. Hardcoded rule severities → `profile.lodging.rules[].severity`.
The literal capacity dropdown (`:804-809`) and the twenty literal `<option>` elements
for bulk count (`:831-852`). The CAARMS title and logo.

Also fix the CSS-class derivation at `:1216` / `:1337`, which uses a **non-global**
`.replace(" ", "-")` so a multi-word value only gets its first space replaced.

## Tests

- Each rule code fires and does not fire across a matrix; severity honoured from the
  profile; a waiver suppresses.
- Capacity boundary at `==` and `>`.
- Bulk create preserves zero-pad width: `"Room 007"` → `"Room 008"`;
  `"Butler"` → `"Butler 1"`; duplicate-name rejection lists **all** collisions.
- Occupied room: name/capacity/gender edit → 400, but comments/held_by/category → 200.
- Deleting a room unassigns its occupants.
- Assign beyond capacity → 400. Stale `row_version` → 409.
- Name matcher: `"Bob"` must **not** match `"Bobby Jones"`; `"bob jones"`,
  `"Jones, Bob"`, `"BOB JONES "` must; two Joneses → `AMBIGUOUS`.
- vitest: the drop handler sends a version and handles 409 with a toast and refetch;
  grid ↔ list parity; reorder produces contiguous `sort_order`.
- **Parity**: the shared fixture produces identical code sets in Python and JS.

## Acceptance criteria

- [ ] `rules.py` ported test-first, with the parity fixture built from real anonymised
      assignments, before any board UI work.
- [ ] Every rule code covered in both suites; parity test green.
- [ ] Two browsers dragging the same attendee: one wins, the other gets a 409 and a
      reload prompt. No silent overwrite.
- [ ] Name matcher rejects substring matches and reports ambiguity.
- [ ] No vocabulary literal left in the markup.
- [ ] Retention documented: gender identity and roommate requests deleted at
      event + 30 days.

## Risks

**Sensitive data.** This app holds the most sensitive fields in the stack on a board
several staff can see. Keep `ALLOWED_ADMIN_PRINCIPALS` short, set the deletion date,
and make sure the privacy notice on the form matches what the app does.

**Behaviour-preservation is hard to prove without the fixture.** If real anonymised
2026 assignments are not available, the rewrite is a guess. Get them before starting.

**The grid and list renderers are near-duplicates today**
(`admin_lodging.html:1288-1420` ≈ `:1421-1532`). Unify them or they will drift; the
parity test only covers the rules, not the two views.
