# Troubleshooting

Always start here:

```zsh
eventkit azure status --app <app>   # the ledger beside what Azure reports
eventkit azure doctor               # tooling, sign-in, gates; changes nothing
eventkit azure resume --app <app>   # replays only pending and failed steps
```

| Symptom | Cause | Fix |
|---|---|---|
| Stopped at a gate you can't do today | — | `eventkit azure gate ack <id> --until <date> --reason <ticket>`, then `resume` |
| Failed under `--yes` with a checklist | By design — gates don't block CI | Run interactively, or acknowledge the gate |
| Container never answers | Image can't be pulled | Check `AcrPull` and `acrUseManagedIdentityCreds`; `resume` re-checks |
| " | Startup exceeded the limit | `WEBSITES_CONTAINER_START_TIME_LIMIT` should be `600` |
| " | A required setting is missing | Correct — the log line names it |
| Admin sign-in returns 500, nothing changed | Easy Auth client secret expired | `eventkit azure secrets rotate` |
| Everyone denied, including you | `AUTHORIZED_PRINCIPALS` empty means deny all | Re-run `deploy` — don't edit in the portal |
| Every webhook 403s, registrants see success | `headers:` not nested in the handler | Paste the block the toolkit prints |
| Some fields silently empty | An element was renamed in Drupal | `unmapped_keys` at `/api/webhook/status` names it |
| Postgres was reachable, now isn't | The plan was scaled; outbound IPs changed | The drift workflow re-adds them; or add them manually |
| Database locked, writes lost | More than one instance on SQLite | `deploy` pins it back, or move to `--postgres` |
| `teardown` refuses | It wants the group name typed exactly | Not a formality — the group holds every app for the event |
| "Resource exists but differs" | Deliberate refusal | `--adopt` to record as-is, `--reconcile` to bring to spec |

## A few in more detail

**Container won't start.** `eventkit azure logs --app <app>`. In likelihood
order: the image can't be pulled (role assignment propagation is eventually
consistent — `resume` re-checks), startup exceeded 230 s, or a required setting
is absent and the app is correctly refusing to boot.

**Webhook 403s.** Almost always the `headers:` nesting. Otherwise the token
differs — the app logs `webhook.verify outcome=deny reason=mismatch fp=3f9a21`,
a six-character fingerprint of what was presented. Compare it with the
fingerprint of the value in `.env.deploy`.

**Postgres unreachable.** App Service outbound addresses change when the plan is
scaled or moved:

```zsh
az webapp show -g <rg> -n <app> --query possibleOutboundIpAddresses -o tsv
```

Add each as a firewall rule.

**SQLite locking.** It lives on Azure Files over SMB, uses
`journal_mode=TRUNCATE` (WAL needs shared-memory mmap, which SMB lacks), and
requires one instance. If you genuinely need more, move to Postgres.

## See what it would do

```zsh
eventkit azure deploy --event <event> --dry-run
```

Prints every `az` command, runs none, and passes straight through the gates
without waiting.

## Change the toolkit

```zsh
eventkit azure eject --dest ./deploy
```

Copies it into the repository. Please open an issue too — an ejected copy stops
receiving fixes.
