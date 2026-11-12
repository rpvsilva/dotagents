#!/usr/bin/env bash
# Passes when spec.md has the Goal, Behaviours, Out of scope and Open questions headings and a numbered behaviour.
set -euo pipefail
spec="$EVAL_SCRATCH/spec.md"
[ -f "$spec" ]
for heading in 'Goal' 'Behaviours' 'Out of scope' 'Open questions'; do
  grep -Eqi "^#+ +$heading *\$" "$spec"
done
awk '/^#+ +[Bb]ehaviours *$/ { on = 1; next } /^#+ / { on = 0 } on && /^ *1\. / { found = 1 }
     END { exit !found }' "$spec"
