#!/usr/bin/env bash
# Runs every Python unittest suite: each folder holding a tracked test_*.py.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
status=0

while read -r dir; do
  echo "-- $dir"
  python3 -m unittest discover -s "$dir" -p 'test_*.py' || status=1
done < <(git ls-files '*test_*.py' | xargs -n1 dirname | sort -u)

[ "$status" -eq 0 ] || { echo "python.test.sh: failures"; exit 1; }
echo "python.test.sh: ok"
