#!/usr/bin/env bash
# Merges a PR only when agent-pr-gate passes it on its current head commit.
# The only way an agent may merge here: GitHub Free has no rulesets on private repos, so this script is the gate.
# It runs the gate's read-only CLI (`agent-pr-gate check`, pinned to GATE_REF, the commit the gate
# workflow uses): the same rules, read from the base commit, so a PR cannot change its own gate. The
# CLI waits for CI, judges CI, evidence, approvals and code owners, and prints the verdict as JSON.
# A PR into another branch (build-epic's task PR into a ticket branch, or its ticket PR into an
# integration branch) skips the gate: CI and the gate run on PRs into the default branch only. Whoever
# merges it runs the checks locally first (ship_ticket.py runs map.md's Checks, tests/run.sh among
# them, on a ticket head). The PR into the default branch that carries it (the ticket's own PR, or the
# integration branch's) is judged in full, approvals included.
# It squashes, or with --merge (build-epic's task_merge_method=merge) makes a merge commit.
# Usage: scripts/merge-pr.sh [--merge] <pr-number>   (needs GH_TOKEN, node and npx)
set -euo pipefail

GATE_REF=2da570e75f377b413e19bdfc338dab7c9e881538
method=--squash
if [ "${1:-}" = --merge ]; then method=--merge; shift; fi
case "${1:-}" in -*) echo "usage: merge-pr.sh [--merge] <pr-number>"; exit 2 ;; esac
pr="${1:?usage: merge-pr.sh [--merge] <pr-number>}"
wait="${MERGE_PR_CI_WAIT:-1800}"

view="$(gh pr view "$pr" --json state,isDraft,baseRefName,headRefOid)"
[ "$(jq -r .state <<<"$view")" = "OPEN" ] || { echo "PR #$pr is $(jq -r .state <<<"$view"), not merging"; exit 1; }
[ "$(jq -r .isDraft <<<"$view")" = "false" ] || { echo "PR #$pr is a draft, not merging"; exit 1; }

base="$(jq -r .baseRefName <<<"$view")"
default="$(gh repo view --json defaultBranchRef --jq .defaultBranchRef.name)"
[ -n "$default" ] || { echo "PR #$pr: could not read the default branch, not merging"; exit 1; }
if [ "$base" != "$default" ]; then
  echo "PR #$pr: base $base is not $default, merging without the gate"
  gh pr merge "$pr" "$method" --delete-branch --match-head-commit "$(jq -r .headRefOid <<<"$view")"
  exit 0
fi

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
gh pr merge "$pr" "$method" --delete-branch --match-head-commit "$sha"
