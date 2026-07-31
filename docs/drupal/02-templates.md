# 02 — Templates and importing

Building the form from the YAML in [`templates/`](templates/), and the two ways
to get it into Drupal.

## What is here

| File | Contents |
|---|---|
| `registration.core.yaml` | Identity plumbing, name, email + confirm, institution, role, submit. The minimum every app can consume. |
| `fragment.swag.yaml` | T-shirt size. `ticket-reconciler` only. |
| `fragment.lodging.yaml` | Lodging question plus the sensitive room-preference fieldset. `lodging-planner`. |
| `fragment.poster.yaml` | Poster question plus title/adviser/abstract. `poster-gallery`. |
| `fragment.ticketing.yaml` | Exemption checkbox and the `destination_url` tier computation. `ticket-reconciler`. |
| `registration.full.yaml` | All of the above concatenated. A complete conference form. |

Fragments are plain concatenation — there is no include mechanism in Drupal
webform YAML:

```sh
cat templates/registration.core.yaml \
    templates/fragment.swag.yaml \
    templates/fragment.poster.yaml \
    > my-event-registration.yaml
```

Take only the fragments whose apps you are deploying. A `lodging` field with no
`lodging-planner` behind it just collects data nobody reads.

## Replace the placeholders

```sh
grep -o '{{[A-Z_]*}}' my-event-registration.yaml | sort -u
```

```
{{CONTACT_EMAIL}}     {{ELIGIBILITY_NOTE}}  {{EVENT_NAME}}
{{HOTEL_NOTE}}        {{LOGIN_PATH}}        {{SWAG_DEADLINE}}
{{TICKET_NOTE}}       {{TIER_A_SLUG}}       {{TIER_B_SLUG}}
```

Check none survive before importing:

```sh
! grep -q '{{' my-event-registration.yaml && echo "OK: no placeholders left"
```

## Validate before importing

Check the form against eventkit's parser first. This catches malformed YAML and,
more usefully, tells you which logical fields your apps will and will not find:

```sh
docker run --rm -v "$PWD:/w" -w /w \
  ghcr.io/pu-shd/eventkit:latest \
  python -c "
from eventkit.drupal import WebformSchema
s = WebformSchema.from_path('my-event-registration.yaml')
fm, warnings = s.infer_field_map(want=['email','name','uuid','attendee_status'])
print('elements:', len(s.elements))
print('mapped:', sorted(fm.fields))
for w in warnings: print('WARN:', w)
"
```

Every heuristic match produces a warning. Inference never guesses silently — but
inference is a convenience, not the contract. Pin the mapping explicitly in your
event profile once you are happy with it. See
[04 — The field-map contract](04-field-map-contract.md).

## Importing into Drupal

There are two real paths. The one you will find by searching is wrong.

> **`drush webform:import` does not do this.** It imports *submissions*, not
> definitions. Running it against a form definition will not do what you want.

### Path A — the Source tab (recommended)

This is what CAARMS actually used, and what to lead with unless you have a
reason not to.

1. **Structure → Webforms → Add webform.** Give it a title and note the machine
   name (`registration`).
2. Open the new form → **Build** tab → **Source** sub-tab.
3. Paste the entire element body, replacing what is there.
4. **Save**.

Drupal validates the YAML on save and reports the offending line if it is
malformed. The elements files here are element-only bodies, which is exactly
what the Source tab expects — no envelope, no wrapping.

Why lead with this: it works regardless of Drupal or Webform module version, it
cannot touch unrelated configuration, and the failure mode is an error message
rather than a surprise.

### Path B — configuration import

For sites that manage configuration in git.

1. Wrap the element body in a config envelope and name the file
   `webform.webform.<machine_name>.yml`:

   ```yaml
   langcode: en
   status: open
   dependencies: {  }
   open: null
   close: null
   weight: 0
   uid: 1
   template: false
   archive: false
   id: registration
   title: 'Registration'
   description: ''
   categories: {  }
   elements: |
     # ← the entire element body, indented two spaces
   ```

   Note `elements:` is a **literal block string**, not a mapping. The whole
   element YAML is indented underneath it.

2. `drush cim --partial --source=path/to/config/`

**Two cautions.** `drush cim` requires your Drupal core and Webform module
versions to match those the config was exported from, or it refuses. And
`--partial` still overwrites any config object present in the source directory —
keep that directory to exactly the webforms you mean to import.

## After importing

- **Enable `webform_computed_twig`** if you used `fragment.ticketing.yaml`.
  Without it the `destination_url` element degrades silently and every registrant
  gets an empty tier.
- Open the form as an **anonymous user in a private window** and walk every
  branch. `#states` conditions behave differently for authenticated users, and
  most registrants are anonymous.
- Submit one test registration and inspect it at
  `/admin/structure/webform/manage/<id>/results/submissions`.
- Confirm `uuid` holds a real UUID and not the literal `[webform_submission:uuid]`.

Then go to [03 — Wiring to deployed apps](03-integration.md).

## Versioning your form

Export the form back out after every change and commit it:

**Build → Source → copy**, or:

```sh
drush config:get webform.webform.registration elements --format=yaml \
  > webforms/registration.yaml
```

Two reasons. Drupal has no history for a form built in the UI — an accidental
deletion is unrecoverable. And your event profile's field map references element
keys, so a committed export is what lets CI check the two still agree.
