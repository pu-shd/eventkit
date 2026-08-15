# Wait for Drupal to actually be wired up.
#
# The Remote Post handler is created in Drupal's admin UI, which the toolkit
# cannot reach. The only honest confirmation is the application reporting that
# it has received an authenticated submission — so that is what is polled.
# /api/webhook/status returns counters and timestamps only, no attendee data.

_ek_step_gate_webhook() {
  if [[ -z "${EK_CONF_GATES[(r)webhook]:-}" ]]; then
    ek_step_record webhook skipped "" "app declares no webhook gate"
    ek_step_done "not required by this app"
    return 0
  fi

  typeset -g EK_WEBHOOK_TOKEN="$(ek_env_get DRUPAL_WEBHOOK_TOKEN)"
  local url="https://${EK_WEBAPP}.azurewebsites.net"

  ek_blank
  ek_info "Paste this into the Drupal handler's ${EK_C_BOLD}Custom options${EK_C_RESET}:"
  print -r -- ""
  print -r -- "    headers:"
  print -r -- "      X-Drupal-Webhook-Token: '${EK_WEBHOOK_TOKEN:-<set DRUPAL_WEBHOOK_TOKEN first>}'"
  print -r -- ""

  ek_await_manual_step \
    --id webhook \
    --title "Drupal Remote Post handler" \
    --verify ek_verify_webhook_received \
    --portal "${EK_EVENT_SITE:-https://your-drupal-site}/admin/structure/webform" \
    --checklist \
"Open your registration webform → Handlers → Add handler → Remote Post.|Completed URL and Updated URL: ${url}/api/drupal-webhook|Method POST, Post type JSON.|Custom options: the headers: block printed above. The nesting matters — a flat key is ignored by Guzzle and every call 403s.|Leave the Error message blank so a failure never surfaces to a registrant.|Save, then submit one test registration." \
    || return 1
}
ek_step_register webhook "Drupal Remote Post handler (manual — needs Drupal)" _ek_step_gate_webhook
