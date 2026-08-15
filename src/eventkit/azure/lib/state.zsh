# The step ledger.
#
# Replaces the predecessors' `.env.deploy` as the *structural* record: what was
# created, in what order, and what is still outstanding. `resume` replays only
# what is `pending` or `failed`, which is what makes an interrupted deploy safe
# to re-run.
#
# Secrets are deliberately absent. App Service settings are the source of truth
# for those and are read back when needed; local working values live in a
# gitignored .env.deploy. The ledger is meant to be committed, so anything in it
# is public to anyone with repository access.
#
# JSON is manipulated through python3 rather than jq: eventkit already requires
# Python and `az` ships one, so this adds no dependency. Every helper closes its
# file — an unclosed handle raises ResourceWarning onto stderr, which silently
# corrupts any caller that captures combined output.

typeset -g EK_STATE_FILE="${EK_STATE_FILE:-.eventkit/state.json}"

_ek_py() { python3 -c "$1" "${@:2}"; }

ek_state_init() {
  local event="$1" app="$2"
  [[ -f "$EK_STATE_FILE" ]] && return 0
  mkdir -p "${EK_STATE_FILE:h}"
  _ek_py '
import json, sys

event, app, path = sys.argv[1], sys.argv[2], sys.argv[3]
doc = {
    "schemaVersion": 1,
    "event": event,
    "app": app,
    "azure": {},
    "names": {},
    "datastore": {},
    "steps": [],
    "history": [],
}
with open(path, "w") as fh:
    json.dump(doc, fh, indent=2)
    fh.write("\n")
' "$event" "$app" "$EK_STATE_FILE"
  ek_dim "created ${EK_STATE_FILE}"
}

_ek_state_load_py='
def _load(path):
    with open(path) as fh:
        return __import__("json").load(fh)

def _save(path, doc):
    import json
    with open(path, "w") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
'

# ek_state_get <dotted.path> [default]
ek_state_get() {
  [[ -f "$EK_STATE_FILE" ]] || { print -r -- "${2:-}"; return 0; }
  _ek_py "${_ek_state_load_py}"'
import sys

path, dotted, default = sys.argv[1], sys.argv[2], sys.argv[3]
node = _load(path)
for part in dotted.split("."):
    if isinstance(node, dict) and part in node:
        node = node[part]
    else:
        print(default)
        raise SystemExit
print("" if node is None else node)
' "$EK_STATE_FILE" "$1" "${2:-}"
}

# ek_state_set <dotted.path> <value>
ek_state_set() {
  _ek_py "${_ek_state_load_py}"'
import sys

path, dotted, value = sys.argv[1], sys.argv[2], sys.argv[3]
doc = _load(path)
node = doc
parts = dotted.split(".")
for part in parts[:-1]:
    node = node.setdefault(part, {})
node[parts[-1]] = value
_save(path, doc)
' "$EK_STATE_FILE" "$1" "$2"
}

# ek_step_status <id> -> pending|done|skipped|failed  (pending when unknown)
ek_step_status() {
  _ek_py "${_ek_state_load_py}"'
import sys

path, want = sys.argv[1], sys.argv[2]
try:
    doc = _load(path)
except FileNotFoundError:
    print("pending")
    raise SystemExit
for step in doc.get("steps", []):
    if step.get("id") == want:
        print(step.get("status", "pending"))
        raise SystemExit
print("pending")
' "$EK_STATE_FILE" "$1"
}

# ek_step_record <id> <status> [resourceId] [note]
ek_step_record() {
  _ek_py "${_ek_state_load_py}"'
import datetime, sys

path, sid, status = sys.argv[1], sys.argv[2], sys.argv[3]
resource, note = sys.argv[4], sys.argv[5]
doc = _load(path)
steps = doc.setdefault("steps", [])
entry = next((s for s in steps if s.get("id") == sid), None)
if entry is None:
    entry = {"id": sid}
    steps.append(entry)
entry["status"] = status
entry["at"] = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
if resource:
    entry["resourceId"] = resource
if note:
    entry["note"] = note
_save(path, doc)
' "$EK_STATE_FILE" "$1" "$2" "${3:-}" "${4:-}"
}

# ek_gate_ack <id> <until-date> <reason>
ek_gate_ack() {
  _ek_py "${_ek_state_load_py}"'
import sys

path, sid, until, reason = sys.argv[1:5]
doc = _load(path)
for step in doc.setdefault("steps", []):
    if step.get("id") == sid:
        step["ackUntil"] = until
        step["reason"] = reason
        break
else:
    doc["steps"].append(
        {"id": sid, "status": "skipped", "ackUntil": until, "reason": reason}
    )
_save(path, doc)
' "$EK_STATE_FILE" "$1" "$2" "$3"
}

# Record what ran, and with which tool versions, so a regression can be
# correlated with an `az` upgrade rather than guessed at.
ek_state_history() {
  local verb="$1" az_version
  az_version="$(ek_az_version 2>/dev/null || print -r -- unknown)"
  _ek_py "${_ek_state_load_py}"'
import datetime, sys

path, verb, az_version, ek_version = sys.argv[1:5]
doc = _load(path)
doc.setdefault("history", []).append({
    "at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
    "verb": verb,
    "azVersion": az_version,
    "eventkit": ek_version,
})
doc["history"] = doc["history"][-50:]
_save(path, doc)
' "$EK_STATE_FILE" "$verb" "$az_version" "${EK_VERSION:-unknown}"
}

ek_state_steps_json() {
  [[ -f "$EK_STATE_FILE" ]] || { print -r -- "[]"; return 0; }
  _ek_py "${_ek_state_load_py}"'
import json, sys

print(json.dumps(_load(sys.argv[1]).get("steps", [])))
' "$EK_STATE_FILE"
}
