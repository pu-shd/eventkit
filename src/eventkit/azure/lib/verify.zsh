# Gate predicates.
#
# One function per manual step, each answering a single yes/no question about
# the live subscription. They are separate from the gate machinery so they can
# be run on their own — `eventkit azure doctor` uses them to report on a
# deployment nobody has touched in months.
#
# Every `az` invocation here is read-only.

# The one that matters most. The predecessor's provisioning script never
# configured Easy Auth at all, yet its entire admin authorization model depended
# on the header Easy Auth injects. It worked because somebody did it by hand in
# the portal — unscripted, undocumented, and not reproducible.
ek_verify_easy_auth() {
  local client_id
  client_id="$(ek_az_query webapp auth show \
    --resource-group "$EK_RG" --name "$EK_WEBAPP" \
    --query "identityProviders.azureActiveDirectory.registration.clientId" -o tsv)"
  [[ -n "$client_id" && "$client_id" != "null" ]]
}

# Easy Auth must also be switched on, not merely configured.
ek_verify_auth_enabled() {
  local enabled
  enabled="$(ek_az_query webapp auth show \
    --resource-group "$EK_RG" --name "$EK_WEBAPP" \
    --query "platform.enabled" -o tsv)"
  [[ "${enabled:l}" == "true" ]]
}

# The web app's managed identity can pull from the registry, so no registry
# password is ever stored anywhere.
ek_verify_acrpull() {
  local principal acr_id
  principal="$(ek_az_query webapp identity show \
    --resource-group "$EK_RG" --name "$EK_WEBAPP" --query principalId -o tsv)"
  [[ -z "$principal" ]] && return 1
  acr_id="$(ek_az_query acr show --name "$EK_ACR" --query id -o tsv)"
  [[ -z "$acr_id" ]] && return 1
  ek_az_exists role assignment list --assignee "$principal" --scope "$acr_id" \
    --query "[?roleDefinitionName=='AcrPull'].id" -o tsv
}

ek_verify_provider_registered() {
  local namespace="${1:-Microsoft.DBforPostgreSQL}" state
  state="$(ek_az_query provider show --namespace "$namespace" --query registrationState -o tsv)"
  [[ "$state" == "Registered" ]]
}

ek_verify_dns_cname() {
  local fqdn="$1" target="$2"
  command -v dig >/dev/null 2>&1 || return 1
  dig +short "$fqdn" CNAME 2>/dev/null | grep -qi "$target"
}

# Read the binding back from `hostname list` rather than trusting the return of
# `az webapp config ssl create`, which is a preview command whose output shape
# is not stable.
ek_verify_domain_cert() {
  local fqdn="$1" ssl_state
  ssl_state="$(ek_az_query webapp config hostname list \
    --resource-group "$EK_RG" --webapp-name "$EK_WEBAPP" \
    --query "[?name=='${fqdn}'].sslState" -o tsv)"
  [[ "$ssl_state" == "SniEnabled" ]]
}

# A human has to fetch these from the Eventbrite UI; there is no API for it.
ek_verify_eventbrite() {
  local token="${EK_EVENTBRITE_TOKEN:-}" event="${EK_EVENTBRITE_EVENT_ID:-}"
  [[ -z "$token" || -z "$event" ]] && return 1
  command -v curl >/dev/null 2>&1 || return 1
  curl -sf -o /dev/null -H "Authorization: Bearer ${token}" \
    "https://www.eventbriteapi.com/v3/events/${event}/" 2>/dev/null
}

# The Drupal Remote Post handler is created in Drupal's admin UI, so the only
# way to know it is wired up is to watch for the app receiving an authenticated
# submission. /api/webhook/status returns counters only — no attendee data — so
# polling it is safe.
ek_verify_webhook_received() {
  local url="https://${EK_WEBAPP}.azurewebsites.net/api/webhook/status"
  local token="${EK_WEBHOOK_TOKEN:-}"
  [[ -z "$token" ]] && return 1
  command -v curl >/dev/null 2>&1 || return 1
  local count
  count="$(curl -sf --max-time 10 -H "X-Drupal-Webhook-Token: ${token}" "$url" 2>/dev/null \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("authenticated_total", 0))' 2>/dev/null)"
  [[ -n "$count" ]] && (( count > 0 ))
}

ek_verify_app_responds() {
  command -v curl >/dev/null 2>&1 || return 1
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 \
    "https://${EK_WEBAPP}.azurewebsites.net/healthz" 2>/dev/null)"
  [[ "$code" == "200" ]]
}

ek_verify_gh_auth() {
  command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1
}
