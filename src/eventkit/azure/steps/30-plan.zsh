# One App Service plan per event, shared by that event's applications.
#
# A B1 hosts several small apps comfortably. Five plans for a one-week event is
# money spent on nothing.

_ek_step_plan() {
  if ek_az_exists appservice plan show --resource-group "$EK_RG" --name "$EK_PLAN" --query id -o tsv; then
    ek_dim "  ${EK_PLAN} exists"
  else
    ek_az appservice plan create --resource-group "$EK_RG" --name "$EK_PLAN" \
      --is-linux --sku "${EK_PLAN_SKU:-B1}" -o none
  fi
  ek_step_record plan done "$(ek_az_query appservice plan show --resource-group "$EK_RG" --name "$EK_PLAN" --query id -o tsv)"
  ek_step_done "${EK_PLAN} (${EK_PLAN_SKU:-B1})"
}
ek_step_register plan "App Service plan" _ek_step_plan
