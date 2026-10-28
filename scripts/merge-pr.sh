#!/usr/bin/env bash
# Merges a PR only when the gate passes and every check is green on its current head commit.
# The only way an agent may merge here: GitHub Free has no rulesets on private repos, so this script is the gate.
# It starts agent-pr-gate for the PR (workflow_dispatch, so the workflow comes from main and a PR
# cannot change its own gate), waits for that run (the gate itself waits for CI), then judges.
# Usage: scripts/merge-pr.sh <pr-number>
set -euo pipefail

GATE=agent-pr-gate
pr="${1:?usage: merge-pr.sh <pr-number>}"

view() { gh pr view "$pr" --json state,isDraft,headRefOid,statusCheckRollup; }
v="$(view)"
[ "$(jq -r .state <<<"$v")" = "OPEN" ] || { echo "PR #$pr is $(jq -r .state <<<"$v"), not merging"; exit 1; }
[ "$(jq -r .isDraft <<<"$v")" = "false" ] || { echo "PR #$pr is a draft, not merging"; exit 1; }

# The run is found by its run-name ("agent-pr-gate #<pr>"), started at most a minute before now
# (the margin absorbs clock skew between this machine and GitHub), polled MERGE_PR_TRIES times, 2s apart.
since="$(jq -nr 'now - 60 | todate')"
gh workflow run "$GATE.yml" -f pr="$pr" >/dev/null
run=""
for _ in $(seq "${MERGE_PR_TRIES:-30}"); do
  run="$(gh run list --workflow "$GATE.yml" --event workflow_dispatch --limit 20 --json databaseId,displayTitle,createdAt |
    jq -r --arg t "$GATE #$pr" --arg s "$since" '[.[] | select(.displayTitle == $t and .createdAt >= $s)] | sort_by(.createdAt) | last | .databaseId // empty')"
  [ -n "$run" ] && break
  sleep 2
done
[ -n "$run" ] || { echo "PR #$pr: the $GATE run did not start, not merging"; exit 1; }
echo "PR #$pr: waiting for $GATE run $run"
gh run watch "$run" >/dev/null

v="$(view)"
sha="$(jq -r .headRefOid <<<"$v")"
blocked=0
tab=$'\t'
# Check runs carry name/conclusion (empty while running), commit statuses carry context/state;
# the latest of each name counts.
results="$(jq -r '.statusCheckRollup | group_by(.name // .context) | map(last | "\(.name // .context)\t\((.conclusion // .state // "") | if . == "" then "PENDING" else . end)") | .[]' <<<"$v")"
grep -qx "$GATE${tab}SUCCESS" <<<"$results" || { echo "PR #$pr: $GATE is not green on $sha"; blocked=1; }
# Every other check: green or skipped, and at least one ($GATE-job is the gate's own run).
others="$(grep -v -e "^$GATE$tab" -e "^$GATE-job$tab" <<<"$results" || true)"
[ -n "$others" ] || { echo "PR #$pr: no CI checks on $sha"; blocked=1; }
while IFS=$'\t' read -r name result; do
  [ -n "$name" ] || continue
  case "$result" in SUCCESS | SKIPPED | NEUTRAL) ;; *) echo "PR #$pr: $name is $result on $sha"; blocked=1 ;; esac
done <<<"$others"
[ "$blocked" -eq 0 ] || { echo "not merging"; exit 1; }

# --match-head-commit: GitHub refuses the merge if a commit landed after the checks above.
gh pr merge "$pr" --squash --delete-branch --match-head-commit "$sha"
