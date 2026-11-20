#!/usr/bin/env bash
# Applies the `main` ruleset: GitHub refuses direct pushes to main, merges without passing CI, and high-tier
# changes (CODEOWNERS) without a code owner's approval. Run it once the repo is public (rulesets need a public repo
# on a free plan); run again, it updates the ruleset named `main` instead of adding a second.
# Usage: scripts/protect-main.sh [--dry-run] [<owner/repo>]   (needs GH_TOKEN or a gh login, and jq)
#   --dry-run  prints the ruleset JSON and calls nothing that writes.
# Required checks are the job ids in .github/workflows/ci.yml; never agent-pr-gate-job (merge-pr.sh runs the gate).
set -euo pipefail

dry=0
if [ "${1:-}" = --dry-run ]; then dry=1; shift; fi
case "${1:-}" in -*) echo "usage: protect-main.sh [--dry-run] [<owner/repo>]" >&2; exit 2 ;; esac

ruleset="$(jq -n '{
  name: "main",
  target: "branch",
  enforcement: "active",
  bypass_actors: [],
  conditions: {ref_name: {include: ["refs/heads/main"], exclude: []}},
  rules: [
    {type: "deletion"},
    {type: "non_fast_forward"},
    {type: "pull_request", parameters: {
      required_approving_review_count: 0,
      require_code_owner_review: true,
      dismiss_stale_reviews_on_push: true,
      require_last_push_approval: false,
      required_review_thread_resolution: false,
      allowed_merge_methods: ["squash", "merge"]}},
    {type: "required_status_checks", parameters: {
      strict_required_status_checks_policy: false,
      required_status_checks: [{context: "lint"}, {context: "test"}, {context: "secrets"}]}}
  ]}')"

if [ "$dry" = 1 ]; then
  echo "$ruleset"
  exit 0
fi

repo="${1:-$(gh repo view --json nameWithOwner --jq .nameWithOwner)}"
[[ "$repo" == */* ]] || { echo "protect-main: not an owner/repo: $repo" >&2; exit 1; }

# One line, whatever gh printed, for a failed call.
fail() {
  if grep -qE 'HTTP (403|404)' <<<"$1"; then
    echo "protect-main: GitHub refused (rulesets need the repo public on a free plan): $repo" >&2
  else
    echo "protect-main: gh failed: $(tr '\n' ' ' <<<"$1")" >&2
  fi
  exit 1
}

list="$(gh api "repos/$repo/rulesets" 2>&1)" || fail "$list"
id="$(jq -r '[.[] | select(.name == "main")][0].id // empty' <<<"$list")"

if [ -n "$id" ]; then
  out="$(gh api -X PUT "repos/$repo/rulesets/$id" --input - <<<"$ruleset" 2>&1)" || fail "$out"
  echo "protect-main: updated ruleset main (id $id) on $repo"
else
  out="$(gh api -X POST "repos/$repo/rulesets" --input - <<<"$ruleset" 2>&1)" || fail "$out"
  echo "protect-main: created ruleset main on $repo"
fi
