#!/usr/bin/env bash
# Runs every Python unittest suite: each folder holding a tracked test_*.py.
# With PYTHON_TEST_FILES (the changed files, one per line, as tests/run.sh sets it from `tests/affected.sh --files`),
# only the folders holding one of them, the deepest when folders nest. Every folder when it is unset, empty or
# `--all`, or when a changed file a Python test may read (a .py, scripts/hooks/secret-patterns.txt) is in none.
# PYTHON_TEST_LIST=1 prints the folders it would run and stops.
set -euo pipefail

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

while read -r dir; do
  echo "-- $dir"
  python3 -m unittest discover -s "$dir" -p 'test_*.py' || status=1
done <<<"$dirs"

[ "$status" -eq 0 ] || { echo "python.test.sh: failures"; exit 1; }
echo "python.test.sh: ok"
