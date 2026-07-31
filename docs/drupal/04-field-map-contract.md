# 04 — The field-map contract

How a Drupal element key becomes a value an application can read, and how to
keep the two in sync when someone renames something.

## The problem this solves

Applications refer to **logical field names** — `email`, `attendee_status`,
`t_shirt_size`. Your Drupal form has **element keys**, which may or may not
match. The field map is the translation, and it lives in the event profile.

The predecessor did this badly and it is worth knowing why. `ticketed` embedded
a 55-line copy of the CAARMS webform in `schema_parser.py` and looked for an
override file at two implicit paths. No override ever shipped in the image, so
**the CAARMS field map always won** — silently, for every adopter, against their
own differently-named form. The failure mode was not an error. It was empty
columns.

eventkit fails fast instead: no embedded default, and a missing required field
raises at startup with a copy-pasteable stub.

## Resolution order

1. **`drupal.field_map` in the event profile** — explicit, authoritative.
2. **`drupal.webform_schema`** — a path to your exported webform YAML. eventkit
   infers a map and logs a warning for every non-exact match.
3. **Neither** — startup error naming the missing logical fields.

Inference is a convenience for getting started. Pin the map explicitly before
you go live.

## Writing the map

```yaml
drupal:
  join_key: uuid
  webform_schema: ./webform-schema.yml
  field_map:
    fields:
      email:            { key: [email, confirm_email_address], kind: email, required: true }
      name:             { key: registrant_name, kind: name, required: true }
      uuid:             { key: uuid, kind: text }
      sid:              { key: sid, kind: int }
      serial:           { key: serial, kind: int }
      attendee_status:  { key: attendee_status, kind: select }
      t_shirt_size:     { key: t_shirt_size, kind: select }
      lodging:          { key: lodging, kind: bool }
      gender_identity:  { key: gender_identity, kind: select_other }
```

`key` accepts a list, tried in order — useful when a field was renamed
mid-event and both spellings exist in the data.

### Kinds

| `kind` | Coercion |
|---|---|
| `text` | Trimmed string |
| `email` | Trimmed, lowercased; unwraps `webform_email_confirm` composites |
| `name` | Composite → `(first, last)`; a plain string splits on the first space |
| `bool` | `1 true yes on checked y t` → `True` |
| `int` | `""` → `None`, `"12"` → `12` |
| `select` | String; option key preserved verbatim |
| `select_other` | `{select, other}` → the `other` value when `select` is `_other_` |
| `multiselect` | dict/list/string → list |
| `url` | Trimmed string |

Get `kind` wrong and you get a wrong value rather than an error — a `bool` field
mapped as `text` yields the string `"0"`, which is truthy.

## Checking it

```sh
eventkit fieldmap check path/to/event-profile.yaml
```

Real output:

```
OK  19 logical field(s) mapped
  attendee_status                      <- attendee_status  (select)
  destination_url                      <- destination_url  (url)
  email                                <- email, confirm_email_address  (email required)
  gender_identity                      <- gender_identity  (select_other)
  name                                 <- registrant_name  (name required)
  serial                               <- serial  (int)
  sid                                  <- sid  (int)
  t_shirt_size                         <- t_shirt_size  (select)
  uuid                                 <- uuid  (text)
  …
```

And validate the profile as a whole:

```sh
eventkit profile validate path/to/event-profile.yaml
# OK  CAARMS 2026  slug=caarms-2026  theme=princeton-orfe  checkin_days=5
```

Both belong in CI.

## Fieldsets are flattened

Drupal nests fieldset children in the export; submission data is flat. eventkit
flattens containers when reading a schema, so this:

```yaml
lodging_section:
  '#type': fieldset
  gender_identity:
    '#type': webform_select_other
```

makes `gender_identity` a top-level element as far as the field map is
concerned. Nest freely for readability.

> This was a real defect, found while writing these docs. Before it was fixed,
> eventkit read only top-level elements — so against the actual CAARMS export it
> could not see `gender_identity`, `roommate_preference`, `identified_roommate`,
> `poster_title`, `faculty_adviser_name` or `poster_presentation_abstract`. Six
> fields, the whole lodging and poster halves of the form, reported as
> "could not infer an element". The shipped example schema had been hand-flattened,
> which hid it. `TestNestedContainers` now pins the behaviour.

## Keeping form and profile in sync

The failure this prevents: someone renames an element in Drupal, the field map
still names the old key, and that field silently becomes empty for every
subsequent registration. Nothing errors.

Three layers, cheapest first:

**1. Commit the export.** After every form change, export the webform YAML into
the repo next to the profile ([02](02-templates.md#versioning-your-form)).

**2. Check in CI.** With both files committed, assert every mapped key exists:

```sh
eventkit fieldmap check event-profile.yaml
```

Fails if the profile names an element the schema does not have.

**3. Watch `unmapped_keys` at runtime.** Every webhook logs the element keys it
received but has no mapping for, and `/api/webhook/status` surfaces them:

```sh
curl -s https://<app>.azurewebsites.net/api/webhook/status \
  -H "X-Drupal-Webhook-Token: $TOKEN" | jq .unmapped_keys
```

A rename shows up here within one submission. Layers 1 and 2 catch it before
deploy; layer 3 catches it when someone edits the form in production without
touching the repo, which is how it will actually happen.

## Adding a field mid-event

Safe, in this order:

1. Add the element to the webform. Nothing consumes it yet; it appears in
   `unmapped_keys`.
2. Add it to the profile's `field_map`.
3. Add the column to the app and deploy.
4. Backfill earlier submissions if you need them
   ([03](03-integration.md#backfilling-submissions-made-before-the-handler-existed)).

Do not do it in the other order. A profile naming an element that does not exist
fails validation at startup, and if the field is marked `required` the app will
not boot.

## Next

[05 — Conditional ticketing](05-conditional-ticketing.md)
