#!/usr/bin/env bash
# Runs tests/parallel.sh on throwaway suites: every suite runs, the outputs come in the order given, and one failing
# suite fails the whole run.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
fails=0

fail() { echo "FAIL: $*"; fails=$((fails + 1)); }

# suite <name> <exit code>: a suite that logs its run, prints its name and exits with the code given.
suite() {
  printf '#!/bin/sh\necho %s >>"%s/ran"\necho "out %s"\necho "err %s" >&2\nexit %s\n' \
    "$1" "$SANDBOX" "$1" "$1" "$2" >"$SANDBOX/$1.test.sh"
  chmod +x "$SANDBOX/$1.test.sh"
}
suite a 0
suite b 0
suite c 0

out="$("$REPO/tests/parallel.sh" "$SANDBOX/a.test.sh" "$SANDBOX/b.test.sh" "$SANDBOX/c.test.sh")" \
  || fail "all passing: exited non-zero: $out"
[ "$(sort "$SANDBOX/ran" | tr '\n' ' ')" = "a b c " ] || fail "all passing: not every suite ran: $(cat "$SANDBOX/ran")"
[ "$(grep '^out\|^err' <<<"$out" | tr '\n' ' ')" = "out a err a out b err b out c err c " ] \
  || fail "outputs not in the order given, each with its stderr: $out"

rm -f "$SANDBOX/ran"
suite b 3
code=0
out="$(TEST_JOBS=1 "$REPO/tests/parallel.sh" "$SANDBOX/a.test.sh" "$SANDBOX/b.test.sh" "$SANDBOX/c.test.sh")" || code=$?
[ "$code" = 1 ] || fail "one failing: wanted exit 1, got $code"
[ "$(sort "$SANDBOX/ran" | tr '\n' ' ')" = "a b c " ] || fail "one failing: the others did not all run: $(cat "$SANDBOX/ran")"
grep -q "b.test.sh: FAILED (exit 3)" <<<"$out" || fail "one failing: the failure not named with its code: $out"

out="$("$REPO/tests/parallel.sh")" || fail "no suites: exited non-zero"
[ -z "$out" ] || fail "no suites: printed something: $out"

[ "$fails" -eq 0 ] || { echo "parallel.test.sh: $fails failure(s)"; exit 1; }
echo "parallel.test.sh: ok"
