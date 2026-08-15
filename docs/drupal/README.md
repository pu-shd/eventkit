# Drupal → eventkit

How submissions get from a Drupal webform into an application.

Form design, importing, conditional ticketing and the sanitized exports live in
**[drupal-event-forms](https://github.com/pu-shd/drupal-event-forms)**. This page
covers only eventkit's side: how a payload is parsed and how fields are mapped.

## The flow

```
Drupal webform ──Remote Post──> POST /api/drupal-webhook
                                  │
                                  ├─ verify token   (compare_digest)
                                  ├─ parse_submission(payload, field_map)
                                  ├─ person_key(uuid, email)
                                  └─ upsert, return 200 in ~200ms
```

Every application does exactly this. They share one parser, so a submission
means the same thing everywhere.

## Field maps

A field map ties webform element keys to the logical names an application uses.
Declare it in the event profile:

```yaml
drupal:
  join_key: uuid
  field_map:
    fields:
      email:         { key: [email, confirm_email_address], kind: email, required: true }
      name:          { key: registrant_name, kind: name, required: true }
      student:       { key: student, kind: bool }
      lodging:       { key: lodging, kind: bool }
      gender_identity: { key: gender_identity, kind: select_other }
```

Resolution order, logged once at startup:

1. `profile.drupal.field_map`
2. `profile.drupal.webform_schema` — a path to a Drupal export, inferred from
3. neither → **the application refuses to start**, naming the missing fields

There is no built-in default. A wrong field map silently drops registrations,
which is worse than not booting.

Check one without deploying:

```sh
eventkit fieldmap check event-profile.yaml
```

## Kinds

| `kind` | Accepts | Produces |
|---|---|---|
| `email` | string, `{mail_1, mail_2}`, list | lowercased string |
| `name` | `{first, last}`, `"Ada Lovelace"` | first + last |
| `bool` | `1 true yes on checked y t` | bool |
| `int` | `"12"`, `""` | int or None |
| `select` | string | string |
| `select_other` | `{select, other}` | the other value when select is `_other_` |
| `multiselect` | dict, list, string | list |
| `url`, `text` | string | string |

All coercions are total — junk yields `None`, never an exception.

## Identity

`person_key` prefers the Drupal submission `uuid` over a hash of the email, so
a corrected address does not orphan someone's rows.

**Verify `uuid` arrives on your first test submission.** It is a submission
property, not an element, and it is absent from some exports. Check
`GET /api/webhook/status`, which reports counters and `unmapped_keys` and no
attendee data.

## Templates

[`templates/`](templates/) holds a core registration form plus optional
fragments (poster, lodging, swag, ticketing). Concatenate what you need:

```sh
cat templates/registration.core.yaml templates/fragment.*.yaml > registration.yaml
```

They are verified against eventkit's own parser in CI, so they cannot drift from
the code that reads them.
