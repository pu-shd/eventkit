# 03 — Wiring the form to deployed apps

Getting submissions from Drupal into each eventkit application.

## One handler per app

Each app gets its own Remote Post handler on the same webform, with its own
token and its own endpoint. They fire independently; one failing does not stop
the others.

| App | Endpoint | Token setting |
|---|---|---|
| `ticket-reconciler` | `POST /api/drupal-webhook` | `DRUPAL_WEBHOOK_TOKEN` |
| `poster-gallery` | `POST /api/drupal-webhook` | `DRUPAL_WEBHOOK_TOKEN` |
| `lodging-planner` | `POST /api/drupal-webhook` | `DRUPAL_WEBHOOK_TOKEN` |
| `nametag-press` | `POST /api/drupal-webhook` | `DRUPAL_WEBHOOK_TOKEN` |

`link-forge` has no database and subscribes to nothing.

**Use a different token per app.** They are separate secrets in separate app
settings; sharing one means rotating it four times or not at all.

```sh
openssl rand -hex 32
```

## Creating a handler

`/admin/structure/webform/manage/<webform_id>/handlers` → **Add handler** →
**Remote Post**.

| Setting | Value |
|---|---|
| Title | `ticket-reconciler` (name it after the app) |
| **Completed URL** | `https://<app>.azurewebsites.net/api/drupal-webhook` |
| **Updated URL** | same as Completed |
| Deleted URL | leave empty |
| Method | `POST` |
| Post type | `JSON` |
| **Custom options** | see below — this is the part that goes wrong |

### The header gotcha

The token must be nested under a `headers:` key in **Custom options**. This is
Guzzle's request-options format, not a flat list of headers, and getting it
wrong produces a 403 with no clue why.

**Correct:**

```yaml
headers:
  X-Drupal-Webhook-Token: 'your-64-character-hex-token'
```

**Wrong** — sent as a Guzzle *option* named `X-Drupal-Webhook-Token`, which
Guzzle ignores:

```yaml
X-Drupal-Webhook-Token: 'your-64-character-hex-token'
```

If every call returns 403 while the token looks right, this is almost always why.

### Completed only

Set the **Completed** and **Updated** URLs. Leave **Draft**, **Converted** and
**Deleted** empty.

Remote Post handlers fire **synchronously, inside the registrant's request**. A
slow or erroring handler delays the person submitting the form. With four
handlers on one webform that risk multiplies, so:

- Leave the handler's **Error message** blank so a failing app cannot surface an
  error to a registrant mid-registration.
- Keep **Completed** as the only trigger. Draft posts send half-filled data.

## What gets posted

A JSON body with the form values under `data`, plus submission metadata at the
top level:

```json
{
  "sid": "1234",
  "serial": "87",
  "uuid": "3f8c1e2a-...",
  "webform_id": "registration",
  "data": {
    "email": "ada@example.edu",
    "registrant_name": { "first": "Ada", "last": "Lovelace" },
    "attendee_status": "Attendee",
    "t_shirt_size": "UMED",
    "gender_identity": { "select": "_other_", "other": "…" }
  }
}
```

eventkit accepts the `data` wrapper **or** a flat body, and looks for
`sid`/`serial`/`uuid` at either level. Composites are normalised — see
[01](01-design.md#composites-eventkit-understands).

> The real CAARMS export defines no `uuid`, `sid` or `serial` **elements** —
> those arrived only as submission properties. The templates here declare them
> explicitly as `#type: value` so they also land inside `data`, where the field
> map can see them regardless of how the handler is configured.

## Configuring each app

Each app needs, at minimum:

```sh
az webapp config appsettings set -g <rg> -n <app> --settings \
  DRUPAL_WEBHOOK_TOKEN="$(openssl rand -hex 32)" \
  ALLOWED_ADMIN_PRINCIPALS="alice@example.edu,bob@example.edu" \
  ENABLE_RESTORE=False
```

Notes that have bitten people:

- **The token is required.** No default. The app will not start without it, and
  refuses known placeholders like `secret_drupal_token`.
- **An empty allow-list denies everyone**, which is the safe direction but does
  mean nobody can get in until you set it.
- **`ENABLE_RESTORE` and `ENABLE_DESTRUCTIVE_OPS` default to `False`.** Turn one
  on for the duration of the operation, then turn it back off.

## Verifying the wiring

Submit a real test registration through the public form. Then, for each app:

```sh
# 1. Did an authenticated submission arrive?
curl -s https://<app>.azurewebsites.net/api/webhook/status \
  -H "X-Drupal-Webhook-Token: $TOKEN" | jq

# 2. Is the admin surface actually protected?
curl -s -o /dev/null -w '%{http_code}\n' \
  https://<app>.azurewebsites.net/api/admin/registrants
# expect 401 or 403 — never 200

# 3. Does the public surface leak anything? (poster-gallery)
curl -s https://<app>.azurewebsites.net/api/presenters | grep -c '@'
# expect 0
```

`/api/webhook/status` returns counters and timestamps only — no attendee data —
so it is safe to poll. It also reports `unmapped_keys`, which is how you find
out that a form element is arriving and being ignored.

## Backfilling submissions made before the handler existed

Handlers fire on submission, so anything submitted before you added them is
invisible to the app. Two options:

**Re-save each submission.** For a handful, open each at
`/admin/structure/webform/manage/<id>/results/submissions`, edit, save. The
**Updated** URL fires.

**Bulk export and import.** Export submissions as JSON from the results page,
then feed them to the app's importer. Crucially the importer runs the *same*
parser as the webhook, so a record that imports correctly would have posted
correctly.

## Rotating a token

1. Generate a new one.
2. Set it on the app (`az webapp config appsettings set …`) and wait for restart.
3. Update the handler's **Custom options** in Drupal.
4. Submit a test registration and check `/api/webhook/status`.

Between 2 and 3 the handler posts a stale token and the app returns 403. Drupal
does not retry, so **those submissions are lost**. Rotate outside a registration
window, or plan to re-save the affected submissions afterwards.

## Next

[04 — The field-map contract](04-field-map-contract.md)
