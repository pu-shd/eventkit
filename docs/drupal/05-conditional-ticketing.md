# 05 — Conditional ticketing

Different people paying different amounts, some paying nothing, and the
reconciliation that tells you who actually paid.

This is the most intricate part of the stack. It is also the part that was most
tangled in the predecessor, so the design here is deliberately narrower than
what CAARMS 2026 actually ran.

## How it works

The webform does **not** send anyone to a payment page. It computes a **tier
slug** and stores it. The tier slug is a key into the event profile, which maps
it to the *name* of an environment variable holding the discount code.

```
webform computes  →  destination_url = "pu-affiliate"
                            │
event profile     →  tiers[key=pu-affiliate].discount_code_env
                     = "EVENTBRITE_DISCOUNT_PU_AFFILIATE"
                            │
App Service       →  EVENTBRITE_DISCOUNT_PU_AFFILIATE = "REDACTED"
                            │
ticket-reconciler →  builds the Eventbrite URL with that code
```

Three separations, each load-bearing:

- **The form knows tiers, not prices.** Prices live in Eventbrite. The
  predecessor kept a price table in Twig and it drifted from the real prices.
- **The profile knows variable names, not codes.** The profile is committed and
  is served to the browser at `GET /api/event-profile`. A pasted code would be
  public. `TicketTier.discount_code_env` rejects anything that is not upper
  snake case — a real code like `2030EXAMPLEGA` is refused.
- **Only App Service knows codes.** Not git, not the profile, not the form.

> Discount codes are **semi-public by nature** — the Twig that computes the tier
> is delivered to the browser, so a determined registrant can read the tier and
> a code embedded in a purchase URL. Keeping them out of git is still right.
> Do not document them as secrets, and do not rely on them for access control.

## The exemption switch

`tickets_sold_separately` is a required checkbox shown only to people who owe a
ticket. Speakers and organizers never see it.

Because a `#states`-hidden element **is not submitted at all**, absence is the
signal. eventkit treats an absent or unchecked value as *exempt*. This is the
`exempt_means: unchecked_is_exempt` setting in the profile — invert it if your
form is phrased the other way round.

## Writing the tier computation

From `templates/fragment.ticketing.yaml`:

```yaml
destination_url:
  '#type': webform_computed_twig
  '#title': 'Ticket tier'
  '#store': true
  '#ajax': true
  '#prepopulate': true
  '#template': |
    {% if data.destination_url %}
      {{ data.destination_url }}
    {% elseif data.attendee_status == 'Speaker' or data.attendee_status == 'Organizer' %}
      {# Exempt: emit nothing. #}
    {% elseif data.user_authentication_check %}
      {{TIER_A_SLUG}}
    {% else %}
      {{TIER_B_SLUG}}
    {% endif %}
```

Read it as a cascade, first match wins:

1. **A pinned tier from the URL.** `?destination_url=GROUPRATE` lets a group
   organizer circulate a link at a negotiated rate. Attacker-controlled — only
   hand these to people you mean to give that price.
2. **The exclusion gate.** Speakers and organizers emit nothing. Put this
   *before* the rest, or a logged-in speaker falls through into an affiliate
   tier and gets asked to pay.
3. **Everything else**, most specific first.

`'#store': true` persists the value so `ticket-reconciler` can read it back.
`'#ajax': true` recomputes as answers change. Both are required.

### Enable the module

`webform_computed_twig` ships with the Webform suite but is **not enabled by
default**. Without it the element degrades silently and every registrant gets an
empty tier — meaning everyone reads as exempt. Check before you open
registration.

## Matching tiers in the profile

```yaml
ticketing:
  vendor: eventbrite
  slug: example-2030
  exempt_field: tickets_sold_separately
  exempt_means: unchecked_is_exempt
  prefer_destination_url_discount: true
  event_url_template: "https://www.eventbrite.com/e/{slug}-tickets-{event_id}"
  tiers:
    - key: pu-affiliate
      label: "Affiliate rate"
      discount_code_env: EVENTBRITE_DISCOUNT_PU_AFFILIATE
      match: { email_domain_suffix: ["example.edu"] }
    - key: general
      label: "General admission"
      discount_code_env: EVENTBRITE_DISCOUNT_GENERAL
      match: { default: true }
```

`match` is a fallback for when the form did not compute a tier — by email domain
or by another field's value. Exactly one tier should carry `default: true`.

Two places can decide a tier: the Twig in the form, and `match` in the profile.
Prefer the form, and keep `match` as a safety net. Two sources of truth that
disagree is a bug you will debug at the front desk.

## Reconciliation

`ticket-reconciler` pulls the Eventbrite attendee list and joins it to
registrations on lowercased email, producing one of:

| Status | Meaning |
|---|---|
| `Paid` | Registered and a matching paid Eventbrite order |
| `Complete` | Paid and checked in |
| `Pending` | Registered, owes a ticket, no matching order |
| `Exempt` | Registered, owes nothing (speaker, organizer) |
| `Waived` | An admin waived the fee, with a recorded justification |
| `Refunded` / `Cancelled` | Order reversed |
| `Unmatched` | A paid Eventbrite order with no matching registration |

`Unmatched` is the one to watch. It usually means someone bought a ticket with a
different address than they registered with. Staff link the two manually, and a
manual link wins over an email match — a payment claimed by one registrant will
not also be matched to another.

Triage `Pending` and `Unmatched` weekly during registration, not the week of the
event.

## What CAARMS 2026 actually ran, and why this is narrower

The real form computed **five** slugs from a cascade over login state, student
status, attendee status, poster status and email domain, plus three
group-organizer codes arriving by query string, plus a Twig price table of seven
entries. It worked, and it was very hard to reason about.

Known problems from that build, all avoided here:

- **The price table drifted from Eventbrite.** Two of seven entries were for
  slugs the cascade could no longer produce; two producible slugs had no price.
- **Codes were string literals in application source** (`main.py:499-511`),
  along with a hardcoded email-domain branch and the event slug.
- **A companion group form used full Eventbrite URLs as radio option keys**, then
  wrote a `#states` condition against a URL containing a *different* event id.
  The condition never matched, so the chair never saw the instruction telling
  them how many student tickets their purchase covered.

If you need group rates, the pattern that works: give organizers a
`?destination_url=<slug>` link, add the tier to the profile, and keep the slug
short and opaque. Do not put URLs in option keys.

## Testing before you open

Walk every branch as an anonymous user in a private window, and confirm the
stored `destination_url` on each test submission:

| Registrant | Expected tier |
|---|---|
| Anonymous, attendee | `{{TIER_B_SLUG}}` |
| Logged in, attendee | `{{TIER_A_SLUG}}` |
| Speaker | *(empty)* |
| Organizer | *(empty)* |
| Anonymous with `?destination_url=GROUPRATE` | `GROUPRATE` |

Then confirm the exemption checkbox appears for attendees and not for speakers,
and that `ticket-reconciler` shows each test registration in the expected state.

## Next

[06 — New-event runbook](06-runbook.md)
