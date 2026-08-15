# GitHub, for the CI half of the deployment.
#
# Variables rather than secrets wherever the value is not sensitive: a client id
# and a tenant id are identifiers, not credentials, and putting them in
# variables makes them readable in a failed run's logs where secrets are masked
# into uselessness.

ek_gh_available() { command -v gh >/dev/null 2>&1; }

ek_gh_repo() {
  local remote
  remote="$(git remote get-url origin 2>/dev/null)" || return 1
  print -r -- "$remote" | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##'
}

ek_gh_var_set() {
  local repo="$1" key="$2" value="$3"
  (( EK_DRY_RUN )) && { print -r -- "  ${EK_C_GREY}dry-run:${EK_C_RESET} gh variable set ${key}"; return 0; }
  gh variable set "$key" --repo "$repo" --body "$value" >/dev/null
}

ek_gh_secret_set() {
  local repo="$1" key="$2" value="$3"
  (( EK_DRY_RUN )) && { print -r -- "  ${EK_C_GREY}dry-run:${EK_C_RESET} gh secret set ${key}"; return 0; }
  gh secret set "$key" --repo "$repo" --body "$value" >/dev/null
}
