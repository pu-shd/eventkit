# Postgres, only when asked for.
#
# Two things the predecessor got wrong are fixed here. It created the server
# with `--public-access 0.0.0.0`, which opens it to every Azure IP for the
# window between creation and the narrowing that follows; `None` creates it with
# no rule at all. And it never revisited the firewall, so a plan scale — which
# changes the outbound IPs — silently made the database unreachable.

_ek_step_database() {
  if ! ek_conf_bool needs_db || [[ "$EK_DB_KIND" != "postgres" ]]; then
    ek_step_record database skipped "" "not requested"
    ek_step_done "not requested"
    return 0
  fi

  if ! ek_verify_provider_registered Microsoft.DBforPostgreSQL; then
    ek_info "Registering the Microsoft.DBforPostgreSQL provider; this takes a few minutes."
    ek_az provider register --namespace Microsoft.DBforPostgreSQL -o none
    ek_await_manual_step --id provider-registered \
      --title "Microsoft.DBforPostgreSQL provider registration" \
      --verify ek_verify_provider_registered \
      --checklist "Nothing to do — registration is running.|It usually completes in two to five minutes." \
      || return 1
  fi

  if ! ek_az_exists postgres flexible-server show --resource-group "$EK_RG" --name "$EK_DB_SERVER" --query id -o tsv; then
    local password
    password="$(ek_env_get DB_PASSWORD)"
    [[ -z "$password" ]] && { password="$(ek_gen_secret)"; ek_env_save DB_PASSWORD "$password"; }
    ek_env_save DB_USER "${EK_DB_USER}"

    # --public-access None: created with no firewall rule at all, rather than
    # open to all of Azure and narrowed a moment later.
    ek_az postgres flexible-server create --resource-group "$EK_RG" --name "$EK_DB_SERVER" \
      --location "$EK_LOCATION" --admin-user "$EK_DB_USER" --admin-password "$password" \
      --sku-name Standard_B1ms --tier Burstable --version 16 \
      --public-access None --yes -o none
    ek_az postgres flexible-server db create --resource-group "$EK_RG" \
      --server-name "$EK_DB_SERVER" --database-name "$EK_APP" -o none
  fi

  _ek_sync_db_firewall
  ek_state_set datastore.kind postgres
  ek_state_set names.dbServer "$EK_DB_SERVER"
  ek_step_record database done "$EK_DB_SERVER"
  ek_step_done "$EK_DB_SERVER"
}

# Outbound IPs change when the plan is scaled or moved. `drift` re-runs this,
# because the failure mode is an outage rather than a report.
_ek_sync_db_firewall() {
  local ips
  ips="$(ek_az_query webapp show --resource-group "$EK_RG" --name "$EK_WEBAPP" \
    --query possibleOutboundIpAddresses -o tsv)"
  [[ -z "$ips" ]] && return 0
  local ip n=0
  for ip in ${(s:,:)ips}; do
    n=$(( n + 1 ))
    ek_az postgres flexible-server firewall-rule create --resource-group "$EK_RG" \
      --name "$EK_DB_SERVER" --rule-name "appservice-${n}" \
      --start-ip-address "$ip" --end-ip-address "$ip" -o none 2>/dev/null || true
  done
  ek_dim "  ${n} outbound IP rule(s)"
}
ek_step_register database "Database (Postgres only when requested)" _ek_step_database
