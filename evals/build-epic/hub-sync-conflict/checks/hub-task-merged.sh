#!/usr/bin/env bash
# Passes when the hub task 02-02 is `done`, its `Commit:` a merge commit on the ticket branch's first-parent history,
# and the ticket branch holds `main`'s tip: the base merge that conflicted was resolved and landed.
set -euo pipefail
task="$EVAL_SCRATCH/run/tasks/02-02-report-summary.md"
grep -qx 'Status: done' "$task" || { echo "02-02 not done: $(grep '^Status:' "$task")" >&2; exit 1; }
commit="$(sed -n 's/^Commit: //p' "$task")"
[ -n "$commit" ] || { echo "02-02 has no Commit: line" >&2; exit 1; }
first="$(git -C "$EVAL_WORK" rev-list --first-parent refs/heads/feat/report)"
grep -qx "$commit" <<<"$first" || { echo "not on feat/report: $commit" >&2; exit 1; }
[ "$(git -C "$EVAL_WORK" rev-list --parents -n 1 "$commit" | wc -w)" -eq 3 ] || { echo "not a merge: $commit" >&2; exit 1; }
git -C "$EVAL_WORK" merge-base --is-ancestor "$(git --git-dir "$EVAL_SCRATCH/origin.git" rev-parse main)" refs/heads/feat/report \
  || { echo "feat/report lacks main's tip" >&2; exit 1; }
