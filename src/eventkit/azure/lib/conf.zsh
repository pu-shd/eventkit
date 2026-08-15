# The per-app contract.
#
# `deploy/app.conf` in each application repository declares what that app needs.
# The toolkit reads it and generates the whole flow, so adding a sixth
# application is a config file rather than a new script — which is what the two
# predecessor repositories got wrong, ending up with 1,475 lines of 70%-shared
# shell that had already drifted apart.
#
# TOML is parsed with Python's tomllib rather than by hand in zsh.

typeset -g EK_CONF_FILE=""
typeset -gA EK_CONF=()
typeset -ga EK_CONF_SETTINGS=()
typeset -ga EK_CONF_GATES=()

ek_conf_find() {
  local app="$1" candidate
  for candidate in "deploy/app.conf" "${app}/deploy/app.conf" "../${app}/deploy/app.conf"; do
    [[ -f "$candidate" ]] && { print -r -- "$candidate"; return 0; }
  done
  return 1
}

ek_conf_load() {
  local file="$1"
  [[ -f "$file" ]] || ek_die "No app.conf at ${file}. Run this from an application repository, or pass --conf."
  EK_CONF_FILE="$file"

  local dumped
  dumped="$(python3 -c '
import sys, tomllib
with open(sys.argv[1], "rb") as fh:
    doc = tomllib.load(fh)
scalars = {k: v for k, v in doc.items() if not isinstance(v, (list, dict))}
for key, value in scalars.items():
    if isinstance(value, bool):
        value = "true" if value else "false"
    print(f"SCALAR\t{key}\t{value}")
for gate in doc.get("gates", []) or []:
    print(f"GATE\t{gate}")
for setting in doc.get("setting", []) or []:
    print("SETTING\t" + "\t".join([
        str(setting.get("name", "")),
        str(setting.get("type", "string")),
        "true" if setting.get("required") else "false",
        str(setting.get("default", "")),
        str(setting.get("generate", "")),
        str(setting.get("value", "")),
        str(setting.get("prompt", "")),
    ]))
' "$file")" || ek_die "Could not parse ${file}."

  local line kind rest
  while IFS= read -r line; do
    kind="${line%%$'\t'*}"
    rest="${line#*$'\t'}"
    case "$kind" in
      SCALAR)  EK_CONF[${rest%%$'\t'*}]="${rest#*$'\t'}" ;;
      GATE)    EK_CONF_GATES+=("$rest") ;;
      SETTING) EK_CONF_SETTINGS+=("$rest") ;;
    esac
  done <<< "$dumped"

  [[ -n "${EK_CONF[name]:-}" ]] || ek_die "${file} declares no name."
}

ek_conf_get() { print -r -- "${EK_CONF[$1]:-${2:-}}"; }
ek_conf_bool() { [[ "$(ek_conf_get "$1" "${2:-false}")" == "true" ]]; }

# Iterate settings as: name type required default generate value prompt
ek_conf_each_setting() {
  local handler="$1" entry
  for entry in "${EK_CONF_SETTINGS[@]}"; do
    local -a f
    f=("${(@s:	:)entry}")
    # Trailing empty fields may be absent entirely; no_unset would abort on them.
    while (( ${#f} < 7 )); do f+=(""); done
    "$handler" "${f[1]}" "${f[2]}" "${f[3]}" "${f[4]}" "${f[5]}" "${f[6]}" "${f[7]}"
  done
}
