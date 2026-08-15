# The step engine.
#
# Every unit of provisioning is a step with an id, a description, and a function.
# The engine consults the ledger, skips what is already `done`, and records the
# outcome — which is the whole of what makes `deploy` idempotent and `resume`
# work. There is no separate resume code path, so resume is exercised on every
# run and cannot rot.

typeset -ga EK_STEPS=()

# ek_step_register <id> <description> <function>
ek_step_register() {
  EK_STEPS+=("$1"$'\t'"$2"$'\t'"$3")
}

ek_steps_load() {
  local file
  for file in "${EK_ROOT}"/steps/*.zsh(N); do
    source "$file"
  done
  EK_STEP_TOTAL=${#EK_STEPS}
}

# Run every registered step in order.
ek_steps_run() {
  local entry id desc fn state rc
  for entry in "${EK_STEPS[@]}"; do
    id="${entry%%$'\t'*}"
    desc="${${entry#*$'\t'}%%$'\t'*}"
    fn="${entry##*$'\t'}"

    state="$(ek_step_status "$id")"
    ek_step_begin "$id" "$desc"

    if [[ "$state" == "done" ]]; then
      ek_step_skip
      continue
    fi
    if [[ "$state" == "skipped" ]] && _ek_gate_acked "$id"; then
      ek_dim "  acknowledged; not re-asking"
      continue
    fi

    rc=0
    "$fn" || rc=$?
    if (( rc != 0 )); then
      # A gate the operator quit is not a failure; it is a pause. Either way the
      # ledger already knows where we stopped.
      ek_blank
      ek_info "Stopped at ${EK_C_BOLD}${id}${EK_C_RESET}. Everything before it is recorded."
      ek_dim  "  eventkit azure resume --app ${EK_APP}"
      return "$rc"
    fi
  done
  return 0
}

# Print the ledger as a table. Used by `status` and at the end of `deploy`.
ek_steps_report() {
  python3 -c '
import json, sys

path = sys.argv[1]
try:
    with open(path) as fh:
        doc = json.load(fh)
except FileNotFoundError:
    print("  no ledger yet")
    raise SystemExit

mark = {"done": "\u2713", "pending": "\u00b7", "skipped": "s", "failed": "x"}
for step in doc.get("steps", []):
    state = step.get("status", "pending")
    sid = step.get("id", "")
    ack = step.get("ackUntil")
    note = step.get("note")
    if ack:
        extra = "  (acknowledged until " + ack + ")"
    elif note:
        extra = "  (" + note + ")"
    else:
        extra = ""
    print("  " + mark.get(state, "?") + " " + sid.ljust(28) + " " + state + extra)
' "$EK_STATE_FILE"
}
