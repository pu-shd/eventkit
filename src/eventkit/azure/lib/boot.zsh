# Common preamble. Sourced first by every entry point.

emulate -L zsh
setopt err_return no_unset pipe_fail

: "${EVENTKIT_AZURE_LIB:?EVENTKIT_AZURE_LIB must point at the toolkit's lib directory}"

typeset -g EK_ROOT="${EVENTKIT_AZURE_LIB:h}"
typeset -g EK_DRY_RUN=0
typeset -g EK_ASSUME_YES=0
typeset -g EK_NO_REPROMPT=0
typeset -g EK_VERBOSE=0

for _ek_lib in color log prompt state name az gh secrets verify manual conf steps; do
  source "${EVENTKIT_AZURE_LIB}/${_ek_lib}.zsh"
done
unset _ek_lib

ek_color_init

# Parse the flags every verb accepts. Leaves the rest in EK_ARGS.
ek_parse_common_flags() {
  typeset -ga EK_ARGS=()
  while (( $# )); do
    case "$1" in
      --dry-run)         EK_DRY_RUN=1 ;;
      --yes|-y)          EK_ASSUME_YES=1 ;;
      --non-interactive) EK_ASSUME_YES=1 ;;
      --no-reprompt)     EK_NO_REPROMPT=1 ;;
      --verbose|-v)      EK_VERBOSE=1 ;;
      --app)             shift; EK_APP="$1" ;;
      --app=*)           EK_APP="${1#*=}" ;;
      --event)           shift; EK_EVENT="$1" ;;
      --event=*)         EK_EVENT="${1#*=}" ;;
      --state)           shift; EK_STATE_FILE="$1" ;;
      --state=*)         EK_STATE_FILE="${1#*=}" ;;
      *)                 EK_ARGS+=("$1") ;;
    esac
    shift
  done
}
