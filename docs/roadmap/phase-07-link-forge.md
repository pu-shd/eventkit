# Phase 7 — `link-forge`

Prefilled per-person links: reimbursement forms, the DocuSign media-release
PowerForm, slide upload, and tokenised speaker webform prefill.

**Depends on:** Phase 1 (`auth`, `eventprofile`, `ui`), Phase 2.
**Design detail:** [`PLAN.md`](PLAN.md) §G.4.

## The smallest phase

Two to three days. The entire current feature is **one line** —
`posted/frontend/admin_reimbursement.html:289` — a URL template with the event name
and travel dates baked into a fragment.

## Decision: its own repo, stateless, no database

`PLAN.md` flag F5 questioned whether this needs to be an app at all. It does, and it
should be its own thing:

- **Not a route inside `ticket-reconciler`.** The audience is event and finance staff,
  who should not hold an authorization that also exposes payment amounts and gross
  revenue.
- **It outlives the reconciler.** Reimbursement links get used for weeks after the
  event, when the reconciler has been torn down.
- **It has no schema**, so bolting it onto a migration-bearing app taxes it forever.
- **Not a bare static page either**, because the tokenised speaker links are bearer
  credentials that must not be baked into a committed HTML file — the leaked
  `links-for-speakers.html` is precisely that mistake — and staff need auth in front
  of the roster.

No models. `SPEAKER_LINK_TOKENS` is a JSON env var or Key Vault secret mapping
lowercased email → token, loaded into memory at startup.

Roster comes from one of: a proxied call to `ticket-reconciler`, a pasted CSV, or an
uploaded CSV held **in memory only**.

## Fragment vs query — enforced, not just documented

This is the substance of the phase.

`#`-fragment parameters are **never sent to a server**: not in access logs, not in
`Referer`, not in a CDN. `?`-query parameters **are** sent, and land in Drupal's
webserver log, App Service logs, and any proxy in between.

So each link kind declares `param_style: fragment|query` and
`sensitivity: low|pii|bearer`, and the app:

- **refuses** to render a `bearer` kind with `param_style: fragment` — a fragment
  defeats prefill anyway;
- shows a warning banner on any `query` + `pii` kind. The slide-upload link puts a
  participant's email into log lines today;
- logs only `kind` plus a SHA-256 prefix of the email — never a rendered URL.

Recommend to the Drupal side that slide-upload prefill move to POST-then-redirect or
a short-lived opaque token.

## What to delete

The CAARMS dates and business purpose in `admin_reimbursement.html:289`, the
`orfe.princeton.edu` host, the `caarms.princeton.edu` host in token links, and the
committed token values.

Also the **fourth** copy of the Princeton affiliation-normalisation rule
(`admin_reimbursement.html:230-238`). It exists in six places across the two
predecessor repos; it belongs in `profile.affiliation.domain_map`.

## Tests

- Templating substitutes and URL-encodes name, email and dates.
- An unknown placeholder **raises** rather than silently blanking.
- `sensitivity: bearer` with no token for that email → 404, never an unsigned link.
- A `bearer` + `fragment` combination is rejected at load.
- CSV paste tolerates `"Last, First"`, quoted commas, and a BOM.
- Roster proxy failure degrades to paste mode.
- `test_no_pii_logs.py`: **no `@` reaches `caplog`** under any code path.
- vitest: clipboard success plus the `document.execCommand` fallback for non-TLS
  origins; per-kind card visibility by role; paste parser parity with the Python
  fixture.

## Acceptance criteria

- [ ] No database, no `migrations/`, no Alembic.
- [ ] Every link kind declares `param_style` and `sensitivity`.
- [ ] `bearer` + `fragment` refused; `query` + `pii` warns.
- [ ] No rendered URL and no email address in any log line.
- [ ] Speaker tokens loaded from a secret, never committed.
- [ ] Deployable and usable with `ticket-reconciler` torn down.

## Risks

**The speaker tokens are the sensitive part.** Nine live ones leaked through a saved
HTML page during CAARMS 2026. They are bearer credentials: whoever holds the URL can
edit that speaker's submission. Regenerate after every event, keep them in a secret
store, and never render them into a page that could be saved or shared.

**This app is the one most likely to be handed to non-technical staff**, because
"copy a link" is its whole interface. The warning banners are not decoration — they
are how a finance admin learns that a link they are about to email puts an address in
a log.
