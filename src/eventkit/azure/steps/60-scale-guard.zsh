# Keep a SQLite deployment single-instance.
#
# SQLite lives on an SMB share here. WAL needs a shared-memory mmap that SMB does
# not provide, so eventkit runs it in TRUNCATE mode with a busy timeout — which
# is correct for one writer and corrupts nothing, but two App Service instances
# writing the same file over SMB is a different proposition. So the toolkit pins
# the instance count and refuses to leave autoscale configured.

_ek_step_scale_guard() {
  if [[ "$EK_DB_KIND" != "sqlite" ]]; then
    ek_step_record scale-guard skipped "" "using ${EK_DB_KIND}"
    ek_step_done "not needed for ${EK_DB_KIND}"
    return 0
  fi

  ek_az appservice plan update --resource-group "$EK_RG" --name "$EK_PLAN" \
    --number-of-workers 1 -o none 2>/dev/null || \
    ek_warn "Could not pin the worker count; check that the plan is not autoscaled."

  local autoscale
  autoscale="$(ek_az_query monitor autoscale list --resource-group "$EK_RG" \
    --query "[?contains(targetResourceUri, '${EK_PLAN}')].name" -o tsv)"
  if [[ -n "$autoscale" ]]; then
    ek_err "Autoscale is configured on ${EK_PLAN}, and this app stores its data in SQLite on a shared file."
    ek_err "Two instances writing that file is not safe. Remove the autoscale rule, or move to Postgres:"
    ek_dim "  eventkit azure deploy --app ${EK_APP} --postgres"
    ek_step_record scale-guard failed "" "autoscale present"
    return 1
  fi

  ek_step_record scale-guard done
  ek_step_done "single instance"
}
ek_step_register scale-guard "Guard against multi-instance SQLite" _ek_step_scale_guard
