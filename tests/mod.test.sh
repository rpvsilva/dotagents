#!/usr/bin/env bash
# Checks every Claude Code mod in adapters/claude/mods/: its plain-logic tests (hooks/*.unit.ts) under node --test,
# then `claude plugin validate` and `claude plugin test` (its hooks/*.test.tsx under the engine). Without node 22.6+
# the unit tests are skipped with a warning; without claude, validate and test are.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
fails=0

fail() { echo "FAIL: $*"; fails=$((fails + 1)); }

# node strips TypeScript types from 23.6 on, and from 22.6 behind --experimental-strip-types.
node_args=()
if ! command -v node >/dev/null; then
  echo "WARN: node not installed, mod unit tests skipped (install node 22.6+)"
else
  v="$(node -p process.versions.node | tr -d '\r')"
  major="${v%%.*}" minor="${v#*.}"
  minor="${minor%%.*}"
  if [ "$major" -gt 23 ] || { [ "$major" -eq 23 ] && [ "$minor" -ge 6 ]; }; then
    node_args=(--test)
  elif [ "$major" -eq 23 ] || { [ "$major" -eq 22 ] && [ "$minor" -ge 6 ]; }; then
    node_args=(--experimental-strip-types --test)
  else
    echo "WARN: node $v cannot run .ts, mod unit tests skipped (install node 22.6+)"
  fi
fi
command -v claude >/dev/null || echo "WARN: claude not installed, claude plugin validate and test skipped"

for m in "$REPO"/adapters/claude/mods/*/; do
  [ -d "$m" ] || continue
  m="${m%/}"
  name="${m##*/}"
  units=("$m"/hooks/*.unit.ts)
  if [ "${#node_args[@]}" -gt 0 ] && [ -e "${units[0]}" ]; then
    node "${node_args[@]}" "${units[@]}" >"$SANDBOX/out" 2>&1 || fail "$name: node --test: $(cat "$SANDBOX/out")"
  fi
  if command -v claude >/dev/null; then
    claude plugin validate "$m" >"$SANDBOX/out" 2>&1 || fail "$name: claude plugin validate: $(cat "$SANDBOX/out")"
    claude plugin test "$m" >"$SANDBOX/out" 2>&1 || fail "$name: claude plugin test: $(cat "$SANDBOX/out")"
  fi
done

[ "$fails" -eq 0 ] || { echo "mod.test.sh: $fails failure(s)"; exit 1; }
echo "mod.test.sh: ok"
