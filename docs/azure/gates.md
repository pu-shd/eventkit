# Gates

A gate is a step the toolkit cannot do for you, paired with a read-only check
that tells it when you have. It polls; it never asks you to confirm.

| Gate | You do | Passes when |
|---|---|---|
| `az-login` | `az login` | `az account show` returns a subscription |
| `gh-auth` | `gh auth login` | `gh auth status` exits 0 |
| `provider-reg` | nothing — registration can lag 2–5 min | `Microsoft.DBforPostgreSQL` is `Registered` |
| `easy-auth` | add the Entra identity provider in the portal | a `clientId` exists **and** the platform is enabled |
| `acrpull` | nothing — the toolkit assigns it | the `AcrPull` role assignment appears |
| `eventbrite-token` | fetch the private token and event id from Eventbrite | `/users/me/` **and** `/events/<id>/` both 200 |
| `webhook` | add the Remote Post handler in Drupal | the app reports `authenticated_total > 0` |
| `dns-cname` | ask your DNS admin for the CNAME | `dig` shows it pointing at the web app |
| `domain-cert` | nothing — the toolkit requests it | the hostname's `sslState` is `SniEnabled` |

## The three that need explaining

**`easy-auth`** — Authentication → Add identity provider → Microsoft → Workforce
configuration; *Restrict access*: Require authentication; *Unauthenticated
requests*: HTTP 302 redirect.

Both halves of the check matter: a provider registered with the platform
disabled leaves the app wide open while looking configured.

**`eventbrite-token`** — both calls are checked. A valid token against the wrong
event id fails later as an empty attendee list, which reads as "nobody bought a
ticket" rather than as a misconfiguration.

**`webhook`** — Drupal → your form → Handlers → Add handler → Remote Post.
Completed and Updated URL both `https://<app>.azurewebsites.net/api/drupal-webhook`.
Method POST, type JSON. Custom options:

```yaml
headers:
  X-Drupal-Webhook-Token: <the token the toolkit printed>
```

**The nesting matters.** A flat key is ignored by Guzzle and every call 403s
while the registrant still sees success.

The check polls the app's own `/api/webhook/status` until an authenticated
submission has actually arrived — proof the whole path works, which "handler
saved" is not.

## Acknowledging one

```zsh
eventkit azure gate ack easy-auth --until 2027-09-01 --reason "OIT ticket 12345"
```

Both flags are required and it expires. Skipped gates marked critical reappear
in nightly [drift](ci-cd.md#drift) until met or acknowledged.
