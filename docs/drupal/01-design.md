# 01 — Designing the registration form

What to put in the webform, and the handful of choices that are expensive to
change once registration opens.

## Prerequisites

The full Drupal 10 Webform module suite. Specifically you need:

| Module | Why |
|---|---|
| `webform` | Everything. `webform_name`, `webform_select_other`, `webform_email_confirm`, and the Remote Post handler all ship with it. |
| `webform_ui` | The browser form builder and, more importantly, the **Source** tab used to paste YAML. |
| `captcha` (+ `recaptcha`) | Open registration forms attract bots. |
| `cas` | Only if you want single sign-on prefill (`[cas:attribute:*]`). Everything works without it; you lose prefill and the "are you logged in" branch. |

`webform_computed_twig` is part of the Webform suite but is **not enabled by
default**. You need it for conditional ticketing. Enable it before importing a
form that uses it, or the element silently degrades.

## The six decisions that are expensive later

### 1. The join key is the one thing you cannot retrofit

Each app stores its own copy of the registrant. eventkit derives a `person_key`
to correlate them, preferring the Drupal submission **UUID** and falling back to
a hash of the normalised email address.

Prefer the UUID, because email changes. Someone registers as
`a.smith@example.edu`, then asks you to correct it to `asmith@example.edu`. With
an email-derived key that is a *new person* in every database, and the original
rows linger as orphans. With a UUID it is the same person everywhere.

Two ways the UUID can reach the app:

- **As a Remote Post submission property.** The handler posts submission
  metadata alongside the form data.
- **As an explicit `#type: value` element** with `[webform_submission:uuid]`.

The templates here declare it explicitly, because the real CAARMS export turned
out to define **no** `uuid`, `sid` or `serial` elements at all — those values
were arriving only as Remote Post properties. Declaring them costs nothing and
removes the ambiguity.

> **Verify this on your first test submission.** A misconfigured token posts its
> own text — the literal string `[webform_submission:uuid]` — rather than a
> value. Every registrant would then share an identical `person_key` and the
> whole roster would collapse onto one row. eventkit detects bracketed values and
> falls back to the email hash, but you want to know that happened.

### 2. Email uniqueness

Set `'#unique': true` on the email element with a helpful `#unique_error`. This
is the only thing standing between you and duplicate people. It is far cheaper
than deduplicating four databases later.

### 3. Option keys are contracts

For `attendee_status`, `t_shirt_size`, `roommate_preference` and friends, the
option **keys** must match the event profile exactly. Labels are free text.

```yaml
'#options':
  USML: 'Unisex — Small'      # USML is the contract, the label is copy
```

Choosing opaque keys (`USML`) over readable ones (`Small`) looks unfriendly but
means you can restyle every label without a data migration.

### 4. Which fields are published

`poster-gallery` serves `poster_title`, `faculty_adviser_name` and
`poster_presentation_abstract` on an **unauthenticated** page and in an RSS
feed. Email addresses are never published. Say so on the form — people write
differently when they know an abstract is public.

### 5. Which fields are sensitive

`gender_identity` and `identified_roommate` are the most sensitive data the
stack holds. Collect them only if you are actually assigning rooms, keep the
lodging app's allow-list short, and set a deletion date.

### 6. Fieldsets are free

Group related elements in a `fieldset` or `details`. Drupal nests them in the
export, but **submission data is flat** — a fieldset is presentational and its
children post as top-level keys. eventkit flattens containers when reading a
schema, so nesting costs you nothing and makes a long form readable.

## Element vocabulary

### Composites eventkit understands

Drupal composites arrive as nested objects, not strings. eventkit normalises
all of these:

| Element type | Arrives as | eventkit reads it with |
|---|---|---|
| `webform_name` | `{first, last, middle, …}` | `coerce_name` — falls back to splitting a plain string on the first space |
| `webform_email_confirm` | `{mail_1, mail_2}` | `coerce_email` — takes `mail_1` |
| `webform_select_other` | `{select, other}` | `coerce_select_other` — returns `other` when `select` is `_other_` |
| `checkbox` | `"1"`, `"0"`, `true`, `"Yes"`, `""` | `coerce_bool` — truthy set is `1 true yes on checked y t` |
| `checkboxes` | list or `{key: bool}` | `coerce_multivalue` |

You do not have to use composites. A plain `textfield` named `registrant_name`
works — eventkit splits `"Ada Lovelace"` into first and last on the first space.
Composites are better because they survive `"Ursula K. Le Guin"`.

### Identity plumbing

```yaml
uuid:
  '#type': value
  '#default_value': '[webform_submission:uuid]'
sid:
  '#type': value
  '#default_value': '[webform_submission:sid]'
serial:
  '#type': value
  '#default_value': '[webform_submission:serial]'
user_authentication_check:
  '#type': hidden
  '#default_value': '[current-user:uid]'
  '#prepopulate': true
```

`sid` is the internal submission id — apps use it to deep-link back to the
submission in Drupal. `serial` is the human-facing number staff quote at the
front desk. `user_authentication_check` is empty for anonymous users and drives
every "are you logged in?" condition.

## Conditional logic with `#states`

`#states` shows, hides, requires or disables an element based on another's
value.

```yaml
poster_presentation_details:
  '#type': fieldset
  '#states':
    visible:
      ':input[name="presenting_poster"]':
        value: 'Yes'
    required:
      ':input[name="presenting_poster"]':
        value: 'Yes'
```

Multiple conditions with `or`:

```yaml
  '#states':
    visible:
      - ':input[name="attendee_status"]':
          value: Speaker
      - or
      - ':input[name="attendee_status"]':
          value: Organizer
```

Negation uses `'!value'`, and pattern matching uses `pattern` / `'!pattern'`:

```yaml
  '#states':
    visible:
      ':input[name="email"]':
        '!pattern': '@example\.edu$'
```

### Three `#states` traps

**Never nest `#states` inside `#states`.** The CAARMS form contained:

```yaml
actions:
  '#states':
    '#states':          # ← wrong
      disabled: ...
```

Drupal does not recognise the inner key, so the rule never fired. The submit
button was never disabled and affiliates submitted un-authenticated
registrations that invalidated their own fee exemption — silently, for the
entire event.

**A hidden element submits nothing.** If `tickets_sold_separately` is hidden by
`#states`, it is absent from the payload — not `false`. Decide what absence
means and make sure the app agrees. eventkit treats an absent exemption checkbox
as "exempt".

**`#states` conditions compare against option keys.** A companion form used a
full Eventbrite URL as a radio option key, then wrote a condition against a URL
containing a *different* event id. The condition never matched and the notice
never displayed. Use short slugs as keys.

## Anti-spam

Open registration forms get link spam in every free-text field. The pattern used
throughout the templates:

```yaml
'#pattern': '^((?!http:\/\/|https:\/\/|www\.|<a\b).)*$'
'#pattern_error': 'Please enter a name, not a web address.'
```

Plus a `captcha` element before `actions`.

## Prefill and tokenised links

Two different mechanisms, often confused:

**Prepopulate from a query string** — `'#prepopulate': true` lets
`?destination_url=GROUPRATE` set a value. Anything arriving this way is
attacker-controlled; only use it for values you are willing to let the holder of
the link choose.

**Tokenised submission links** — Drupal can issue a per-submission edit link
(`?token=…`) that lets a named person edit a submission you started for them.
Used for speakers, who get a short pre-filled form rather than the full one.

> These tokens are **bearer credentials**. Anyone holding the URL can edit that
> submission. Nine live ones leaked through a saved HTML page during CAARMS 2026.
> Do not paste them into documents, and regenerate them after the event.

## Next

[02 — Templates and importing](02-templates.md)
