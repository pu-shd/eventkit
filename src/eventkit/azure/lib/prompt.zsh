# Interactive prompts.
#
# Every answer is validated at the point of entry rather than at `az` call time,
# because the alternative is discovering that an ACR name was invalid after
# eleven other resources already exist.

# ek_ask <var> <question> [default] [validator]
ek_ask() {
  local var="$1" question="$2" default="${3:-}" validator="${4:-}"
  local current="${(P)var:-}"
  [[ -n "$current" ]] && default="$current"

  if (( EK_ASSUME_YES )) || (( EK_NO_REPROMPT )); then
    [[ -z "$default" ]] && ek_die "$question — no value, and running non-interactively."
    typeset -g "$var"="$default"
    return 0
  fi

  local answer
  while true; do
    if [[ -n "$default" ]]; then
      print -n -- "${EK_C_BOLD}?${EK_C_RESET} ${question} ${EK_C_GREY}[${default}]${EK_C_RESET} "
    else
      print -n -- "${EK_C_BOLD}?${EK_C_RESET} ${question} "
    fi
    read -r answer || answer=""
    [[ -z "$answer" ]] && answer="$default"

    if [[ -z "$answer" ]]; then
      ek_warn "A value is required."
      continue
    fi
    if [[ -n "$validator" ]] && ! "$validator" "$answer"; then
      continue
    fi
    typeset -g "$var"="$answer"
    return 0
  done
}

ek_confirm() {
  local question="$1" default="${2:-n}"
  (( EK_ASSUME_YES )) && return 0
  local hint="[y/N]"; [[ "$default" == "y" ]] && hint="[Y/n]"
  local answer
  print -n -- "${EK_C_BOLD}?${EK_C_RESET} ${question} ${EK_C_GREY}${hint}${EK_C_RESET} "
  read -r answer || answer=""
  [[ -z "$answer" ]] && answer="$default"
  [[ "${answer:l}" == y* ]]
}

# --- validators ------------------------------------------------------------
# Each prints why it rejected, so the operator can fix it without reading docs.

ek_valid_acr() {
  if [[ ! "$1" =~ '^[a-zA-Z0-9]{5,50}$' ]]; then
    ek_warn "A registry name is 5–50 characters, letters and digits only (no dashes). Got: $1"
    return 1
  fi
  return 0
}

ek_valid_webapp() {
  if [[ ! "$1" =~ '^[a-zA-Z0-9][a-zA-Z0-9-]{1,58}[a-zA-Z0-9]$' ]]; then
    ek_warn "A web app name is 2–60 characters of letters, digits and dashes, not starting or ending with a dash."
    return 1
  fi
  return 0
}

ek_valid_rg() {
  if [[ ! "$1" =~ '^[a-zA-Z0-9._()-]{1,90}$' ]]; then
    ek_warn "A resource group name is up to 90 characters of letters, digits, and . _ ( ) -"
    return 1
  fi
  return 0
}

ek_valid_emails() {
  local entry
  for entry in ${(s:,:)1}; do
    entry="${entry## }"; entry="${entry%% }"
    [[ -z "$entry" ]] && continue
    if [[ ! "$entry" =~ '^(@[^@[:space:]]+\.[^@[:space:]]+|[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+)$' ]]; then
      ek_warn "Not an address or an @domain rule: $entry"
      return 1
    fi
  done
  return 0
}

ek_valid_location() {
  [[ "$1" =~ '^[a-z][a-z0-9]+$' ]] || { ek_warn "A location looks like 'eastus' or 'westeurope'."; return 1; }
  return 0
}
