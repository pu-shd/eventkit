# One container registry per event.
#
# `--admin-enabled false` from the start: the admin account is a username and
# password that would then have to live somewhere, and the whole point of the
# managed identity below is that no registry credential exists at all.

_ek_step_registry() {
  if ek_az_exists acr show --name "$EK_ACR" --query id -o tsv; then
    ek_dim "  ${EK_ACR} exists"
  else
    ek_az acr create --resource-group "$EK_RG" --name "$EK_ACR" \
      --sku Basic --admin-enabled false -o none
  fi
  # Enforce it even on a registry somebody else created.
  ek_az acr update --name "$EK_ACR" --admin-enabled false -o none
  ek_step_record registry done "$(ek_az_query acr show --name "$EK_ACR" --query id -o tsv)"
  ek_step_done "$EK_ACR"
}
ek_step_register registry "Container registry (no admin account)" _ek_step_registry
