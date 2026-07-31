# Drupal forms → eventkit apps

How to design the registration webform for your next event, generate it from the
YAML templates here, and wire it to a deployed set of eventkit applications.

Written from the CAARMS 2026 build. Everything here has been checked against the
real Drupal export and against eventkit's own parser; where the two disagreed,
the disagreement is documented rather than smoothed over.

## Read in this order

| | Document | Read it when |
|---|---|---|
| 01 | [Designing the form](01-design.md) | Before you create anything. The element vocabulary, composites, `#states`, and the six decisions that are expensive to change later. |
| 02 | [Templates and importing](02-templates.md) | You are ready to build the form. Includes the two real import paths — one of which is not the one you will find by searching. |
| 03 | [Wiring to deployed apps](03-integration.md) | The form exists and you need submissions to reach the apps. Remote Post handlers, tokens, and the header gotcha that costs everyone an afternoon. |
| 04 | [The field-map contract](04-field-map-contract.md) | Your element keys differ from the defaults, or someone renamed a field and data stopped arriving. |
| 05 | [Conditional ticketing](05-conditional-ticketing.md) | Different people pay different amounts, or some pay nothing. |
| 06 | [New-event runbook](06-runbook.md) | Doing it for real. An ordered checklist from empty Drupal to first live registration. |
| 07 | [Troubleshooting](07-troubleshooting.md) | Something is not arriving, or arriving wrong. |

Templates live in [`templates/`](templates/).

## The mental model

One Drupal webform is the **only** place a registrant types anything. Each
eventkit application subscribes to that form independently:

```
        Drupal registration webform
                    │
    ┌───────┬───────┼───────┬────────┐
    │       │       │       │        │   one Remote Post handler each,
    ▼       ▼       ▼       ▼        ▼   one token each, own database each
 ticket-  lodging- nametag- poster-  link-
 recon…   planner  press    gallery  forge
```

Three consequences worth internalising before you design anything:

**Apps do not talk to each other.** Each keeps its own copy of the registrant.
That is deliberate — one app going down cannot take registration with it, and an
adopter can run only the tools they need. The cost is that the same person is a
row in several databases, which is why the join key matters (see
[01](01-design.md#the-join-key-is-the-one-thing-you-cannot-retrofit)).

**The form is the schema.** There is no admin UI for adding a field to an app.
You add an element to the webform, name it in the event profile's field map, and
the app picks it up. An element eventkit does not know about is ignored, not an
error — which is convenient and is also how fields silently go missing.

**Nothing event-specific belongs in application code.** Dates, ticket tiers,
t-shirt sizes, role labels, lodging vocabularies and branding all live in one
`event-profile.yaml`. For a new event you write a new profile and a new webform;
you do not fork an app.

## Two things to get right on day one

**Set up Easy Auth before you announce anything.** Every admin surface in the
stack is gated on the `X-MS-CLIENT-PRINCIPAL-NAME` header that Azure Easy Auth
injects. The provisioning scripts do not configure the identity provider — it is
a manual portal step. Verify it:

```sh
az webapp auth show -g <rg> -n <app> \
  --query "identityProviders.azureActiveDirectory.registration.clientId" -o tsv
```

An empty result means your admin routes are trusting a header that nothing is
setting.

**Generate real webhook tokens.** `openssl rand -hex 32`, one per app, none
reused. The apps refuse to start on a known placeholder value, and refuse to
start with no value at all.

## Conventions

- `{{PLACEHOLDER}}` in a template is yours to replace.
- Element **keys** are contracts; element **titles** are copy. Change titles
  freely, change keys deliberately.
- Option **keys** must match the event profile; option **labels** are copy.
