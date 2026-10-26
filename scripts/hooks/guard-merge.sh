#!/usr/bin/env bash
# Blocks shell commands that would bypass the merge gate: `gh pr merge`, merging through `gh api`,
# approving a PR (`gh pr review`, or `gh api` with APPROVE in the command), and pushing to main/master. Only in repos with a .review-gate.yml at their root,
# where scripts/merge-pr.sh is the only way to merge (GitHub Free enforces nothing on private repos).
# Input: the command as $1, or a hook's JSON on stdin with .tool_input.command (and optional .cwd).
# Exit 2 with the reason on stderr blocks the command; exit 0 lets it run.
set -euo pipefail

if [ $# -gt 0 ]; then
  cmd="$1"
  cwd="$PWD"
else
  input="$(cat)"
  cmd="$(jq -r '.tool_input.command // .command // empty' <<<"$input")"
  cwd="$(jq -r '.cwd // empty' <<<"$input")"
  cwd="${cwd:-$PWD}"
fi
[ -n "$cmd" ] || exit 0

s='[[:space:]]'
start="(^|[;&|(]|$s)"
end="($s|[;&|)]|$)"
same='[^;&|]*'

# A command that moves into another repo (`cd <dir> && ...`) is judged by that repo.
# Only one cd to an existing dir counts; anything less clear is judged by $cwd.
cd_re="${start}cd$s+([^;&|[:space:]]+)"
if [ "$(grep -cE "$cd_re" <<<"${cmd//[;&|]/$'\n'}")" -eq 1 ] && [[ "$cmd" =~ $cd_re ]]; then
  dir="${BASH_REMATCH[2]//[\"\']/}"
  dir="${dir/#\~/$HOME}"
  dir="$(cd "$cwd" 2>/dev/null && cd "$dir" 2>/dev/null && pwd)" && cwd="$dir"
fi

root="$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null)" || exit 0
[ -f "$root/.review-gate.yml" ] || exit 0

block() {
  echo "guard-merge: $1" >&2
  exit 2
}

if [[ "$cmd" =~ ${start}gh$s+pr$s+merge$end ]] || [[ "$cmd" =~ ${start}gh$s+api$s.*pulls/[^[:space:]]+/merge ]]; then
  block "merge with scripts/merge-pr.sh <number>, which checks ci-ok and agent-pr-gate first."
fi
if [[ "$cmd" =~ ${start}gh$s+pr$s+review$s.*(--approve|-a)$end ]] ||
  { [[ "$cmd" =~ ${start}gh$s+api$s.*pulls/[^[:space:]]+/reviews ]] && [[ "$cmd" =~ APPROVE ]]; }; then
  block "approving a PR is Rui's call; an agent never approves."
fi
if [[ "$cmd" =~ ${start}git$s+push$end ]]; then
  if [[ "$cmd" =~ ${start}git$s+push$s$same($s|:|\+|refs/heads/)(main|master)$end ]]; then
    block "never push to main; push a branch and open a PR."
  fi
  branch="$(git -C "$cwd" symbolic-ref --short HEAD 2>/dev/null || true)"
  if [ "$branch" = main ] || [ "$branch" = master ]; then
    block "you are on $branch; create a branch (git switch -c <name>) and push that."
  fi
fi
exit 0
