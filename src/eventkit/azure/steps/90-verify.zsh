# Prove the thing works before saying it does.
#
# A deploy workflow can go green while the container fails to boot: the image
# ships fine and only then crashes on a missing setting. So the last step asks
# the app.

_ek_step_verify() {
  if (( EK_DRY_RUN )); then
    ek_step_done "skipped under --dry-run"
    return 0
  fi

  local url="https://${EK_WEBAPP}.azurewebsites.net"
  ek_info "  waiting for ${url}/healthz"

  local budget="${EK_VERIFY_TIMEOUT_SECONDS:-180}"
  local interval="${EK_VERIFY_INTERVAL_SECONDS:-10}"
  local waited=0
  while (( waited < budget )); do
    if ek_verify_app_responds; then
      ek_ok "  healthy after ${waited}s"
      break
    fi
    sleep "$interval"
    waited=$(( waited + interval ))
  done

  if (( waited >= budget )); then
    ek_warn "  ${url}/healthz did not answer within ${budget}s."
    ek_dim  "  A first boot pulls the image and runs migrations, so this can be slow."
    ek_dim  "  Logs: eventkit azure logs --app ${EK_APP}"
    ek_step_record verify failed "" "healthz timeout"
    return 0
  fi

  # An admin route answering 200 to nobody is the failure this whole toolkit
  # exists to prevent, so check it explicitly rather than trusting the gate.
  if ek_conf_bool easy_auth || [[ "$(ek_conf_get easy_auth)" == "admin-routes-only" ]]; then
    local code
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "${url}/api/webhook/status" 2>/dev/null)"
    if [[ "$code" == "200" ]]; then
      ek_err "  /api/webhook/status answered 200 to an unauthenticated request."
      ek_err "  Easy Auth is not protecting this app. Do not put real data in it."
      ek_step_record verify failed "" "admin route is public"
      return 0
    fi
    ek_ok "  admin routes refuse anonymous callers (${code})"
  fi

  ek_step_record verify done
  ek_step_done
}
ek_step_register verify "Verify the deployment answers" _ek_step_verify
