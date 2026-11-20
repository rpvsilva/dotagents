#!/usr/bin/env bash
# Merges a PR only when agent-pr-gate passes it on its current head commit.
# The only way an agent may merge here: GitHub enforces the main ruleset (PR, CI, code-owner review on high-tier paths), and this script adds the agent-pr-gate judgement pinned to the head commit and the tier rules.
# It runs the gate's read-only CLI (`agent-pr-gate check`, pinned to GATE_REF, the commit the gate
# workflow uses): the same rules, read from the base commit, so a PR cannot change its own gate. The
# CLI waits for CI, judges CI, evidence, approvals and code owners, and prints the verdict as JSON.
# A PR into another branch (build-epic's task PR into a ticket branch, or its ticket PR into an
# integration branch) skips the gate: CI and the gate run on PRs into the default branch only. Whoever
# merges it runs the checks locally first (ship_ticket.py runs map.md's Checks, tests/run.sh among
# them, on a ticket head). The PR into the default branch that carries it (the ticket's own PR, or the
# integration branch's) is judged in full, approvals included.
# A PR into the default branch whose author is not a maintainer (`maintainers:` in .review-gate.yml, read
# from the base branch) or the maintainer's agent App (`app/<maintainer>-agent`) merges only after a maintainer's
# latest review is an approval of its current head: the gate calls an outsider's medium-tier PR out of scope.
# It squashes, or with --merge (build-epic's task_merge_method=merge) makes a merge commit.
# Usage: scripts/merge-pr.sh [--merge] <pr-number>   (needs GH_TOKEN, node and npx)
set -euo pipefail

GATE_REF=2da570e75f377b413e19bdfc338dab7c9e881538
method=--squash
if [ "${1:-}" = --merge ]; then method=--merge; shift; fi
case "${1:-}" in -*) echo "usage: merge-pr.sh [--merge] <pr-number>"; exit 2 ;; esac
pr="${1:?usage: merge-pr.sh [--merge] <pr-number>}"
wait="${MERGE_PR_CI_WAIT:-1800}"

view="$(gh pr view "$pr" --json state,isDraft,baseRefName,headRefOid,isCrossRepository,author,reviews)"
[ "$(jq -r .state <<<"$view")" = "OPEN" ] || { echo "PR #$pr is $(jq -r .state <<<"$view"), not merging"; exit 1; }
[ "$(jq -r .isDraft <<<"$view")" = "false" ] || { echo "PR #$pr is a draft, not merging"; exit 1; }

base="$(jq -r .baseRefName <<<"$view")"
# A fork PR's branch is the contributor's: never try to delete it.
delete=(--delete-branch)
[ "$(jq -r .isCrossRepository <<<"$view")" = "true" ] && delete=()
default="$(gh repo view --json defaultBranchRef --jq .defaultBranchRef.name)"
[ -n "$default" ] || { echo "PR #$pr: could not read the default branch, not merging"; exit 1; }
if [ "$base" != "$default" ]; then
  echo "PR #$pr: base $base is not $default, merging without the gate"
  gh pr merge "$pr" "$method" ${delete[@]+"${delete[@]}"} --match-head-commit "$(jq -r .headRefOid <<<"$view")"
  exit 0
fi

head="$(jq -r .headRefOid <<<"$view")"
rules="$(gh api -H "Accept: application/vnd.github.raw" "repos/{owner}/{repo}/contents/.review-gate.yml?ref=$base")" || rules=""
# `maintainers: [a, b]` on one line; no list read means nobody is trusted.
maintainers="$(sed -n 's/^maintainers:[[:space:]]*\[\(.*\)\].*/\1/p' <<<"$rules" | tr -d ' "'"'" | tr ',' '\n' | sed '/^$/d')"
[ -n "$maintainers" ] || { echo "PR #$pr: could not read the maintainers from .review-gate.yml on $base, not merging"; exit 1; }
author="$(jq -r '.author.login // empty' <<<"$view")"
trusted=false
while IFS= read -r m; do
  # The App's login is `app/<maintainer>-agent`; a plain user named `<maintainer>-agent` is an outsider.
  if [ "$author" = "$m" ] || [ "$author" = "app/$m-agent" ]; then trusted=true; fi
done <<<"$maintainers"
if [ "$trusted" = false ]; then
  approved=false
  while IFS= read -r m; do
    # A maintainer's latest approving or rejecting review counts; comments do not undo an approval.
    state="$(jq -r --arg m "$m" --arg h "$head" '[.reviews[]? | select(.author.login == $m and (.state == "APPROVED" or .state == "CHANGES_REQUESTED" or .state == "DISMISSED"))] | last | if . != null and .state == "APPROVED" and .commit.oid == $h then "ok" else "no" end' <<<"$view")"
    [ "$state" = ok ] && approved=true
  done <<<"$maintainers"
  if [ "$approved" = false ]; then
    echo "PR #$pr: author ${author:-unknown} is not a maintainer and no maintainer has approved head ${head:0:7}, not merging"
    exit 1
  fi
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
# An outsider's PR was approved on $head: if it moved while the gate waited, that approval no longer covers it.
if [ "$trusted" = false ] && [ "$sha" != "$head" ]; then
  echo "PR #$pr: head moved from ${head:0:7} to ${sha:0:7} after the maintainer approved it, not merging"
  exit 1
fi
gh pr merge "$pr" "$method" ${delete[@]+"${delete[@]}"} --match-head-commit "$sha"
