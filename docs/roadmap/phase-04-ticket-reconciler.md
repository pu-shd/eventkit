# Phase 4 — `ticket-reconciler`

Reconciliation of Drupal registrations against Eventbrite sales, front-desk
check-in, swag inventory, waivers.

**Depends on:** Phase 1 (`auth`, `webhook`, `backup`, `eventbrite.client`/`sync`,
`realtime`, `notify`), Phase 2, and the template established by Phase 3.
**Design detail:** [`PLAN.md`](PLAN.md) §G.1.

## Why second among the apps

Highest value and highest risk, so it runs on a foundation that Phase 3 has already
validated against real Drupal traffic. It carries `auth`, `realtime`, the check-in
key migration, and most of the security table.

**Cut over well outside any registration window.**

## Scope

The reconciliation report engine, Eventbrite sync with the auto-sync loop,
front-desk check-in with a four-state cycle per day key, swag inventory /
replacement / issuance, saved name-filter groups, fee waivers with justification,
refund override, Excel export, QR purchase links, and the stats cards.

Models, the full route table, and what moves from which `file:line` are in
`PLAN.md` §G.1.

## The one piece worth extracting carefully

`build_report(registrants, payments, profile)` — from `ticketed/backend/main.py:448-579`
— becomes a **pure function** with no `Session` and no `settings`. It is the most
valuable and least testable code in the whole stack, and it is currently 130 lines
inside a route handler.

Its truth table is the priority test target:

- A manual `linked_payment_id` wins over an email match.
- A manually-claimed payment must **not** also match its own email-owner, nor a
  different registrant. (The guard is at `main.py:471-474`; keep it and test it.)
- Each of the seven derived statuses: `Paid`, `Complete`, `Pending`, `Exempt`,
  `Waived`, `Refunded`, `Cancelled`.
- A leftover `status=="paid"` payment becomes an `Unmatched` row; a leftover
  `refunded` one is suppressed.
- Sort stability.

## Three changes that are not refactors

### `Payment.email` drops `unique=True`

`models.py:29` makes one purchaser buying two tickets a 500. The HEAD commit of the
archived repo worked around it by aggregating attendees by email before insert, but
**the constraint is the bug**. Drop it; keep the index.

### Migration `0002` — check-in keys become ISO dates

`checkin_status` is a JSON blob keyed `"6/28"`. Rewrite every key to the profile's
ISO day key **by position, not by parsing** — `"6/28"` is ambiguous across years and
both `"7/1"` and `"07/01"` appear in the live data. Fail the migration loudly on a
key that is neither in the legacy set nor already ISO.

This is exactly the migration the hand-rolled migrator cannot express, and it is why
Phase 1 adopts Alembic.

### Realtime becomes polling

Four `broadcast_*` helpers (`main.py:713-772`) iterate a module-global
`active_checkin_sockets` list. With two App Service instances a check-in on one never
reaches a browser on the other, and every send error is swallowed — two front-desk
iPads silently disagreeing about who is checked in.

Default: `GET /api/changes?since=` at 3s while the check-in tab is focused, 30s when
blurred. WebSocket is opt-in via the profile, and even then polling stays the
fallback so a dropped socket cannot strand the front desk mid-registration.

## What to delete

- **`main.py:499-511`** — the hardcoded `CODE_AFFILIATE` / `CODE_GENERAL` discount
  codes, the `princeton.edu` email-domain branch, and the `caarms-2026-tickets-{id}`
  Eventbrite slug. Replaced by `profile.ticketing.resolve_tier()`.
- The hardcoded `status_order` dict (`main.py:568-576`) → `profile.ticketing.status_order`.
- The `"6/28"…"7/1"` day keys (`frontend/app.js:1258-1262`).
- `t_shirt_size` → `swag_size`. **This app owns swag exclusively** — see Phase 5.
- The QR call to `api.qrserver.com` (`app.js:1237`), which shipped attendee purchase
  URLs *including the live discount code* to a third party on every render, and broke
  the front desk on captive-portal wifi. Vendor a ~4 KB MIT encoder and render SVG
  locally.
- `site_name` / `site_slogan` defaults (`config.py:8-9`) and `frontend/images/caarms_0.png`
  (1.0 MB, served unoptimized).

`POST /api/admin/clear` and its workflow are **already deleted** in the archived
repo. Do not recreate an HTTP path for destructive operations; see Phase 2.

## Excel export

Keep the in-browser SheetJS path *and* add `GET /api/reports/export.xlsx`. The
browser path exports only filtered rows, which is what staff actually want; the
server path is the auditable full dump. Vendor SheetJS with SRI.

## Tests

Beyond the `build_report` truth table:

- `resolve_tier` across all five legacy slugs plus anonymous / affiliate / student /
  domain cases.
- Check-in state cycle 0→1→2→3→0 with ISO keys.
- Migration `0002` round-trip on a fixture database containing **both** `"6/28"` and
  `"07/01"`.
- Swag inventory going negative returns 400, not a negative count.
- Webhook rejects absent and bad tokens; **no token or header dump reaches the logs**
  (this test already exists in the archived repo — carry it forward).
- `/api/admin/clear` returns 404.
- vitest: report-table sort matches server order; `esc()` on a registrant named
  `<img onerror>`; day columns rendered from profile keys; export column set.

## Acceptance criteria

- [ ] `build_report` is pure, takes lists plus a profile, and has a passing truth table.
- [ ] Migration `0002` verified on a copy of the live database before cutover.
- [ ] Polling change feed correct with two instances running.
- [ ] No discount code, day key, or swag size literal anywhere in the source.
- [ ] A full test registration cycle rehearsed: register → purchase → sync → check in
      → issue swag → waive a fee → refund → override.
- [ ] Cutover performed outside a registration window.

## Risks

**The reconciliation semantics are subtle and staff trust them.** Extract
`build_report` verbatim first with tests that pin current behaviour, *then* refactor.
Do not improve the logic and move it in the same commit.

**The check-in migration is one-way.** Take a backup, run it against a copy, diff the
blobs, and only then run it for real.

**Cutover risk is concentrated here.** Unlike `poster-gallery`, a regression at the
front desk during check-in is immediately and publicly bad. Parallel-run through a
full rehearsal before pointing the Drupal handler at the new app.
