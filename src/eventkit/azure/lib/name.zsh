# Resource naming.
#
# Deterministic and clamped. Azure's limits differ per resource type and are
# silently fatal at create time, so the clamping happens here and the result is
# written to the ledger — a truncated name must be the *same* truncated name on
# the next run.
#
# No institutional prefix is baked in. The predecessors hardcoded `orfe-`
# throughout, which is exactly the kind of thing that makes a toolkit
# un-adoptable.

typeset -g EK_PREFIX="${EK_PREFIX:-ek}"

# Azure name-length ceilings we care about.
typeset -gA EK_NAME_LIMITS=(
  rg       90
  plan     40
  webapp   60
  acr      50
  storage  24
  db       63
  identity 128
)

# Characters permitted per type: "alnum" or "dash".
typeset -gA EK_NAME_CHARSET=(
  rg       dash
  plan     dash
  webapp   dash
  acr      alnum
  storage  alnum
  db       dash
  identity dash
)

ek_slug() {
  print -r -- "${1:l}" | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//; s/-+/-/g'
}

# ek_name <type> <part>...
#
# Joins the parts, applies the character set, and clamps to the type's ceiling
# by trimming the *middle* rather than the tail, so the discriminating suffix
# (usually a random id) survives.
ek_name() {
  local kind="$1"; shift
  local limit="${EK_NAME_LIMITS[$kind]:-60}"
  local charset="${EK_NAME_CHARSET[$kind]:-dash}"

  local joined="${EK_PREFIX}-${(j:-:)@}"
  joined="$(ek_slug "$joined")"
  [[ "$charset" == "alnum" ]] && joined="${joined//-/}"

  if (( ${#joined} > limit )); then
    local keep_tail=8
    local head_len=$(( limit - keep_tail ))
    joined="${joined[1,$head_len]}${joined[-$keep_tail,-1]}"
  fi
  print -r -- "$joined"
}

# A short, stable discriminator so two events in one subscription do not collide
# on globally-unique names.
ek_random_id() {
  local seed="$1"
  # shasum is macOS; sha256sum is Linux; python3 is both. The value only has to
  # be stable for a given seed, not cryptographic.
  if command -v shasum >/dev/null 2>&1; then
    print -r -- "$(print -r -- "$seed" | shasum | cut -c1-6)"
  elif command -v sha256sum >/dev/null 2>&1; then
    print -r -- "$(print -r -- "$seed" | sha256sum | cut -c1-6)"
  else
    print -r -- "$(python3 -c "import hashlib,sys;print(hashlib.sha256(sys.argv[1].encode()).hexdigest()[:6])" "$seed")"
  fi
}
