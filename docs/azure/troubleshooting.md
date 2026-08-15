# When it stops

First, always:

```zsh
eventkit azure status --app <app>     # the ledger beside what Azure reports
eventkit azure doctor                 # tooling, sign-in, gates; changes nothing
```

Then `eventkit azure resume --app <app>`. Resume is safe at any point — it
replays only what is `pending` or `failed`, and it is the same code path as
`deploy`, so it is exercised on every run.

---

### The deploy stopped at a gate and I cannot do that part today

```zsh
eventkit azure gate ack <id> --until 2026-09-01 --reason "OIT ticket 12345"
eventkit azure resume --app <app>
```

The acknowledgement expires. Nightly drift will tell you when.

### It failed under `--yes` with a checklist

That is the design. A gate under `--yes` fails fast rather than blocking a CI
job forever. Run it interactively, or acknowledge the gate.

### The container never answers, and the deploy reports it never got healthy

```zsh
eventkit azure logs --app <app>
```

In order of likelihood:

1. **The image cannot be pulled.** Check the identity step ran:
   `az role assignment list --assignee <principal> --scope <acr-id>` should show
   `AcrPull`, and `acrUseManagedIdentityCreds` should be true. Role assignment
   propagation is eventually consistent — `resume` re-checks.
2. **Startup exceeded the time limit.** `WEBSITES_CONTAINER_START_TIME_LIMIT`
   should be `600`; the 230-second default kills a first boot that runs
   migrations. The toolkit sets it; a hand-edited app might not have it.
3. **A required setting is missing** and the application refuses to start —
   which is correct behaviour. The log line names it.

### Admin sign-in started returning 500 and nothing changed

The Easy Auth client secret expired. This is the single most likely "the site
broke and nobody knows why" event on a two-year horizon.

```zsh
eventkit azure secrets rotate --app <app>
```

It uses `--append`, so there is no window with no valid secret. The nightly
drift workflow warns 60 days ahead; if you are reading this, it was not running.

### Everyone is being denied, including me

`AUTHORIZED_PRINCIPALS` empty means **deny all**, deliberately. An empty
allow-list that meant "allow everyone" would turn one mis-set application setting
into an open admin panel.

```zsh
az webapp config appsettings list -g <rg> -n <app> \
  --query "[?name=='AUTHORIZED_PRINCIPALS'].value" -o tsv
```

Fix it by re-running `eventkit azure deploy` — not by editing it in the portal,
or the ledger and reality diverge.

### The Drupal webhook returns 403 for every submission

Almost always the `headers:` nesting in the Remote Post handler's custom options.
A flat key is ignored by Guzzle and the token never arrives. The toolkit prints
the exact block; paste it as-is.

Failing that, the token differs. The application logs
`webhook.verify outcome=deny reason=mismatch fp=3f9a21` — a six-character
fingerprint of what was presented, never the token itself. Compare it against
the fingerprint of the value in `.env.deploy`.

### Registrations arrive but some fields are empty

Someone renamed an element in Drupal. `GET /api/webhook/status` reports
`unmapped_keys`; the application also logs them per submission. Update the field
map in the event profile.

### Postgres was reachable and now is not

The App Service plan was scaled or moved, so its outbound addresses changed and
the firewall no longer admits them. The nightly drift workflow re-adds them
automatically — this is the one case where drift remediates rather than reports,
because the failure is an outage.

Manually:

```zsh
az webapp show -g <rg> -n <app> --query possibleOutboundIpAddresses -o tsv
```

and add each as a firewall rule.

### The database is locked, or writes are being lost

SQLite is on Azure Files over SMB. It uses `journal_mode=TRUNCATE` — WAL is
impossible over SMB, since it needs shared-memory mmap — and the plan must stay
at one instance. Check:

```zsh
az appservice plan show -g <rg> -n <plan> --query sku.capacity
```

If it is above 1, `eventkit azure deploy` will pin it back. If you genuinely need
more than one instance, move to Postgres with `--postgres`.

### `eventkit azure teardown` refuses

It requires the resource group name typed exactly. That is not a formality: the
group holds every application deployed for the event, and the predecessors had a
teardown script and a teardown workflow pointing at *different* resource groups.

### A resource exists but the toolkit says it differs

Deliberate. Present-and-matching is recorded and skipped; absent is created;
present-but-different is refused with a warning. Use `--adopt` to record it
as-is, or `--reconcile` to bring it to spec. Silently adopting a mismatched
resource is how you end up deploying into somebody else's application.

### I want to see what it would do

```zsh
eventkit azure deploy --event <event> --dry-run
```

Prints every `az` command, runs none, and passes straight through the manual
gates without waiting — a dry run describes what would happen; it does not block
on something nobody is being asked to do yet.

### Something in the toolkit itself is wrong

```zsh
eventkit azure eject --dest ./deploy
```

Copies the toolkit into the repository so it can be modified. Please also open
an issue — an ejected copy stops receiving fixes.
