# 06 — New-event runbook

An ordered checklist from nothing to first live registration. Timings assume a
multi-day conference with lodging; a one-day symposium compresses to about a
week of elapsed work.

## Phase 1 — Decide scope (T-10 weeks)

**Pick your apps.** You do not need all five.

| Need | App |
|---|---|
| Paid or conditionally-priced tickets, front-desk check-in, swag | `ticket-reconciler` |
| Assigning attendees to rooms | `lodging-planner` |
| Printing badges | `nametag-press` |
| Per-person reimbursement / release / upload links | `link-forge` |
| Public poster or talk directory | `poster-gallery` |

Each app is one Azure Web App, one database, one webhook, one deploy pipeline.
Adding one you will not use costs real money and is one more thing to patch.

**Write the event profile.** Start from `examples/caarms-2026/event-profile.yaml`.

```sh
eventkit profile validate my-event/event-profile.yaml
```

Fill in at minimum: `event`, `schedule` (including `checkin_days` as **ISO
dates** — never `6/28`), `branding`, and the vocabularies for whichever apps you
picked (`swag.options`, `roles.options`, `lodging.vocab`, `ticketing.tiers`).

- [ ] Apps chosen
- [ ] `event-profile.yaml` validates
- [ ] Check-in day keys are ISO dates
- [ ] `ticketing.tiers` carry env var *names*, no codes

## Phase 2 — Build the form (T-9 weeks)

1. Assemble from [`templates/`](templates/) — core plus the fragments matching
   your apps.
2. Replace every `{{PLACEHOLDER}}`.
3. Enable `webform_computed_twig` if you are using conditional ticketing.
4. Import via **Build → Source** ([02](02-templates.md#path-a--the-source-tab-recommended)).
5. Export the saved form back out and commit it.
6. `eventkit fieldmap check` against the profile.

- [ ] Form imported and saved
- [ ] Export committed next to the profile
- [ ] `eventkit fieldmap check` passes with no missing required fields
- [ ] Walked every `#states` branch as an anonymous user
- [ ] `uuid` holds a real UUID, not `[webform_submission:uuid]`
- [ ] Captcha enabled

## Phase 3 — Deploy the apps (T-8 weeks)

For each app: provision, then configure.

```sh
openssl rand -hex 32                     # a fresh token per app
az webapp config appsettings set -g <rg> -n <app> --settings \
  DRUPAL_WEBHOOK_TOKEN="…" \
  ALLOWED_ADMIN_PRINCIPALS="alice@example.edu,bob@example.edu" \
  ENABLE_RESTORE=False \
  ENABLE_DESTRUCTIVE_OPS=False
```

**Configure Easy Auth.** This is a manual portal step that provisioning does not
do, and every admin surface depends on it:

```sh
az webapp auth show -g <rg> -n <app> \
  --query "identityProviders.azureActiveDirectory.registration.clientId" -o tsv
```

Empty output means your admin routes are trusting a header nothing sets.

- [ ] Each app deployed and returning 200 or a 302 to login
- [ ] Easy Auth verified non-empty for every app
- [ ] Distinct webhook token per app
- [ ] Allow-lists set (empty denies everyone)
- [ ] Anonymous request to an admin route returns 401/403
- [ ] `GET /api/presenters` contains no `@` (if running `poster-gallery`)

## Phase 4 — Wire the handlers (T-8 weeks)

One Remote Post handler per app ([03](03-integration.md)).

- [ ] Handler per app, named after the app
- [ ] Token under a **`headers:`** key in Custom options
- [ ] Completed **and** Updated URLs set; Draft and Deleted empty
- [ ] Error message blank
- [ ] Test submission reaches every app
- [ ] `/api/webhook/status` shows `unmapped_keys: []`

## Phase 5 — Rehearse (T-7 weeks)

Do not skip this. It is the last cheap moment to find a problem.

Submit a test registration for each persona and confirm each app agrees:

| Persona | Expect |
|---|---|
| Anonymous attendee | tier B, exemption checkbox shown, `Pending` |
| Affiliate attendee | tier A, `Pending` |
| Speaker | no tier, no checkbox, `Exempt` |
| Student presenting a poster | appears in the gallery |
| Attendee requesting lodging | appears unassigned on the board |

Then rehearse the operations:

- [ ] Buy a real ticket; confirm it reconciles to `Paid`
- [ ] Refund it; confirm `Refunded`
- [ ] Create rooms, assign someone, trigger a rules warning deliberately
- [ ] Print one badge sheet on plain paper and hold it against real Avery stock
- [ ] Check someone in on each day key
- [ ] Take a backup and confirm the file is complete
- [ ] Generate a reimbursement link and open it

## Phase 6 — Open registration (T-6 weeks)

- [ ] Form set to Open
- [ ] Test submissions deleted from every app *and* from Drupal
- [ ] Someone owns the weekly `Pending` / `Unmatched` triage

## Phase 7 — During registration

**Weekly:** triage `Pending` and `Unmatched` in `ticket-reconciler`; check
`unmapped_keys` is still empty.

**T-3 weeks:** chase everyone still `Pending`. Create rooms and bulk-generate
room numbers.

**T-5 days:** freeze lodging. Rules panel clean, or every remaining warning
explicitly waived with a justification.

**T-1 day:** print badges. Calibrate on plain paper first. Print by role, and
print spares for walk-ins.

## Phase 8 — At the event

- Front desk runs `ticket-reconciler` check-in.
- Walk-ins: register them on the public form; they flow through to every app.
  A walk-in added directly to `lodging-planner` as a write-in does **not** reach
  `nametag-press` — that is the cost of independent databases. Register them
  properly, or hand-write the badge.
- On-site fee waivers get a recorded justification.

## Phase 9 — After (T+1 to T+30 days)

- [ ] Reimbursement and media-release links sent (`link-forge`)
- [ ] Refund overrides processed
- [ ] Backups taken from every app and stored off the platform
- [ ] Speaker prefill tokens regenerated — they are bearer credentials
- [ ] Webhook tokens rotated
- [ ] Sensitive lodging data deleted (gender identity, roommate requests)
- [ ] Apps torn down, **except** `poster-gallery` and `link-forge`, which stay up
- [ ] Form set to Closed, export committed

## Deferred-cost list

Things that are cheap now and expensive later:

| Now | Later |
|---|---|
| `uuid` join key | Re-keying four databases; duplicate people after any email correction |
| `'#unique': true` on email | Manual dedup across four databases |
| ISO check-in day keys | A data migration, and ambiguity between `7/1` and `07/01` |
| Opaque option keys | A data migration to change a label |
| Committing the form export | No history for a UI-built form; an accidental deletion is unrecoverable |
| One token per app | Rotating one secret four times, or not at all |

## Next

[07 — Troubleshooting](07-troubleshooting.md)
