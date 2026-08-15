# The `az` wrapper.
#
# Every Azure call goes through here so that --dry-run, logging and failure
# reporting happen once. `az` is also version-sensitive in ways that have bitten
# this project — authV2 is an extension whose surface has changed, and
# `az webapp config ssl create` is marked preview — so the version is checked
# here and recorded in the ledger.

typeset -g EK_AZ_MIN="2.60.0"
typeset -g EK_AZ_TESTED_MAX="2.99.0"

ek_az_version() {
  az version --query '"azure-cli"' -o tsv 2>/dev/null
}

ek_az_version_check() {
  local version
  version="$(ek_az_version)" || { ek_warn "Could not determine the az version."; return 0; }
  if ! ek_semver_ge "$version" "$EK_AZ_MIN"; then
    ek_die "az $version is older than the supported minimum $EK_AZ_MIN."
  fi
  if ek_semver_ge "$version" "$EK_AZ_TESTED_MAX"; then
    ek_warn "az $version is newer than the tested maximum $EK_AZ_TESTED_MAX. If a command behaves oddly, that is the first thing to suspect."
  fi
  ek_dim "az $version"
}

ek_semver_ge() {
  local -a a b
  a=(${(s:.:)1}) b=(${(s:.:)2})
  local i
  for i in 1 2 3; do
    local x="${a[$i]:-0}" y="${b[$i]:-0}"
    x="${x%%[^0-9]*}"; y="${y%%[^0-9]*}"
    (( ${x:-0} > ${y:-0} )) && return 0
    (( ${x:-0} < ${y:-0} )) && return 1
  done
  return 0
}

# ek_az <args...> — run, or print under --dry-run.
ek_az() {
  if (( EK_DRY_RUN )); then
    print -r -- "  ${EK_C_GREY}dry-run:${EK_C_RESET} az ${(j: :)@}"
    return 0
  fi
  (( EK_VERBOSE )) && ek_dim "az ${(j: :)@}"
  az "$@"
}

# Like ek_az but returns the output and tolerates a non-zero exit, for the
# "does this already exist?" questions that make the toolkit idempotent.
ek_az_query() {
  az "$@" 2>/dev/null || true
}

ek_az_exists() {
  local result
  result="$(ek_az_query "$@")"
  [[ -n "$result" && "$result" != "null" && "$result" != "[]" ]]
}

ek_az_account() {
  az account show --query "{id:id, name:name, tenant:tenantId}" -o json 2>/dev/null
}

ek_require_login() {
  if ! az account show >/dev/null 2>&1; then
    ek_err "Not signed in to Azure."
    ek_blank
    print -r -- "  Run this, then try again:"
    print -r -- "    ${EK_C_BOLD}az login${EK_C_RESET}"
    exit 1
  fi
}
