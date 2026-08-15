# Federated credentials for CI, so no secret is stored in GitHub.
#
# A user-assigned managed identity with a federated credential, rather than an
# app registration with a client secret. GitHub presents a short-lived OIDC token
# and Azure exchanges it; there is no password to rotate and none to leak.
#
# Federated credential subjects do not support wildcards, so each pattern that
# needs to deploy gets its own credential. Branch and environment are set up
# here; add a tag pattern by hand if you release that way.

_ek_step_oidc() {
  if ! ek_gh_available; then
    ek_warn "gh is not installed, so CI credentials were not configured."
    ek_dim  "  Install it and run: eventkit azure oidc --app ${EK_APP}"
    ek_step_record oidc skipped "" "gh missing"
    return 0
  fi
  if ! ek_verify_gh_auth; then
    ek_await_manual_step --id gh-auth --title "GitHub CLI sign-in" \
      --verify ek_verify_gh_auth \
      --checklist "Run: gh auth login|Choose GitHub.com, HTTPS, and authenticate in the browser." \
      || return 1
  fi

  local repo
  repo="$(ek_gh_repo)" || { ek_warn "No git remote named origin; skipping CI setup."; ek_step_record oidc skipped "" "no remote"; return 0; }

  local identity="$EK_IDENTITY"
  if ! ek_az_exists identity show --resource-group "$EK_RG" --name "$identity" --query id -o tsv; then
    ek_az identity create --resource-group "$EK_RG" --name "$identity" --location "$EK_LOCATION" -o none
    (( EK_DRY_RUN )) || sleep 5
  fi

  local client_id principal_id
  client_id="$(ek_az_query identity show --resource-group "$EK_RG" --name "$identity" --query clientId -o tsv)"
  principal_id="$(ek_az_query identity show --resource-group "$EK_RG" --name "$identity" --query principalId -o tsv)"
  client_id="${client_id:-<dry-run>}"; principal_id="${principal_id:-<dry-run>}"

  # Least privilege: push images, and restart the one web app. Not Contributor
  # on the resource group, which is what the predecessor granted.
  local acr_id webapp_id
  acr_id="$(ek_az_query acr show --name "$EK_ACR" --query id -o tsv)"
  webapp_id="$(ek_az_query webapp show --resource-group "$EK_RG" --name "$EK_WEBAPP" --query id -o tsv)"
  ek_az role assignment create --assignee-object-id "$principal_id" \
    --assignee-principal-type ServicePrincipal --role AcrPush --scope "${acr_id:-/}" -o none 2>/dev/null || true
  ek_az role assignment create --assignee-object-id "$principal_id" \
    --assignee-principal-type ServicePrincipal --role "Website Contributor" --scope "${webapp_id:-/}" -o none 2>/dev/null || true

  local subject
  for subject in "repo:${repo}:ref:refs/heads/main" "repo:${repo}:environment:production"; do
    local cred_name="gh-${${subject##*:}//\//-}"
    ek_az identity federated-credential create --resource-group "$EK_RG" \
      --identity-name "$identity" --name "$cred_name" \
      --issuer "https://token.actions.githubusercontent.com" \
      --subject "$subject" --audiences "api://AzureADTokenExchange" -o none 2>/dev/null || true
  done

  # Identifiers, not credentials — variables rather than secrets, so a failed
  # run's log is readable instead of a wall of asterisks.
  ek_gh_var_set "$repo" AZURE_CLIENT_ID "$client_id"
  ek_gh_var_set "$repo" AZURE_TENANT_ID "$EK_TENANT"
  ek_gh_var_set "$repo" AZURE_SUBSCRIPTION_ID "$EK_SUBSCRIPTION"
  ek_gh_var_set "$repo" AZURE_RESOURCE_GROUP "$EK_RG"
  ek_gh_var_set "$repo" AZURE_WEBAPP_NAME "$EK_WEBAPP"
  ek_gh_var_set "$repo" ACR_NAME "$EK_ACR"

  ek_state_set names.ciIdentity "$identity"
  ek_step_record oidc done "$client_id"
  ek_step_done "federated to ${repo}, no secret stored"
}
ek_step_register oidc "CI federated credentials (managed identity)" _ek_step_oidc
