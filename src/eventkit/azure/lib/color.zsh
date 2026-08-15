# Colour, respecting the environment rather than assuming a terminal.
#
# NO_COLOR is honoured (https://no-color.org), as is a non-TTY stdout — piping
# `eventkit azure status` into a file should not produce escape codes — and
# TERM=dumb, which is what CI and some editors report.

ek_color_init() {
  if [[ -n "${NO_COLOR:-}" || ! -t 1 || "${TERM:-dumb}" == "dumb" ]]; then
    EK_C_RESET="" EK_C_BOLD="" EK_C_DIM=""
    EK_C_RED="" EK_C_GREEN="" EK_C_YELLOW="" EK_C_BLUE="" EK_C_CYAN="" EK_C_GREY=""
    EK_COLOR=0
  else
    EK_C_RESET=$'\e[0m'  EK_C_BOLD=$'\e[1m'   EK_C_DIM=$'\e[2m'
    EK_C_RED=$'\e[31m'   EK_C_GREEN=$'\e[32m' EK_C_YELLOW=$'\e[33m'
    EK_C_BLUE=$'\e[34m'  EK_C_CYAN=$'\e[36m'  EK_C_GREY=$'\e[90m'
    EK_COLOR=1
  fi
}

# Spinner frames. ASCII when we cannot be sure the terminal renders braille.
ek_spinner_frames() {
  if [[ "${EK_COLOR:-0}" == "1" && "${LANG:-}" == *[Uu][Tt][Ff]* ]]; then
    print -r -- "⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏"
  else
    print -r -- "| / - \\"
  fi
}
