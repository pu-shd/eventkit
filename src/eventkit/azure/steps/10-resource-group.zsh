# One resource group per event, holding every application for that event.
#
# Per event rather than per app because the whole thing is torn down together
# after the conference, and a single `az group delete` is the operation people
# actually want at that point.

_ek_step_resource_group() {
  if ek_az_exists group show --name "$EK_RG" --query id -o tsv; then
    ek_dim "  ${EK_RG} exists"
  else
    ek_az group create --name "$EK_RG" --location "$EK_LOCATION" \
      --tags "eventkit=1" "event=${EK_EVENT}" -o none
  fi
  ek_step_record resource-group done "$(ek_az_query group show --name "$EK_RG" --query id -o tsv)"
  ek_step_done "$EK_RG"
}
ek_step_register resource-group "Resource group" _ek_step_resource_group
