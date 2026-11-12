#!/usr/bin/env bash
# Passes when the implementer added at least one test function under tests/, committed or not.
set -euo pipefail
root="$(git -C "$EVAL_WORK" rev-list --max-parents=0 HEAD | tail -n 1)"
if git -C "$EVAL_WORK" diff "$root" -- tests | grep -Eq '^\+[[:space:]]*def test_'; then
  exit 0
fi
while IFS= read -r f; do
  if grep -Eq '^[[:space:]]*def test_' "$EVAL_WORK/$f"; then
    exit 0
  fi
done < <(git -C "$EVAL_WORK" ls-files --others --exclude-standard -- tests)
exit 1
