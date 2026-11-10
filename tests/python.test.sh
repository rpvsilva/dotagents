#!/usr/bin/env bash
# Runs every Python unittest suite: each folder holding a tracked test_*.py.
# With PYTHON_TEST_FILES (the changed files, one per line, as tests/run.sh and CI set it from `tests/affected.sh --files`),
# only the folders holding one of them, the deepest when folders nest. Every folder when it is unset, empty or
# `--all`, or when a changed file a Python test may read (a .py, scripts/hooks/secret-patterns.txt) is in none.
# A file some folder's code reads or runs by path from elsewhere in the checkout also picks that folder, as READERS
# lists.
# PYTHON_TEST_LIST=1 prints the folders it would run and stops.
set -euo pipefail

# `<file> <folder>...`: the test folders whose code reads or runs <file> by path, so a change to it alone runs them too.
READERS="adapters/claude/telemetry/extract.py evals evals/_runner/adapters
skills/telemetry/pricing.json evals evals/_runner evals/_runner/adapters
install.sh evals evals/_runner evals/_runner/adapters
scripts/parts.sh evals evals/_runner evals/_runner/adapters
scripts/capped evals evals/_runner evals/_runner/adapters
skills/configure/SKILL.md evals evals/_runner evals/_runner/adapters
skills/configure/needs.json evals evals/_runner evals/_runner/adapters
skills/build-epic/needs.json evals evals/_runner/adapters
docs/evals.md evals evals/_runner
tests/affected.sh evals"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
status=0

all="$(git ls-files '*test_*.py' | xargs -n1 dirname | sort -u)"
dirs="$all"
if [ -n "${PYTHON_TEST_FILES:-}" ]; then
  picked=""
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    best=""
    for d in $all; do
      case "$f" in "$d"/*) [ "${#d}" -le "${#best}" ] || best="$d" ;; esac
    done
    while read -r file readers; do
      # shellcheck disable=SC2086 # one folder per word
      [ "$f" != "$file" ] || picked="$picked$(printf '%s\n' $readers)"$'\n'
    done <<<"$READERS"
    if [ -n "$best" ]; then
      picked="$picked$best"$'\n'
    else
      case "$f" in --all | *.py | scripts/hooks/secret-patterns.txt) picked="" && break ;; esac
    fi
  done <<<"$PYTHON_TEST_FILES"
  [ -z "$picked" ] || dirs="$(printf '%s' "$picked" | sort -u)"
fi

if [ "${PYTHON_TEST_LIST:-}" = 1 ]; then echo "$dirs"; exit 0; fi
[ "$dirs" = "$all" ] || echo "python.test.sh: only the folders holding a changed file (tests/run.sh --all runs every one)"

# Each test file runs in its own unittest process, up to TEST_JOBS at once (default: the CPU count; 1 runs them one by
# one), largest first, each with its output kept apart and printed in folder order.
out="$(mktemp -d)"
trap 'rm -rf "$out"' EXIT
jobs="${TEST_JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || nproc 2>/dev/null || echo 2)}"
case "$jobs" in "" | *[!0-9]* | 0) jobs=2 ;; esac
i=0
while read -r dir; do
  for f in "$dir"/test_*.py; do
    [ -f "$f" ] || continue
    i=$((i + 1))
    printf '%s %s %s\n' "$i" "$dir" "${f##*/}" >>"$out/list"
    echo "$(wc -c <"$f" | tr -d ' ') $i $dir ${f##*/}" >>"$out/sizes"
  done
done <<<"$dirs"
[ -f "$out/list" ] || { echo "python.test.sh: no test files"; exit 1; }

# xargs -P, as in tests/parallel.sh: bash 3.2 and Git Bash both have it. A worker always exits 0.
# shellcheck disable=SC2016 # expanded by the worker's sh
sort -rn "$out/sizes" | while read -r _ n dir file; do printf '%s\0%s\0%s\0' "$n" "$dir" "$file"; done |
  xargs -0 -n 3 -P "$jobs" sh -c '
    python3 -m unittest discover -s "$2" -p "$3" >"$0/$1.out" 2>&1
    echo "$?" >"$0/$1.code"' "$out"

shown=""
while read -r n dir file; do
  [ "$dir" = "$shown" ] || { echo "-- $dir"; shown="$dir"; }
  echo "- $file"
  cat "$out/$n.out" 2>/dev/null
  code="$(cat "$out/$n.code" 2>/dev/null || echo "no result")"
  [ "$code" = 0 ] || { echo "python.test.sh: $dir/$file failed (exit $code)"; status=1; }
done <"$out/list"

[ "$status" -eq 0 ] || { echo "python.test.sh: failures"; exit 1; }
echo "python.test.sh: ok"
