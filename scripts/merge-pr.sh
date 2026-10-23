#!/usr/bin/env bash
# Merges a PR only when every required check is green on its current head commit.
# The only way an agent may merge here: GitHub Free has no rulesets on private repos, so this script is the gate.
# Usage: scripts/merge-pr.sh <pr-number>
set -euo pipefail

REQUIRED=(ci-ok agent-pr-gate)
pr="${1:?usage: merge-pr.sh <pr-number>}"

view="$(gh pr view "$pr" --json state,isDraft,headRefOid,statusCheckRollup)"
state="$(jq -r .state <<<"$view")"
sha="$(jq -r .headRefOid <<<"$view")"

[ "$state" = "OPEN" ] || { echo "PR #$pr is $state, not merging"; exit 1; }
[ "$(jq -r .isDraft <<<"$view")" = "false" ] || { echo "PR #$pr is a draft, not merging"; exit 1; }

blocked=0
for name in "${REQUIRED[@]}"; do
  # Check runs carry name/conclusion, commit statuses carry context/state.
  result="$(jq -r --arg n "$name" '[.statusCheckRollup[] | select((.name // .context) == $n) | (.conclusion // .state)] | last // "MISSING"' <<<"$view")"
  if [ "$result" != "SUCCESS" ]; then
    echo "PR #$pr: $name is $result on $sha"
    blocked=1
  fi
done
[ "$blocked" -eq 0 ] || { echo "not merging"; exit 1; }

# --match-head-commit: GitHub refuses the merge if a commit landed after the checks above.
gh pr merge "$pr" --squash --delete-branch --match-head-commit "$sha"
