# The manual-step gate.
#
# This is the reason the toolkit exists. The predecessor scripts *printed*
# instructions for the steps a human has to do in a portal — configuring the
# Entra ID identity provider, adding a DNS record — and then exited or carried
# on regardless. One of those steps was never scripted or documented at all, and
# the deployment worked only because somebody did it by hand once.
#
# So: show the checklist, open the right portal blade, then **poll** until the
# thing is actually true, and carry on the moment it is.

typeset -g EK_GATE_POLL_SECONDS="${EK_GATE_POLL_SECONDS:-10}"
typeset -g EK_GATE_TIMEOUT_SECONDS="${EK_GATE_TIMEOUT_SECONDS:-1800}"

_ek_open_url() {
  local url="$1"
  if command -v open >/dev/null 2>&1; then open "$url" >/dev/null 2>&1
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$url" >/dev/null 2>&1
  else ek_dim "Open this yourself: $url"
  fi
}

# ek_await_manual_step --id X --title T --verify FN [--portal URL]
#                      [--risk critical|normal] [--checklist "a|b|c"]
#
# Returns 0 when the predicate passes or the operator skips, 1 on quit/timeout.
ek_await_manual_step() {
  local id="" title="" verify="" portal="" risk="normal" checklist=""
  while (( $# )); do
    case "$1" in
      --id)        shift; id="$1" ;;
      --title)     shift; title="$1" ;;
      --verify)    shift; verify="$1" ;;
      --portal)    shift; portal="$1" ;;
      --risk)      shift; risk="$1" ;;
      --checklist) shift; checklist="$1" ;;
      *) ek_die "ek_await_manual_step: unknown option $1" ;;
    esac
    shift
  done

  # Already satisfied? Then there is nothing to ask anybody.
  if "$verify" 2>/dev/null; then
    ek_step_done "already configured"
    ek_step_record "$id" done
    return 0
  fi

  # An acknowledged exception stays acknowledged until its date.
  local ack_until
  ack_until="$(ek_state_get "" "")"
  if _ek_gate_acked "$id"; then
    ek_warn "$title — skipped by an acknowledgement that has not expired."
    return 0
  fi

  ek_blank
  print -r -- "  ${EK_C_YELLOW}${EK_C_BOLD}This step needs you.${EK_C_RESET} ${title}"
  ek_blank
  local -a items
  items=(${(s:|:)checklist})
  local n=1 item
  for item in $items; do
    print -r -- "    ${EK_C_BOLD}${n}.${EK_C_RESET} ${item}"
    n=$(( n + 1 ))
  done
  [[ -n "$portal" ]] && { ek_blank; print -r -- "    ${EK_C_BLUE}${portal}${EK_C_RESET}"; }
  ek_blank

  # A dry run describes what would happen; it does not block on something nobody
  # is being asked to do yet.
  if (( EK_DRY_RUN )); then
    ek_dim "  dry-run: would wait here for ${id}"
    return 0
  fi

  # Non-interactive: fail fast with the checklist rather than hanging a CI job
  # forever waiting for a human who is not there.
  if (( EK_ASSUME_YES )); then
    ek_err "$title is not done, and this run is non-interactive."
    ek_dim "Do the steps above, then: eventkit azure resume --app ${EK_APP:-<app>}"
    ek_step_record "$id" pending "" "blocked: non-interactive"
    return 1
  fi

  print -r -- "  ${EK_C_GREY}waiting — [s]kip  [r]etry now  [o]pen portal  [q]uit and resume later${EK_C_RESET}"

  local -a frames
  frames=(${(z)$(ek_spinner_frames)})
  local started=$SECONDS frame=1 waited=0 key=""

  while true; do
    # Poll on the interval, but keep the keyboard responsive every second.
    local tick=0
    while (( tick < EK_GATE_POLL_SECONDS )); do
      waited=$(( SECONDS - started ))
      printf "\r  %s waiting for %s — %ds elapsed  " \
        "${frames[$frame]}" "$id" "$waited"
      frame=$(( frame % ${#frames} + 1 ))

      key=""
      read -t 1 -k 1 key 2>/dev/null || true
      case "${key:l}" in
        s) printf "\r%-72s\r" ""
           ek_warn "Skipped ${id}."
           if [[ "$risk" == "critical" ]]; then
             ek_warn "This gate is marked critical. It will be reported by 'drift' until it is done or acknowledged:"
             ek_dim  "  eventkit azure gate ack ${id} --until YYYY-MM-DD --reason '<ticket>'"
           fi
           ek_step_record "$id" skipped "" "risk=${risk}"
           return 0 ;;
        o) _ek_open_url "$portal"; continue ;;
        q) printf "\r%-72s\r" ""
           ek_info "Stopped at ${id}. Nothing already done will be repeated."
           ek_dim  "  eventkit azure resume --app ${EK_APP:-<app>}"
           ek_step_record "$id" pending "" "operator quit"
           return 1 ;;
        r) break ;;
      esac
      tick=$(( tick + 1 ))
    done

    if "$verify" 2>/dev/null; then
      printf "\r%-72s\r" ""
      ek_ok "${title} — detected after ${waited}s."
      ek_step_record "$id" done
      return 0
    fi

    if (( waited >= EK_GATE_TIMEOUT_SECONDS )); then
      printf "\r%-72s\r" ""
      ek_warn "Gave up waiting for ${id} after ${waited}s."
      ek_dim  "Nothing is lost. Finish the steps above, then: eventkit azure resume --app ${EK_APP:-<app>}"
      ek_step_record "$id" pending "" "timed out"
      return 1
    fi
  done
}

_ek_gate_acked() {
  python3 -c '
import datetime, json, sys
path, sid = sys.argv[1], sys.argv[2]
try:
    with open(path) as fh:
        doc = json.load(fh)
except FileNotFoundError:
    raise SystemExit(1)
for step in doc.get("steps", []):
    if step.get("id") == sid and step.get("ackUntil"):
        try:
            until = datetime.date.fromisoformat(step["ackUntil"])
        except ValueError:
            raise SystemExit(1)
        raise SystemExit(0 if until >= datetime.date.today() else 1)
raise SystemExit(1)
' "$EK_STATE_FILE" "$1"
}
