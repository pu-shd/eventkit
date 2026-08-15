# Secret generation and app settings.
#
# The toolkit is the **only** writer of app settings. In the predecessors they
# were written in two places — the provisioning script and the deploy workflow —
# and had already drifted three ways on the admin allow-list alone: seven
# addresses in the app's config default, seven in the workflow, four in the
# script. CI here only ships images.

typeset -g EK_ENV_FILE="${EK_ENV_FILE:-.env.deploy}"

# A generator that silently produces nothing is worse than one that fails: the
# caller would fall through to "prompt the operator", which under --yes aborts
# the deploy with a misleading message. So the output is checked.
ek_gen_secret() {
  local out=""
  if command -v openssl >/dev/null 2>&1; then
    out="$(openssl rand -hex 32 2>/dev/null || true)"
  fi
  if [[ ! "$out" =~ '^[0-9a-f]{64}$' ]]; then
    out="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  fi
  [[ "$out" =~ '^[0-9a-f]{64}$' ]] || ek_die "Could not generate a secret: neither openssl nor python3 produced 64 hex characters."
  print -r -- "$out"
}

# Local, gitignored working values. The ledger is committed; this is not.
ek_env_load() {
  [[ -f "$EK_ENV_FILE" ]] || return 0
  local line key value
  while IFS= read -r line; do
    [[ "$line" == \#* || -z "$line" ]] && continue
    key="${line%%=*}"; value="${line#*=}"
    typeset -g "EKV_${key}"="${value}"
  done < "$EK_ENV_FILE"
}

ek_env_save() {
  local key="$1" value="$2"
  touch "$EK_ENV_FILE"
  chmod 600 "$EK_ENV_FILE" 2>/dev/null || true
  if grep -q "^${key}=" "$EK_ENV_FILE" 2>/dev/null; then
    local tmp="${EK_ENV_FILE}.tmp"
    grep -v "^${key}=" "$EK_ENV_FILE" > "$tmp"
    print -r -- "${key}=${value}" >> "$tmp"
    mv "$tmp" "$EK_ENV_FILE"
  else
    print -r -- "${key}=${value}" >> "$EK_ENV_FILE"
  fi
  typeset -g "EKV_${key}"="${value}"
}

ek_env_get() {
  local key="$1"
  print -r -- "${(P)$(print -r -- "EKV_${key}"):-}"
}

# Read a setting back from the deployed app. App Service is the source of truth
# for secrets, so `resume` can carry on without them being in the ledger.
ek_appsetting_get() {
  ek_az_query webapp config appsettings list \
    --resource-group "$EK_RG" --name "$EK_WEBAPP" \
    --query "[?name=='$1'].value" -o tsv
}

ek_appsettings_apply() {
  local -a pairs=("$@")
  (( ${#pairs} )) || return 0
  ek_az webapp config appsettings set \
    --resource-group "$EK_RG" --name "$EK_WEBAPP" \
    --settings "${pairs[@]}" -o none
}
