# Gate reference

A gate is a step the toolkit cannot perform for you, paired with a read-only
predicate that tells it when you have. It polls; it does not ask you to confirm.
Being told "press enter when done" and pressing enter when not done is how the
predecessor's Easy Auth configuration ended up undocumented and unverifiable.

Every predicate lives in `lib/verify.zsh` and changes nothing.

---

## `az-login`

**You do:** `az login`
**Predicate:** `az account show --query id -o tsv` returns a subscription id.

Checked in preflight, before anything else, along with the `az` version. The
version goes into the ledger's history, so a regression can be correlated with a
CLI upgrade rather than guessed at.

## `gh-auth`

**You do:** `gh auth login` — GitHub.com, HTTPS, authenticate in the browser.
**Predicate:** `gh auth status` exits zero.

Only reached if the repository has an `origin` remote. Without `gh` the CI
identity step is skipped with a note, not failed; the application still deploys.

## `easy-auth` — risk: critical

**You do:** in the portal, Authentication → Add identity provider → Microsoft →
Workforce configuration; let it create a new app registration; set *Restrict
access* to **Require authentication** and *Unauthenticated requests* to
**HTTP 302 Found redirect**; save.

**Predicate:** `az webapp auth show --query
identityProviders.azureActiveDirectory.registration.clientId` is non-empty
**and** `--query platform.enabled` is true.

This is the step `posted/deploy/deploy.sh` neither scripted nor documented,
despite its entire admin authorization model depending on the resulting
`X-MS-CLIENT-PRINCIPAL-NAME` header. It needs permissions in your tenant that a
deployment identity should not hold, which is exactly why it is a gate rather
than a missing feature.

Both halves of the predicate matter. An identity provider registered but with
the platform disabled means the application is wide open while looking
configured. A separate check logs at CRITICAL if `WEBSITE_SITE_NAME` is set
without `WEBSITE_AUTH_ENABLED`.

## `provider-reg`

**You do:** nothing. Registration is automatic but can lag two to five minutes.
**Predicate:** `az provider show -n Microsoft.DBforPostgreSQL --query
registrationState -o tsv` is `Registered`.

Only for `--postgres`.

## `acrpull`

**You do:** nothing — the toolkit assigns it.
**Predicate:** `az role assignment list --assignee <principal> --scope <acr>
--query "[?roleDefinitionName=='AcrPull']"` is non-empty.

It is a gate because role assignment propagation is eventually consistent: the
create call returns before the assignment is usable, and pulling too early fails
in a way that looks like a broken image reference.

## `eventbrite-token`

**You do:** fetch the private token and the event id from the Eventbrite UI.
**Predicate:** `GET /v3/users/me/` and `GET /v3/events/<id>/` both return 200
with that token.

Both are checked. A valid token for the wrong event id fails later, during a
sync, as an empty attendee list — which reads as "nobody has bought a ticket"
rather than as a misconfiguration.

## `webhook`

**You do:** in Drupal, on your registration webform: Handlers → Add handler →
Remote Post. Completed URL and Updated URL both
`https://<app>.azurewebsites.net/api/drupal-webhook`. Method POST, Post type
JSON. Custom options: the `headers:` block the toolkit prints. Leave the error
message blank so a failure never surfaces to a registrant. Save, then submit one
test registration.

**Predicate:** the application's own `GET /api/webhook/status` reports
`authenticated_total > 0`.

The predicate is a real end-to-end proof: a submission left Drupal, arrived,
and authenticated. Nothing weaker distinguishes "handler configured" from
"handler configured with the wrong token", which is silent — the registrant sees
success and the row never appears.

`/api/webhook/status` returns counters and timestamps only — no attendee data —
so it is safe to reach with the webhook token. It also reports `unmapped_keys`,
which is how a live element rename in Drupal surfaces as a warning within one
submission instead of as quietly dropped registrations.

**The `headers:` nesting matters.** A flat key is ignored by Guzzle and every
call 403s. This has cost a day before.

## `dns-cname`

**You do:** ask whoever runs your DNS for a CNAME from your hostname to
`<app>.azurewebsites.net`.
**Predicate:** `dig +short <fqdn> CNAME` contains it.

Propagation is why this polls rather than checking once.

## `domain-cert`

**You do:** nothing; the toolkit requests the managed certificate.
**Predicate:** `az webapp config hostname list --query "[?name=='<fqdn>'].sslState"`
is `SniEnabled`.

Read back rather than trusting the return of `az webapp config ssl create`,
which is marked preview and whose output shape can change.

---

## When a gate cannot be met now

```zsh
eventkit azure gate ack easy-auth --until 2026-09-01 --reason "OIT ticket 12345"
```

Both flags are required. The acknowledgement is recorded in the ledger with the
date and the reason, suppresses the nightly drift alert until then, and
resurfaces loudly afterwards.

There is no permanent silence, on purpose. A nightly alert that is always red is
how the `&>/dev/null` habit gets established, and then the one that mattered is
invisible too.
