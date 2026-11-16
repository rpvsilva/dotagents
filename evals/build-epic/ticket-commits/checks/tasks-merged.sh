#!/usr/bin/env bash
# Passes when every task of ticket 01 is `done` with a `Commit:` line naming a merge commit on the ticket branch's
# first-parent history: tasks landed as commits, one merge each.
set -euo pipefail
run="$EVAL_SCRATCH/run"
first="$(git -C "$EVAL_WORK" rev-list --first-parent refs/heads/feat/gh-403-subtasks)"
for task in "$run"/tasks/01-*.md; do
  grep -qx 'Status: done' "$task" || { echo "not done: $task" >&2; exit 1; }
  commit="$(sed -n 's/^Commit: //p' "$task")"
  [ -n "$commit" ] || { echo "no Commit: line: $task" >&2; exit 1; }
  grep -qx "$commit" <<<"$first" || { echo "not on the ticket branch: $commit ($task)" >&2; exit 1; }
  [ "$(git -C "$EVAL_WORK" rev-list --parents -n 1 "$commit" | wc -w)" -eq 3 ] || { echo "not a merge: $commit" >&2; exit 1; }
done
