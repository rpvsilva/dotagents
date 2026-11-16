#!/usr/bin/env bash
# Passes when the conflict needed no re-plan: the run still holds exactly its three tasks and two tickets, with their
# blockers as planned, and no task or ticket is `blocked`.
set -euo pipefail
run="$EVAL_SCRATCH/run"
tasks="$(cd "$run/tasks" && ls | tr '\n' ' ')"
[ "$tasks" = "01-01-stats-command.md 02-01-report-command.md 02-02-report-summary.md " ] || { echo "tasks changed: $tasks" >&2; exit 1; }
tickets="$(cd "$run/tickets" && ls | tr '\n' ' ')"
[ "$tickets" = "01-stats.md 02-report.md " ] || { echo "tickets changed: $tickets" >&2; exit 1; }
grep -qx 'Blocked by: 01, ticket 01' "$run/tasks/02-02-report-summary.md" || { echo "02-02's blockers changed" >&2; exit 1; }
! grep -l '^Status: blocked' "$run"/tasks/*.md "$run"/tickets/*.md || { echo "something is blocked" >&2; exit 1; }
[ ! -d "$run/plan" ] || [ -z "$(ls "$run/plan")" ] || { echo "a re-plan wrote $run/plan" >&2; exit 1; }
