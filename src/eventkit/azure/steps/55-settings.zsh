# Application settings — written here and nowhere else.
#
# Each setting is declared in the app's deploy/app.conf with a type, so the
# toolkit knows whether to prompt, generate, compute or fix it. Secrets are
# generated locally, stored in the gitignored .env.deploy, and pushed to App
# Service; they never touch the ledger.

_ek_setting_pairs=()

_ek_collect_setting() {
  local name="$1" kind="$2" required="$3" default="$4" generate="$5" fixed="$6" prompt="$7"
  local value=""

  case "$kind" in
    fixed)
      value="$fixed" ;;
    computed)
      case "$name" in
        DATABASE_URL)
          if [[ "$EK_DB_KIND" == "postgres" ]]; then
            local password; password="$(ek_env_get DB_PASSWORD)"
            value="postgresql+psycopg://${EK_DB_USER}:${password}@${EK_DB_SERVER}.postgres.database.azure.com:5432/${EK_APP}?sslmode=require"
          else
            value="sqlite:////home/${EK_APP}.db"
          fi ;;
        *) value="" ;;
      esac ;;
    secret)
      value="$(ek_env_get "$name")"
      if [[ -z "$value" ]]; then
        # Prefer whatever the live app already has, so re-running does not
        # rotate a secret behind the operator's back.
        value="$(ek_appsetting_get "$name")"
      fi
      if [[ -z "$value" && "$generate" == "hex32" ]]; then
        value="$(ek_gen_secret)"
        ek_info "  generated ${name}"
      fi
      if [[ -z "$value" && "$required" == "true" ]]; then
        ek_ask value "${prompt:-Value for ${name}}"
      fi
      [[ -n "$value" ]] && ek_env_save "$name" "$value" ;;
    bool|int|string|list|choice)
      value="$(ek_env_get "$name")"
      [[ -z "$value" ]] && value="$(ek_appsetting_get "$name")"
      if [[ -z "$value" ]]; then
        if [[ "$required" == "true" ]]; then
          local validator=""
          [[ "$name" == *PRINCIPALS* ]] && validator=ek_valid_emails
          ek_ask value "${prompt:-Value for ${name}}" "$default" "$validator"
        else
          value="$default"
        fi
      fi
      [[ -n "$value" ]] && ek_env_save "$name" "$value" ;;
  esac

  [[ -n "$value" ]] && _ek_setting_pairs+=("${name}=${value}")
}

_ek_step_settings() {
  _ek_setting_pairs=()
  ek_conf_each_setting _ek_collect_setting

  # Always present, regardless of what the app declares. The start-time limit
  # matters because App Service kills a container that has not answered in 230
  # seconds, and a first boot that runs migrations can exceed that.
  _ek_setting_pairs+=("WEBSITES_PORT=8000")
  _ek_setting_pairs+=("WEBSITES_CONTAINER_START_TIME_LIMIT=600")
  _ek_setting_pairs+=("EVENT_PROFILE=/home/site/event-profile.yaml")

  ek_appsettings_apply "${_ek_setting_pairs[@]}"
  ek_dim "  ${#_ek_setting_pairs} setting(s)"
  ek_step_record settings done
  ek_step_done
}
ek_step_register settings "Application settings" _ek_step_settings
