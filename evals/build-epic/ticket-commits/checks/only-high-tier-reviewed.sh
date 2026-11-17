#!/usr/bin/env bash
# Passes when the one `Tier: high` task (01-02) was reviewed before it merged, at the head it merged, and no other task
# was: every other task is left to the ticket's deep review.
set -euo pipefail
run="$EVAL_SCRATCH/run"
reports=""
for f in "$run"/reviews/*.json "$run"/reviews/*.md; do
  name="${f##*/}"
  [[ "$name" =~ ^[0-9]+-[0-9]+-[0-9a-f]{7}\.(json|md)$ ]] && reports+="$name"$'\n'
done
reports="${reports%$'\n'}"
[ -n "$reports" ] || { echo "no task review in $run/reviews" >&2; exit 1; }
others="$(grep -v '^01-02-' <<<"$reports" || true)"
[ -z "$others" ] || { echo "reviewed besides 01-02: $others" >&2; exit 1; }
merged="$(sed -n 's/^Commit: //p' "$run/tasks/01-02-item-fields.md")"
head="$(git -C "$EVAL_WORK" rev-parse --short=7 "$merged^2")"
grep -qxE "01-02-$head\.(json|md)" <<<"$reports" || { echo "no review of 01-02's merged head $head" >&2; exit 1; }
