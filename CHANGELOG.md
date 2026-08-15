# Changelog

## 0.3.1

### Removed

- **gitleaks, and every third-party GitHub Action.** CI is now shell commands
  plus GitHub's own `actions/*` and Microsoft's `azure/login`, which performs the
  OIDC exchange against the deployment target. Secret scanning is `grep` over six
  credential shapes — private keys, cloud access key ids, provider tokens,
  connection strings, and client-secret and password literals — verified both to
  fire on planted secrets and to stay quiet on `password = os.environ[...]`. The
  named checks for values this codebase has actually leaked are unchanged.
  `.gitleaks.toml` is deleted.
- `docker/setup-buildx-action` and `docker/build-push-action` from the shipped CI
  templates, in favour of plain `docker build` and `docker push`. The runner
  already has a daemon; the cost is Actions layer caching, the gain is that
  nothing third-party stands between a commit and the image that runs in
  production.

## 0.3.0

Phase 2: the Azure bootstrap toolkit.

### Added

- **`eventkit azure`** — an interactive, colourful, resumable toolkit for one
  event's deployment, shipped as package data and executed with `execve` so it
  genuinely owns the terminal and its signals. Verbs: `deploy`, `resume`,
  `update`, `teardown`, `status`, `doctor`, `adopt`, `drift`, `gate ack`,
  `logs`, `open`, `eject`. Global `--dry-run`, `--yes`, `--no-reprompt`,
  `--postgres`, `--verbose`.
- **The manual-step gate.** A numbered checklist and a portal deep link, then a
  poll of a read-only predicate with a spinner and elapsed time, succeeding the
  instant the predicate passes and accepting `[s]kip [r]etry [o]pen [q]uit`
  while it waits. Under `--yes` it fails fast with the checklist rather than
  blocking a CI job. Gates: `az-login`, `gh-auth`, `easy-auth`,
  `provider-reg`, `acrpull`, `eventbrite-token`, `webhook`, `dns-cname`,
  `domain-cert`.
- **`.eventkit/state.json`**, a committed step ledger recording what was created
  and what is outstanding, so `resume` replays only `pending` and `failed` — and
  so the work can be picked up on another machine. No secret is ever written to
  it; there is a test asserting that after a full deploy.
- **Managed identity throughout, no passwords.** System-assigned identity with
  `AcrPull` for the web app and the registry admin account disabled; a
  user-assigned identity with federated credentials for GitHub Actions, holding
  `AcrPush` and `Website Contributor` rather than Contributor on the group.
- **Six CI/CD workflow templates** — `test`, `deploy`, `backup`, `admin-task`,
  `drift`, `teardown` — plus an annotated `app.conf.example`.
- **`docs/azure/`** — the toolkit, the workflows, a gate reference, how to add an
  application, and troubleshooting.
- **36 bats tests** against a mock `az`, `openssl`, `gh`, `curl` and `dig`,
  covering the whole deploy flow with no subscription and no network. `shellcheck`
  over the toolkit. Both run from `./run_tests.sh`.

### Fixed

- `(( EK_DRY_RUN )) && print …` as a function's last statement. In zsh,
  `(( expr ))` exits non-zero when the expression is 0, so with dry run off
  `ek_print_plan` returned 1 and `setopt err_return` aborted the caller: a plain
  `deploy` died silently right after printing its plan while `--dry-run` worked
  perfectly. Now an explicit `if`, in both places it occurred.
- `local … status …` in `ek_steps_run`. `status` is read-only in zsh.
- `ek_gen_secret` returning empty output rather than failing, which sent the
  settings step down the "prompt the operator" path and aborted a `--yes` run
  with a misleading message. It now validates its own output.
- `ek_conf_each_setting` aborting under `no_unset` on a settings table with
  trailing empty fields.
- Invalid TOML in the five shipped `deploy/app.conf` files (`name = "X"; type =
  "computed"` on one line). Fixed in each application repository, which now also
  carries `tests/test_deploy_conf.py`.

### Removed

- The `NOT_YET_BUILT` CLI scaffolding. Every registered verb is now built.

## 0.2.1

- `IdentityMixin` failed under PEP 563 in every application that used it.

## 0.2.0

- Phase 1 complete: `db`, `auth`, `backup`, `realtime`, `notify`, `importer`,
  `admin`, `ui`, `mirror`, and `eventbrite.{client,sync}` joined the modules
  released in 0.1.0. 833 tests.

## 0.1.0

First extraction from `ticketed` and `posted`. Fresh history, no import.

### Added

- **`identity`** — `person_key(uuid, email)` preferring the Drupal submission
  uuid. Frozen, versioned contract (`PERSON_KEY_VERSION = 1`), plus
  `normalize_email`, `diff_populations` and a lazily-resolved `IdentityMixin`.
- **`drupal`** — one parser replacing three. Total coercion primitives,
  configurable `FieldMap` / `WebformSchema` with inference that warns on every
  heuristic, and `parse_submission()` used by both webhook and importer.
- **`eventprofile`** — validated per-event YAML, public JSON projection with a
  trip-wire test, and the legacy check-in key migration.
- **`webhook`** — `compare_digest` verification, `assert_strong()`,
  HMAC-over-body with timestamp, `deferred()`.
- **`eventbrite`** — pure `aggregate_by_email()`, typed models.
- **`logging`** — `configure_logging()` and a `RedactFilter`.
- **`testing`** — a pytest plugin exporting the fixtures every application needs.
- **`cli`** — `eventkit profile|fieldmap|ui|db|mirror`.

Import weight is enforced by test: `eventprofile` and `ui` pull in no FastAPI and
no SQLAlchemy.
