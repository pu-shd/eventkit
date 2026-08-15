# The web app itself.

_ek_step_webapp() {
  if ek_az_exists webapp show --resource-group "$EK_RG" --name "$EK_WEBAPP" --query id -o tsv; then
    ek_dim "  ${EK_WEBAPP} exists"
  else
    ek_az webapp create --resource-group "$EK_RG" --plan "$EK_PLAN" --name "$EK_WEBAPP" \
      --deployment-container-image-name "${EK_ACR}.azurecr.io/${EK_IMAGE}:latest" -o none
  fi
  ek_state_set names.webApp "$EK_WEBAPP"
  ek_step_record webapp done "$(ek_az_query webapp show --resource-group "$EK_RG" --name "$EK_WEBAPP" --query id -o tsv)"
  ek_step_done "https://${EK_WEBAPP}.azurewebsites.net"
}
ek_step_register webapp "Web app" _ek_step_webapp
