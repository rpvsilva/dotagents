#!/usr/bin/env bash
# Merges a PR only when agent-pr-gate passes it on its current head commit.
# The only way an agent may merge here: GitHub Free has no rulesets on private repos, so this script is the gate.
# It runs the gate's read-only CLI (`agent-pr-gate check`, pinned to GATE_REF, the commit the gate
# workflow uses): the same rules, read from the base commit, so a PR cannot change its own gate. The
# CLI waits for CI, judges CI, evidence, approvals and code owners, and prints the verdict as JSON.
# Usage: scripts/merge-pr.sh <pr-number>   (needs GH_TOKEN, node and npx)
set -euo pipefail

GATE_REF=2da570e75f377b413e19bdfc338dab7c9e881538
pr="${1:?usage: merge-pr.sh <pr-number>}"
wait="${MERGE_PR_CI_WAIT:-1800}"

view="$(gh pr view "$pr" --json state,isDraft)"
[ "$(jq -r .state <<<"$view")" = "OPEN" ] || { echo "PR #$pr is $(jq -r .state <<<"$view"), not merging"; exit 1; }
[ "$(jq -r .isDraft <<<"$view")" = "false" ] || { echo "PR #$pr is a draft, not merging"; exit 1; }

echo "PR #$pr: running agent-pr-gate (waits up to ${wait}s for CI)"
code=0
verdict="$(npx --yes "github:0xgui/agent-pr-gate#$GATE_REF" check "$pr" --ci-wait-seconds "$wait")" || code=$?
result="$(jq -r '.result // empty' <<<"$verdict" 2>/dev/null || true)"
# Exit 0 means pass or not in scope; anything else (blocked, could not judge) stops the merge.
if [ "$code" -ne 0 ] || [ -z "$result" ]; then
  echo "PR #$pr: agent-pr-gate says ${result:-no verdict} (exit $code), not merging"
  jq -r '.problems[]? | "  - \(.what) Fix: \(.fix)"' <<<"$verdict" 2>/dev/null || true
  exit 1
fi

# --match-head-commit: GitHub refuses the merge unless the head is still the commit the gate judged.
sha="$(jq -r .head_sha <<<"$verdict")"
gh pr merge "$pr" --squash --delete-branch --match-head-commit "$sha"
