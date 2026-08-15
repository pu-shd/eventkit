#!/usr/bin/env bash
# Python and (later) JavaScript tests, one exit code.
#
# The same script runs locally and inside the Docker `test` target, so "works on
# my machine" and "works in CI" cannot diverge. Bash rather than zsh because this
# runs inside python:3.11-slim, which has no zsh; the *deployment* scripts are
# zsh, per the project convention.
set -euo pipefail

cd "$(dirname "$0")"

echo "==> ruff"
if command -v ruff >/dev/null 2>&1; then
  ruff check src tests
else
  echo "    ruff not installed; skipping lint (CI installs it)"
fi

echo "==> pytest"
python -m pytest -q --cov=eventkit --cov-report=term-missing "$@"

# The JavaScript half arrives with eventkit.ui. vitest + jsdom are confined to
# the Docker `test` stage so no app repo needs node_modules in its runtime build.
if [ -f package.json ]; then
  echo "==> vitest"
  npx vitest run
else
  echo "==> vitest: no package.json yet (arrives with eventkit.ui)"
fi

echo "==> shellcheck (toolkit)"
find src/eventkit/azure -name '*.zsh' -o -name 'eventkit-azure' | while read -r f; do
  shellcheck --shell=bash --severity=warning \
    --exclude=SC1090,SC1091,SC2296,SC2299,SC2154,SC2034,SC2168,SC2178,SC2128,SC2206,SC2207,SC3000-SC3100 \
    "$f" || echo "  (advisory) $f"
done

echo "==> bats (Azure toolkit)"
bats tests/azure/toolkit.bats

echo "==> all green"
