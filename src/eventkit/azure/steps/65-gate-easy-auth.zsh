# The gate that matters most.
#
# Every admin surface in the stack is gated on the X-MS-CLIENT-PRINCIPAL-NAME
# header that Easy Auth injects. Provisioning cannot create the identity
# provider for you — it needs an app registration in your tenant, which is
# usually somebody else's to approve — so this is where a human takes over.
#
# The predecessor never scripted it *and never documented it*. Its deployment
# worked because somebody configured it by hand once, years ago.

_ek_step_gate_easy_auth() {
  if ! ek_conf_bool easy_auth && [[ "$(ek_conf_get easy_auth)" != "admin-routes-only" ]]; then
    ek_step_record easy-auth skipped "" "app declares no admin surface"
    ek_step_done "not required by this app"
    return 0
  fi

  local portal="https://portal.azure.com/#@${EK_TENANT}/resource/subscriptions/${EK_SUBSCRIPTION}/resourceGroups/${EK_RG}/providers/Microsoft.Web/sites/${EK_WEBAPP}/authentication"

  ek_await_manual_step \
    --id easy-auth \
    --risk critical \
    --title "Entra ID authentication for ${EK_WEBAPP}" \
    --verify ek_verify_easy_auth \
    --portal "$portal" \
    --checklist "Open the portal link below (or press [o]).|Choose 'Add identity provider' → Microsoft.|Pick 'Workforce configuration' and let it create a new app registration.|Set 'Restrict access' to 'Require authentication'.|Set 'Unauthenticated requests' to 'HTTP 302 Found redirect'.|Save. This page will notice within ${EK_GATE_POLL_SECONDS}s." \
    || return 1

  if ! ek_verify_auth_enabled; then
    ek_warn "An identity provider is configured but authentication is not switched on."
    ek_warn "Anyone can reach the admin routes until it is."
  fi
  return 0
}
ek_step_register easy-auth "Easy Auth (manual — needs your tenant)" _ek_step_gate_easy_auth
