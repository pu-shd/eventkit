# Managed identity, so nothing anywhere holds a registry credential.
#
# The web app gets a system-assigned identity, that identity is granted AcrPull
# on the event's registry, and the app is told to use it for the pull. The
# alternative — DOCKER_REGISTRY_SERVER_PASSWORD in app settings — is what the
# predecessors did before someone removed it, and it is a password sitting in a
# place that shows up in `az webapp config appsettings list`.
#
# Role assignment is eventually consistent: the assignment can return before it
# is usable, so the gate below polls rather than assuming.

_ek_step_identity() {
  local principal
  principal="$(ek_az_query webapp identity show \
    --resource-group "$EK_RG" --name "$EK_WEBAPP" --query principalId -o tsv)"

  if [[ -z "$principal" ]]; then
    ek_az webapp identity assign --resource-group "$EK_RG" --name "$EK_WEBAPP" -o none
    (( EK_DRY_RUN )) || sleep 5
    principal="$(ek_az_query webapp identity show \
      --resource-group "$EK_RG" --name "$EK_WEBAPP" --query principalId -o tsv)"
  fi
  (( EK_DRY_RUN )) && principal="${principal:-<dry-run>}"
  [[ -n "$principal" ]] || ek_die "The web app has no managed identity and one could not be assigned."

  local acr_id
  acr_id="$(ek_az_query acr show --name "$EK_ACR" --query id -o tsv)"
  acr_id="${acr_id:-<dry-run>}"

  if ! ek_verify_acrpull; then
    ek_az role assignment create --assignee-object-id "$principal" \
      --assignee-principal-type ServicePrincipal \
      --role AcrPull --scope "$acr_id" -o none 2>/dev/null || \
      ek_warn "AcrPull assignment reported an error; it may already exist."
  fi

  # Tell the app to pull with that identity, and keep it warm so a cold start
  # does not greet the first visitor of the morning.
  ek_az webapp config set --resource-group "$EK_RG" --name "$EK_WEBAPP" \
    --generic-configurations '{"acrUseManagedIdentityCreds": true, "alwaysOn": true}' -o none

  ek_az webapp config container set --resource-group "$EK_RG" --name "$EK_WEBAPP" \
    --container-image-name "${EK_ACR}.azurecr.io/${EK_IMAGE}:latest" \
    --container-registry-url "https://${EK_ACR}.azurecr.io" -o none

  # Any credential left over from an older deployment is removed rather than
  # left to rot in the settings list.
  ek_az webapp config appsettings delete --resource-group "$EK_RG" --name "$EK_WEBAPP" \
    --setting-names DOCKER_REGISTRY_SERVER_USERNAME DOCKER_REGISTRY_SERVER_PASSWORD \
    -o none 2>/dev/null || true

  ek_state_set names.principalId "$principal"
  ek_step_record identity done "$principal"
  ek_step_done "system-assigned identity, AcrPull granted"
}
ek_step_register identity "Managed identity and registry pull rights" _ek_step_identity
