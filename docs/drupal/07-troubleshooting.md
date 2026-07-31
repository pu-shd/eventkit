# 07 — Troubleshooting

Symptom first. Each entry gives the likely cause and how to confirm it.

## Nothing arrives at the app

### Every webhook returns 403

**Almost always the header nesting.** The token must be under a `headers:` key
in the handler's **Custom options**:

```yaml
headers:
  X-Drupal-Webhook-Token: 'your-token'
```

Without `headers:`, Guzzle treats it as an unknown request option and drops it,
so the app sees no token at all.

Confirm from the app side:

```sh
curl -s https://<app>.azurewebsites.net/api/webhook/status \
  -H "X-Drupal-Webhook-Token: $TOKEN" | jq '{received_total, authenticated_total, rejected_total}'
```

`rejected_total` climbing with `authenticated_total` flat means the header is
arriving wrong or the values differ.

The app logs a fingerprint, never the token:

```
webhook.verify outcome=deny reason=mismatch fp=3f9a21
```

Compare `fp` against the first six hex of `sha256` of the token you *think*
Drupal is sending. Matching fingerprints with a 403 means something else;
differing fingerprints mean a stale token somewhere.

### The token was just rotated

Between setting the app setting and updating the Drupal handler, the handler
posts a stale token and gets 403. **Drupal does not retry — those submissions
are lost.** Re-save the affected submissions to fire the Updated URL.

### The app will not start

Since the security hardening, several settings are required and several
defaults changed:

| Symptom in the log | Cause |
|---|---|
| `DRUPAL_WEBHOOK_TOKEN must be set` | No value configured. Generate one. |
| `set to a well-known placeholder value` | It is `secret_drupal_token` or similar. |
| pydantic `ValidationError` on `EVENT_SITE` / `REG_FORM` | Required, no default. |

A workflow can go green while the container fails to boot — the image ships
fine and only then crashes. Always smoke-test the URL after a deploy.

### Handler configured after registration opened

Handlers fire on submission. Earlier submissions are invisible. See
[03](03-integration.md#backfilling-submissions-made-before-the-handler-existed).

## Data arrives but is wrong

### A field is always empty

Check `unmapped_keys` — the element is arriving but has no mapping:

```sh
curl -s https://<app>.azurewebsites.net/api/webhook/status \
  -H "X-Drupal-Webhook-Token: $TOKEN" | jq .unmapped_keys
```

If your element key appears there, add it to the profile's `field_map`
([04](04-field-map-contract.md)). If it does **not** appear, the element is not
being submitted at all — usually because `#states` hides it, and a hidden
element submits nothing.

### A field is empty only for some registrants

`#states` is hiding it for them. Confirm by walking that branch as an anonymous
user in a private window. Remember that absence is meaningful: an absent
exemption checkbox reads as *exempt*.

### Everyone has the same name, or one row holds everyone

The `uuid` element is posting the literal token text
`[webform_submission:uuid]` rather than a value, so every registrant derives an
identical `person_key`. eventkit rejects bracketed values and falls back to the
email hash, so you will more likely see a warning than a collapsed roster — but
fix the element.

Inspect a submission at
`/admin/structure/webform/manage/<id>/results/submissions`.

### A name arrives as one string, or reversed

You used a plain `textfield` rather than `webform_name`. eventkit splits on the
first space, so `"Ursula K. Le Guin"` becomes first `Ursula`, last `K. Le Guin`.
Switch to `webform_name`.

### Gender identity is `_other_` instead of the typed value

Something is reading the raw composite rather than coercing it. The field's
`kind` must be `select_other` in the field map — with `kind: text` you get the
raw `{select, other}` object.

### A boolean field is always true

`kind: text` on a checkbox yields the string `"0"`, which is truthy. Set
`kind: bool`.

### Six lodging and poster fields are all unmapped

Fixed — but if you are on an older eventkit, this was the top-level-only schema
reader failing to see fieldset children. Upgrade, or flatten the fieldsets in
your committed export as a workaround.

## Ticketing

### Everyone reads as exempt

`webform_computed_twig` is not enabled, so `destination_url` degrades silently
and no tier is computed. Enable the module and re-save the form.

### A speaker is being asked to pay

The exclusion gate is in the wrong place in the Twig cascade. Speakers and
organizers must be tested **before** the login-state branches, or a logged-in
speaker falls through into an affiliate tier.

### A tier is computed but no discount applies

The tier key does not match any `ticketing.tiers[].key`, or the environment
variable named by `discount_code_env` is not set on the app. Check both:

```sh
eventkit profile public event-profile.yaml | jq '.ticketing.tiers'
az webapp config appsettings list -g <rg> -n <app> \
  --query "[?starts_with(name,'EVENTBRITE_DISCOUNT')].name" -o tsv
```

### The profile will not validate: `discount_code_env`

You pasted an actual discount code. The field holds a **variable name** in upper
snake case (`EVENTBRITE_DISCOUNT_GA`). A real code like `2030EXAMPLEGA` is
rejected precisely because it looks like a name to a naive check — the validator
requires an underscore and forbids a leading digit.

### Lots of `Unmatched` payments

People bought tickets with a different address than they registered with. Link
them manually in the dashboard; a manual link wins over an email match and will
not be double-claimed. If it is widespread, the purchase link is probably not
carrying the registrant's address through.

## Admin access

### Nobody can get in

An empty `ALLOWED_ADMIN_PRINCIPALS` **denies everyone**. That is deliberate —
the alternative failed open — but it does mean you must set it.

### Everyone can get in

Check Easy Auth is actually configured:

```sh
az webapp auth show -g <rg> -n <app> \
  --query "identityProviders.azureActiveDirectory.registration.clientId" -o tsv
```

Empty means nothing is injecting `X-MS-CLIENT-PRINCIPAL-NAME`, and the app is
trusting a header any client can send. Configure the identity provider.

Also confirm `ALLOW_LOCAL_DEV_ADMIN` is not set in production. It refuses to
engage when `WEBSITE_SITE_NAME` is present, but it should not be there at all.

### Restore or clear returns 403 for a legitimate admin

Both are opt-in. Set `ENABLE_RESTORE=True` or `ENABLE_DESTRUCTIVE_OPS=True` for
the duration, then set it back to `False`. Every destructive run writes a
`DESTRUCTIVE:` audit line naming the principal.

## Public surfaces

### Checking for a PII leak

```sh
curl -s https://<app>.azurewebsites.net/api/presenters | grep -c '@'   # expect 0
```

The public presenter schema is pinned by tests to exactly six fields. If an
email appears, something bypassed `PresenterPublicResponse`.

### Abstracts render as raw LaTeX

MathJax is typesetting before the async render completes, or the delimiters
differ. The gallery expects `$ … $` and `\( … \)`.

## Getting more detail

```sh
az webapp log tail -g <rg> -n <app>          # live logs
eventkit profile validate event-profile.yaml # profile problems, line-numbered
eventkit fieldmap check event-profile.yaml   # mapping problems
eventkit profile checkin-keys event-profile.yaml   # legacy → ISO key mapping
```

Logs never contain secrets: values of known settings are redacted and tokens
appear only as fingerprints. If you see a secret in a log, that is a bug worth
reporting.
