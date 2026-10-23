#!/usr/bin/env bash
# Runs every check CI runs. Lint tools that are not installed are skipped with a warning.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1
status=0

step() { echo "== $1"; shift; "$@" || status=1; }

if command -v shellcheck >/dev/null; then
  step shellcheck sh -c "git ls-files -z '*.sh' | xargs -0 shellcheck"
else
  echo "== shellcheck: not installed, skipped (brew install shellcheck)"
fi

if command -v actionlint >/dev/null; then
  step actionlint actionlint
else
  echo "== actionlint: not installed, skipped (brew install actionlint)"
fi

for t in tests/*.test.sh; do step "$t" "$t"; done

exit "$status"
