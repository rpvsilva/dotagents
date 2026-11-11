#!/usr/bin/env bash
# Passes when map.md has the sections every task reads, a Setup line and a Hub files line.
set -euo pipefail
map="$EVAL_SCRATCH/map.md"
[ -f "$map" ]
for heading in 'Checks' 'Invariants' 'Field matrix' 'Test isolation' 'Gaps' 'Lanes'; do
  grep -Eqi "^## +$heading *\$" "$map"
done
grep -Eq '^Setup( \([^)]*\))?:' "$map"
grep -Eq '^Hub files:' "$map"
