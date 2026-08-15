# Tools, login, and the subscription. Nothing is created here.

_ek_step_preflight() {
  ek_az_version_check
  ek_require_login

  local account
  account="$(ek_az_account)"
  local sub_name sub_id tenant
  sub_name="$(print -r -- "$account" | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')"
  sub_id="$(print -r -- "$account" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
  tenant="$(print -r -- "$account" | python3 -c 'import json,sys; print(json.load(sys.stdin)["tenant"])')"

  ek_dim "subscription: ${sub_name} (${sub_id})"
  if ! (( EK_ASSUME_YES )); then
    ek_confirm "Deploy into this subscription?" y || ek_die "Pick another with: az account set --subscription <name>"
  fi

  ek_state_set azure.subscriptionId "$sub_id"
  ek_state_set azure.tenantId "$tenant"
  ek_state_set azure.location "$EK_LOCATION"
  typeset -g EK_SUBSCRIPTION="$sub_id"
  typeset -g EK_TENANT="$tenant"

  ek_step_record preflight done
  ek_step_done
}
ek_step_register preflight "Check tooling and confirm the subscription" _ek_step_preflight
