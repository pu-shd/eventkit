# Persistent storage for the SQLite default.
#
# WEBSITES_ENABLE_APP_SERVICE_STORAGE mounts /home across restarts, which is
# where the database file lives. Without it the database is in the container
# filesystem and disappears on every deploy — silently, with an empty roster as
# the only symptom.

_ek_step_storage() {
  if ! ek_conf_bool needs_db; then
    ek_step_record storage skipped "" "app has no database"
    ek_step_done "not needed"
    return 0
  fi
  if [[ "$EK_DB_KIND" != "sqlite" ]]; then
    ek_step_record storage skipped "" "using ${EK_DB_KIND}"
    ek_step_done "not needed for ${EK_DB_KIND}"
    return 0
  fi
  ek_az webapp config appsettings set --resource-group "$EK_RG" --name "$EK_WEBAPP" \
    --settings WEBSITES_ENABLE_APP_SERVICE_STORAGE=true -o none
  ek_state_set datastore.kind sqlite
  ek_state_set datastore.path "/home/${EK_APP}.db"
  ek_step_record storage done
  ek_step_done "/home/${EK_APP}.db"
}
ek_step_register storage "Persistent storage for SQLite" _ek_step_storage
