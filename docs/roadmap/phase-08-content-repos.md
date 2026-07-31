# Phase 8 — `drupal-event-forms` and `event-stack`

The two content repositories, plus archiving the predecessors.

**Depends on:** all previous phases, because these document what they produced.
**Design detail:** [`PLAN.md`](PLAN.md) §H.

## Why last

Both are documentation of a working system. Writing them before the apps exist means
writing them twice. The exception is already done: the Drupal form-design guide lives
in this repository at [`../drupal/`](../drupal/), because it was needed to build the
next event regardless of extraction progress.

## `drupal-event-forms`

Versioned Drupal webform exports, email templates, and Remote Post recipes that feed
the apps. File tree in `PLAN.md` §H.1.

Much of the *guidance* already exists in [`../drupal/`](../drupal/). This phase adds
the **artefacts**: the actual sanitized YAML exports, the handler recipes, the receipt
email template, and the field-map contracts.

### Sanitization is enforced, not trusted

`tools/redact.py` in CI must reject: `X-Drupal-Webhook-Token` values (the three
predecessor recipe files carry truncated ones — replace with `${WEBHOOK_TOKEN}`),
`?token=` bearer strings, live `azurewebsites.net` hostnames, the Drupal
site-directory hash, the WAF bypass header name and value, and real attendee data in
the receipt sample.

Discount codes stay in `registration.yaml` because the Twig genuinely needs them —
flag in the README that they are **semi-public by nature**, since the browser
receives the computed value. Do not document them as secrets.

### The import path

`drush webform:import` imports **submissions, not definitions**. Document the two
real paths — the Webform UI **Source** tab (lead with this; it is what CAARMS used and
it cannot touch unrelated config) and `drush cim --partial` with the element body
wrapped in a config envelope and renamed `webform.webform.<id>.yml`.

Required contrib: Webform, `webform_ui`, CAS (only for `[cas:attribute:*]` prefill),
Captcha, and `webform_computed_twig` — which ships with the suite but is **not enabled
by default**, and whose absence makes conditional ticketing degrade silently so that
everyone reads as exempt.

### The field-map contract

`contracts/*.fieldmap.yml` is the normative artefact. Three enforcement layers:

1. `check_fieldmap_sync.py` asserts every contract key exists in the referenced
   webform YAML — catching "someone renamed an element in Drupal".
2. Each app's CI pulls the contract at a pinned tag and asserts superset
   compatibility.
3. At runtime `eventkit.drupal` logs `unmapped_keys` and the app exposes them at
   `/api/webhook/status`, so a live rename surfaces within one submission instead of
   as silently dropped data.

Versions are `YYYY.N`; a breaking rename bumps major and requires a PR touching both
repositories.

### Two bugs to fix and regression-assert

`tools/validate_yaml.py` must reject doubly-nested `#states`, and require that every
`#states` comparison value appears in the referenced element's `#options` keys.

1. **`registration.yaml`'s `actions`** has `'#states': {'#states': {disabled: …}}`.
   Drupal ignores the inner key, so the intended "disable submit for an un-logged-in
   affiliate" rule never fired — meaning affiliates could and did submit
   unauthenticated registrations that invalidated their own fee exemption, silently,
   for the whole event.
2. **The group router's `chair_notice`** compares against Eventbrite event id
   `1986024760521` while the actual radio option key is `1993012012580`, so the chair
   never saw the instruction telling them how many student tickets their purchase
   covered. Fix by matching the option key — better, key the radios on short slugs and
   compute URLs in Twig so the two cannot drift. That form uses **full URLs as option
   keys**, which is what made the bug possible.

Also reconcile a copy discrepancy: the form says 8 students + 1 faculty, while
`administrative-utilities.html` says the chair ticket unlocks **14** student spots.

### The travel form: publish with a warning

It collects passport number, country of issue, expiry, DOB and gender, and emails
them as plaintext HTML to an agency. `docs/PRIVACY.md` must say so plainly: webform
submissions persist in the database and in mail logs.

Ship it with `#results_disabled` guidance, mandatory purge-after-N-days, restricted
results access, and an explicit "prefer the travel agency's own secure portal" banner.
Publishing the *structure* is safe and useful; the repo must not imply the *pattern*
is safe.

## `event-stack`

The meta repository. File tree in `PLAN.md` §H.2.

Highest-value contents, in order:

**`CHOOSING-TOOLS.md`** — a question tree, and an explicit "**you do not need all
five**" at the top. Three named bundles: Minimal (`link-forge` only), Conference (all
five), Symposium (reconciler + nametag-press).

**`RUNBOOK.md`** — what staff actually did, with owners, timing, the exact route used,
and a "how you know it worked" check per phase. Registration opens at T-8 weeks →
`Pending` triaged weekly → rooms bulk-created at T-3 weeks, assignments frozen at
T-5 days → badges printed the day prior after a calibration print → check-in per day
key → post-event links, backups, teardown except `poster-gallery` and `link-forge`.

**`SECURITY-PRIVACY.md`** — a field → app → sensitivity → retention inventory.
Emails everywhere; gender identity and roommate requests (sensitive, delete at T+30);
dietary restrictions; passport data (**Drupal only, never in any app database**);
payment amounts (no card data ever touches these apps — Eventbrite holds it). Plus
token rotation, the fragment-vs-query rule, and: **a backup download is a full PII
export — treat it as one.**

Name the real cost of independent databases out loud: **a deletion request is a pass
over five databases.**

**`ARCHITECTURE.md`** — committed Mermaid. Three bands: Drupal on top with labelled
Remote Post arrows fanning out; the apps each over their **own** DB cylinder,
visually separate, on a shared `eventkit` bar, with `link-forge` having no cylinder;
externals at the right (Eventbrite bidirectional to `ticket-reconciler` only, DocuSign
dashed and link-only). Plus a T-8-weeks → T+2-weeks swimlane.

**`ADR/`** — the decisions that will otherwise be re-litigated: independent databases,
SQLite default, polling-first realtime, one parser in eventkit, `link-forge` stateless,
single PDF renderer, Alembic over hand-rolled, no bundler.

**Also document the single-container all-in-one compose deployment** as a first-class
option. Five Azure Web Apps for a one-week event is real money and five things to
patch. The per-app repos are right; per-app *hosting* should not be mandatory.

## Archive the predecessors

Last step of the whole extraction. GitHub **archive**, not delete, with READMEs
pointing at the new repositories. The `pre-extraction` tags already exist
(`ticketed` → `11f0a10`, `posted` → `ea5b3da`).

Before archiving, resolve the outstanding `posted` `main` rollback question in
[`STATUS.md`](STATUS.md) — two `big-agenda` commits passed CI and are not on `main`.
Archiving makes that harder to recover.

## Acceptance criteria

- [ ] `tools/redact.py` and `tools/validate_yaml.py` both run in CI and both reject a
      deliberately-planted violation.
- [ ] Both documented Drupal bugs fixed, with regression assertions.
- [ ] A field-map contract exists per app, and each app's CI checks against it.
- [ ] `CHOOSING-TOOLS.md` leads with "you do not need all five".
- [ ] `SECURITY-PRIVACY.md` retention table complete, including passport data.
- [ ] `provision-event.sh` takes an empty subscription to a working stack, and
      `verify-stack.sh` passes against it.
- [ ] Predecessors archived with pointer READMEs.

## Risks

**Licensing differs by repo.** MIT for code; **CC-BY-4.0 for `drupal-event-forms`**,
which is content and configuration that adopters will fork and edit; CC-BY-4.0 for
`event-stack` docs with MIT for its scripts.

**The `CONTRIBUTING.md` copy problem.** One canonical file lives in `event-stack` and
is **copied** into each repo — GitHub only renders the local file — with a CI diff
check catching drift. It must carry the hard rule that no PR may add a real attendee
record, webhook token, speaker prefill token, or discount code to a fixture.

**Publishing the travel form is a judgement call** and the warning is the whole
mitigation. If in doubt, ship the field list and not the working form.
