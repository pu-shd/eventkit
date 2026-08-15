#!/usr/bin/env bats
#
# The Azure toolkit, exercised with a mock `az` on PATH.
#
# No subscription, no network, no credentials. The predecessors' deploy scripts
# were 1,475 lines of untested shell containing a backslash-then-blank-line that
# silently truncated the app-settings command; the point of a mock is that a bug
# of that shape shows up here instead of in production.

setup() {
  REPO_ROOT="$(cd "${BATS_TEST_DIRNAME}/../.." && pwd)"
  TOOLKIT="${REPO_ROOT}/src/eventkit/azure"
  export EVENTKIT_AZURE_LIB="${TOOLKIT}/lib"

  WORK="$(mktemp -d)"
  cd "$WORK"

  export PATH="${BATS_TEST_DIRNAME}/bin:${PATH}"
  export AZ_MOCK_LOG="${WORK}/az.log"
  export AZ_MOCK_FIXTURES="${BATS_TEST_DIRNAME}/fixtures"
  : > "$AZ_MOCK_LOG"

  export NO_COLOR=1
  export EK_GATE_POLL_SECONDS=1
  export EK_GATE_TIMEOUT_SECONDS=3
  # The verify step polls /healthz; against a mock that never answers, the
  # production 180s budget would make this suite take half an hour.
  export EK_VERIFY_TIMEOUT_SECONDS=1
  export EK_VERIFY_INTERVAL_SECONDS=1

  mkdir -p deploy
  cat > deploy/app.conf <<'CONF'
name        = "poster-gallery"
image       = "poster-gallery"
health_path = "/healthz"
easy_auth   = true
needs_db    = true
db_default  = "sqlite"
gates       = ["easy-auth", "webhook"]

[[setting]]
name = "DATABASE_URL"
type = "computed"
required = true
[[setting]]
name = "DRUPAL_WEBHOOK_TOKEN"
type = "secret"
required = true
generate = "hex32"
[[setting]]
name = "AUTHORIZED_PRINCIPALS"
type = "list"
required = true
default = "admin@example.edu"
[[setting]]
name = "ENABLE_RESTORE"
type = "bool"
default = "False"
CONF
  touch Dockerfile
}

teardown() {
  cd /
  rm -rf "$WORK"
}

run_toolkit() {
  run zsh "${TOOLKIT}/eventkit-azure" "$@"
}

# --------------------------------------------------------------------------
# Naming
# --------------------------------------------------------------------------
@test "names are deterministic" {
  run zsh -c "source ${EVENTKIT_AZURE_LIB}/boot.zsh; ek_name webapp caarms-2026 poster abc123"
  [ "$status" -eq 0 ]
  first="$output"
  run zsh -c "source ${EVENTKIT_AZURE_LIB}/boot.zsh; ek_name webapp caarms-2026 poster abc123"
  [ "$output" = "$first" ]
}

@test "a registry name has no dashes and fits the 50-character ceiling" {
  run zsh -c "source ${EVENTKIT_AZURE_LIB}/boot.zsh; ek_name acr a-very-long-event-name-that-keeps-going-and-going x1"
  [ "$status" -eq 0 ]
  [[ ! "$output" =~ "-" ]]
  [ "${#output}" -le 50 ]
}

@test "a storage name fits the 24-character ceiling" {
  run zsh -c "source ${EVENTKIT_AZURE_LIB}/boot.zsh; ek_name storage some-quite-long-event-name suffix"
  [ "${#output}" -le 24 ]
}

@test "clamping keeps the discriminating suffix" {
  run zsh -c "source ${EVENTKIT_AZURE_LIB}/boot.zsh; ek_name acr an-extremely-long-event-name-that-will-definitely-be-clamped zz9988"
  [[ "$output" == *"zz9988" ]]
}

# --------------------------------------------------------------------------
# The ledger
# --------------------------------------------------------------------------
@test "a fresh ledger starts with no steps" {
  run zsh -c "source ${EVENTKIT_AZURE_LIB}/boot.zsh; EK_STATE_FILE=s.json; ek_state_init ev app >/dev/null; ek_state_steps_json"
  [ "$output" = "[]" ]
}

@test "an unknown step reads as pending" {
  run zsh -c "source ${EVENTKIT_AZURE_LIB}/boot.zsh; EK_STATE_FILE=s.json; ek_state_init ev app >/dev/null; ek_step_status nope"
  [ "$output" = "pending" ]
}

@test "a recorded step round-trips" {
  run zsh -c "source ${EVENTKIT_AZURE_LIB}/boot.zsh; EK_STATE_FILE=s.json; ek_state_init ev app >/dev/null; ek_step_record webapp done /sub/x; ek_step_status webapp"
  [ "$output" = "done" ]
}

@test "recording a step twice updates rather than duplicating" {
  run zsh -c "source ${EVENTKIT_AZURE_LIB}/boot.zsh; EK_STATE_FILE=s.json; ek_state_init ev app >/dev/null; ek_step_record x pending; ek_step_record x done; ek_state_steps_json"
  [[ "$output" == *'"done"'* ]]
  count=$(printf '%s' "$output" | grep -o '"id"' | wc -l | tr -d ' ')
  [ "$count" -eq 1 ]
}

@test "no secret is written to the ledger" {
  run_toolkit deploy --event caarms-2026 --yes --dry-run
  [ -f .eventkit/state.json ]
  run grep -ciE 'token|password|secret' .eventkit/state.json
  [ "$output" = "0" ]
}

# --------------------------------------------------------------------------
# Dry run
# --------------------------------------------------------------------------
@test "dry run prints commands and creates nothing" {
  run_toolkit deploy --event caarms-2026 --yes --dry-run
  [ "$status" -eq 0 ]
  [[ "$output" == *"dry-run:"* ]]
  # The only az calls allowed under dry-run are the read-only preflight ones.
  run grep -c "group create" "$AZ_MOCK_LOG"
  [ "$output" = "0" ]
}

@test "dry run still reports the plan" {
  run_toolkit deploy --event caarms-2026 --yes --dry-run
  [[ "$output" == *"resource group"* ]]
  [[ "$output" == *"web app"* ]]
}

# --------------------------------------------------------------------------
# Idempotence and resume
# --------------------------------------------------------------------------
@test "a second deploy skips the steps already done" {
  run_toolkit deploy --event caarms-2026 --yes --dry-run
  [ "$status" -eq 0 ]
  run_toolkit deploy --event caarms-2026 --yes --dry-run
  [[ "$output" == *"already done"* ]]
}

@test "resume does not re-create the registry" {
  run_toolkit deploy --event caarms-2026 --yes
  : > "$AZ_MOCK_LOG"
  run_toolkit resume --yes
  run grep -c "acr create" "$AZ_MOCK_LOG"
  [ "$output" = "0" ]
}

@test "names survive between runs" {
  run_toolkit deploy --event caarms-2026 --yes --dry-run
  first=$(python3 -c 'import json
with open(".eventkit/state.json") as fh: print(json.load(fh)["names"]["webApp"])')
  run_toolkit deploy --event caarms-2026 --yes --dry-run
  second=$(python3 -c 'import json
with open(".eventkit/state.json") as fh: print(json.load(fh)["names"]["webApp"])')
  [ "$first" = "$second" ]
}

# --------------------------------------------------------------------------
# The manual gate — the requirement this toolkit exists for
# --------------------------------------------------------------------------
@test "a non-interactive run fails fast at a gate instead of hanging" {
  run_toolkit deploy --event caarms-2026 --yes
  [ "$status" -ne 0 ]
  [[ "$output" == *"non-interactive"* ]]
  [[ "$output" == *"resume"* ]]
}

@test "an unmet gate is left pending so resume re-enters there" {
  run_toolkit deploy --event caarms-2026 --yes
  status_of=$(python3 -c '
import json
with open(".eventkit/state.json") as fh:
    steps = {s["id"]: s["status"] for s in json.load(fh)["steps"]}
print(steps.get("easy-auth", "absent"))')
  [ "$status_of" = "pending" ]
}

@test "the gate prints the portal link and a numbered checklist" {
  run_toolkit deploy --event caarms-2026 --yes
  [[ "$output" == *"portal.azure.com"* ]]
  [[ "$output" == *"1."* ]]
  [[ "$output" == *"Add identity provider"* ]]
}

@test "a satisfied gate is not asked about" {
  mkdir -p "${WORK}/fx"
  cp "${AZ_MOCK_FIXTURES}"/* "${WORK}/fx/"
  # Easy Auth already configured: the clientId query answers.
  cat > "${WORK}/fx/webapp_auth_show" <<'EOF'
aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee
EOF
  export AZ_MOCK_FIXTURES="${WORK}/fx"
  run_toolkit deploy --event caarms-2026 --yes
  [[ "$output" == *"already configured"* ]]
}

@test "an acknowledgement needs both an expiry and a reason" {
  run_toolkit deploy --event caarms-2026 --yes --dry-run
  run_toolkit gate ack easy-auth
  [ "$status" -ne 0 ]
  [[ "$output" == *"--until"* ]]
}

@test "an acknowledged gate is not re-asked" {
  run_toolkit deploy --event caarms-2026 --yes --dry-run
  run_toolkit gate ack easy-auth --until 2099-01-01 --reason "OIT ticket 12345"
  [ "$status" -eq 0 ]
  run python3 -c '
import json
with open(".eventkit/state.json") as fh:
    steps = {s["id"]: s for s in json.load(fh)["steps"]}
print(steps["easy-auth"].get("ackUntil"))'
  [ "$output" = "2099-01-01" ]
}

# --------------------------------------------------------------------------
# Managed identity
# --------------------------------------------------------------------------
@test "the identity step grants AcrPull and turns on managed-identity pull" {
  run_toolkit deploy --event caarms-2026 --yes
  run grep -c "webapp identity assign" "$AZ_MOCK_LOG"
  [ "$output" != "0" ]
  run grep -c "acrUseManagedIdentityCreds" "$AZ_MOCK_LOG"
  [ "$output" != "0" ]
}

@test "any registry password left by an older deployment is deleted" {
  run_toolkit deploy --event caarms-2026 --yes
  run grep -c "DOCKER_REGISTRY_SERVER_PASSWORD" "$AZ_MOCK_LOG"
  [ "$output" != "0" ]
}

@test "the registry is created without an admin account" {
  run_toolkit deploy --event caarms-2026 --yes
  run grep -c -- "--admin-enabled false" "$AZ_MOCK_LOG"
  [ "$output" != "0" ]
}

# --------------------------------------------------------------------------
# Settings — written once, by the toolkit
# --------------------------------------------------------------------------
@test "the container start-time limit is set" {
  run_toolkit deploy --event caarms-2026 --yes
  run grep -c "WEBSITES_CONTAINER_START_TIME_LIMIT=600" "$AZ_MOCK_LOG"
  [ "$output" != "0" ]
}

@test "a webhook token is generated and kept out of the ledger" {
  run_toolkit deploy --event caarms-2026 --yes
  [ -f .env.deploy ]
  run grep -c "DRUPAL_WEBHOOK_TOKEN" .env.deploy
  [ "$output" != "0" ]
  run grep -c "DRUPAL_WEBHOOK_TOKEN" .eventkit/state.json
  [ "$output" = "0" ]
}

@test "every declared setting reaches one appsettings call" {
  run_toolkit deploy --event caarms-2026 --yes
  line=$(grep "webapp config appsettings set" "$AZ_MOCK_LOG" | grep "AUTHORIZED_PRINCIPALS" | head -1)
  # The predecessor truncated this command with a stray line continuation, so
  # everything after a certain point silently never reached Azure.
  [[ "$line" == *"DATABASE_URL="* ]]
  [[ "$line" == *"AUTHORIZED_PRINCIPALS="* ]]
  [[ "$line" == *"ENABLE_RESTORE="* ]]
  [[ "$line" == *"WEBSITES_PORT=8000"* ]]
}

# --------------------------------------------------------------------------
# SQLite guard
# --------------------------------------------------------------------------
@test "sqlite deployments are pinned to one instance" {
  run_toolkit deploy --event caarms-2026 --yes
  run grep -c -- "--number-of-workers 1" "$AZ_MOCK_LOG"
  [ "$output" != "0" ]
}

@test "sqlite enables the persistent share" {
  run_toolkit deploy --event caarms-2026 --yes
  run grep -c "WEBSITES_ENABLE_APP_SERVICE_STORAGE=true" "$AZ_MOCK_LOG"
  [ "$output" != "0" ]
}

@test "postgres is created with no firewall opening" {
  run_toolkit deploy --event caarms-2026 --postgres --yes
  if grep -q "flexible-server create" "$AZ_MOCK_LOG"; then
    run grep -c -- "--public-access None" "$AZ_MOCK_LOG"
    [ "$output" != "0" ]
    run grep -c -- "--public-access 0.0.0.0" "$AZ_MOCK_LOG"
    [ "$output" = "0" ]
  fi
}

# --------------------------------------------------------------------------
# Other verbs
# --------------------------------------------------------------------------
@test "doctor reports without changing anything" {
  run_toolkit doctor
  : > "$AZ_MOCK_LOG"
  run_toolkit doctor
  run grep -cE "create|delete|update" "$AZ_MOCK_LOG"
  [ "$output" = "0" ]
}

@test "status works before anything is deployed" {
  run_toolkit status --event caarms-2026 --yes
  [ "$status" -eq 0 ]
  [[ "$output" == *"Ledger"* ]]
}

@test "teardown refuses when the typed name does not match" {
  run_toolkit deploy --event caarms-2026 --yes --dry-run
  run zsh -c "printf 'wrong-name\n' | zsh '${TOOLKIT}/eventkit-azure' teardown"
  [ "$status" -ne 0 ]
  run grep -c "group delete" "$AZ_MOCK_LOG"
  [ "$output" = "0" ]
}

@test "update does not provision" {
  run_toolkit deploy --event caarms-2026 --yes --dry-run
  : > "$AZ_MOCK_LOG"
  run_toolkit update --yes --dry-run
  run grep -cE "group create|acr create|appservice plan create" "$AZ_MOCK_LOG"
  [ "$output" = "0" ]
}

@test "an unknown verb exits 2 with usage" {
  run_toolkit nonsense
  [ "$status" -eq 2 ]
  [[ "$output" == *"Unknown verb"* ]]
}

@test "help needs no configuration" {
  cd /
  run zsh "${TOOLKIT}/eventkit-azure" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"deploy"* ]]
}

# --------------------------------------------------------------------------
# Colour
# --------------------------------------------------------------------------
@test "NO_COLOR suppresses escape codes" {
  NO_COLOR=1 run_toolkit deploy --event caarms-2026 --yes --dry-run
  [[ "$output" != *$'\e['* ]]
}
