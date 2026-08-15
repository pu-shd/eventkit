# Logging and the step counter.
#
# Everything an operator sees goes through here, so that --quiet, NO_COLOR and
# the non-TTY case are handled once rather than at every call site.

# NOTE ON ARITHMETIC: in zsh, (( expr )) exits non-zero when expr evaluates to
# 0, and this toolkit runs under `setopt err_return`. So a post-increment from
# zero — (( i++ )) with i=0 — aborts the enclosing function. Counters are
# incremented with an explicit assignment instead.

typeset -g EK_STEP_TOTAL=0
typeset -g EK_STEP_INDEX=0

ek_info()    { print -r -- "${EK_C_CYAN}·${EK_C_RESET} $*"; }
ek_ok()      { print -r -- "${EK_C_GREEN}✓${EK_C_RESET} $*"; }
ek_warn()    { print -r -- "${EK_C_YELLOW}!${EK_C_RESET} $*" >&2; }
ek_err()     { print -r -- "${EK_C_RED}✗${EK_C_RESET} $*" >&2; }
ek_dim()     { print -r -- "${EK_C_GREY}$*${EK_C_RESET}"; }
ek_blank()   { print -r -- ""; }

ek_die() {
  ek_err "$*"
  exit 1
}

ek_heading() {
  ek_blank
  print -r -- "${EK_C_BOLD}$*${EK_C_RESET}"
  print -r -- "${EK_C_GREY}$(printf '─%.0s' {1..${#1}})${EK_C_RESET}"
}

# ek_step_begin <id> <description>
ek_step_begin() {
  EK_STEP_INDEX=$(( EK_STEP_INDEX + 1 ))
  local counter="[${EK_STEP_INDEX}/${EK_STEP_TOTAL}]"
  print -r -- "${EK_C_BOLD}${counter}${EK_C_RESET} $2 ${EK_C_GREY}($1)${EK_C_RESET}"
}

ek_step_skip() {
  print -r -- "  ${EK_C_GREY}already done${EK_C_RESET}"
}

ek_step_done() {
  print -r -- "  ${EK_C_GREEN}done${EK_C_RESET}${1:+ ${EK_C_GREY}$1${EK_C_RESET}}"
}
