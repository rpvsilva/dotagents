#!/usr/bin/env bash
# Runs the given test suites at once, up to TEST_JOBS at a time (default: the CPU count), each with its output kept
# apart. It says when each one finishes, then prints each suite's output in the order given, and exits 1 if any failed.
# Usage: tests/parallel.sh <suite>...   (TEST_JOBS=1 runs them one at a time)
# The suites run through their shebang, so a `bash` first on PATH (tests/run.sh's bash 3.2 shim) runs them.
set -uo pipefail

[ "$#" -gt 0 ] || exit 0
out="$(mktemp -d)"
trap 'rm -rf "$out"' EXIT
jobs="${TEST_JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || nproc 2>/dev/null || echo 2)}"
case "$jobs" in "" | *[!0-9]* | 0) jobs=2 ;; esac

# Largest file first: the long suites are the big ones, and one started last would finish well after the rest.
i=0
for t in "$@"; do
  i=$((i + 1))
  echo "$(wc -c <"$t" | tr -d ' ') $i"
done | sort -rn >"$out/order"

# xargs -P: parallel runs that bash 3.2 (no `wait -n`) and Git Bash both have. Each worker gets the output folder,
# the suite's position and its path; it always exits 0, so a failing suite never stops xargs.
suites=("$@")
# shellcheck disable=SC2016 # expanded by the worker's sh
while read -r _ i; do
  printf '%s\0%s\0' "$i" "${suites[$((i - 1))]}"
done <"$out/order" | xargs -0 -n 2 -P "$jobs" sh -c '
  start=$(date +%s)
  "$2" >"$0/$1.out" 2>&1
  code=$?
  echo "$code $(($(date +%s) - start))" >"$0/$1.code"
  echo "-- $2: exit $code, $(($(date +%s) - start))s"' "$out"

status=0
i=0
for t in "$@"; do
  i=$((i + 1))
  read -r code secs <"$out/$i.code" 2>/dev/null || { code="no result" secs="?"; }
  echo "== $t (${secs}s)"
  cat "$out/$i.out" 2>/dev/null
  [ "$code" = 0 ] || { echo "== $t: FAILED (exit $code)"; status=1; }
done
exit "$status"
