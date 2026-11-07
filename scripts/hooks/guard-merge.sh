#!/usr/bin/env bash
# Blocks shell commands that would bypass the merge gate: `gh pr merge`, merging through `gh api`,
# approving a PR (`gh pr review`, or `gh api` with APPROVE in the command), and pushing to main/master. Only in repos with a .review-gate.yml at their root,
# where the repo's merge script is the only way to merge (GitHub Free enforces nothing on private repos).
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

# shellcheck source=SCRIPTDIR/lib.sh disable=SC1091
. "$(dirname "$0")/lib.sh"
# Heredoc bodies are data (a file being written), never a command to judge.
cmd="$(strip_heredocs "$cmd")"

s='[[:space:]]'
start="(^|[;&|(]|$s)"
end="($s|[;&|)]|$)"
same='[^;&|]*'

cwd="$(target_dir "$cmd" "$cwd")"

root="$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null)" || exit 0
[ -f "$root/.review-gate.yml" ] || exit 0

block() {
  echo "guard-merge: $1" >&2
  exit 2
}

if [[ "$cmd" =~ ${start}gh$s+pr$s+merge$end ]] || [[ "$cmd" =~ ${start}gh$s+api$s.*pulls/[^[:space:]]+/merge ]]; then
  block "merge through the repo's merge script, which checks the PR first; never merge directly."
fi
if [[ "$cmd" =~ ${start}gh$s+pr$s+review$s.*(--approve|-a)$end ]] ||
  { [[ "$cmd" =~ ${start}gh$s+api$s.*pulls/[^[:space:]]+/reviews ]] && [[ "$cmd" =~ APPROVE ]]; }; then
  block "approving a PR is a human's call; an agent never approves."
fi
if [[ "$cmd" =~ ${start}git$s+push$end ]]; then
  if [[ "$cmd" =~ ${start}git$s+push$s$same($s|:|\+|refs/heads/)(main|master)$end ]]; then
    block "never push to main; push a branch and open a PR."
  fi
  branch="$(git -C "$cwd" symbolic-ref --short HEAD 2>/dev/null || true)"
  # A `git switch`/`git checkout <branch>` before the push moves to that branch first; the last one wins.
  before="$(tr '\n' ';' <<<"$cmd" | sed -E "s/${start}git${s}+push${end}.*//")"
  # Flags are -x... or --word; a bare -- means paths follow (`git checkout -- file`), no switch.
  switch_re="${start}git$s+(switch|checkout)$s+((-(-[^;&|[:space:]]+|[^-;&|[:space:]][^;&|[:space:]]*)$s+)*)([^-;&|[:space:]][^;&|[:space:]]*)(.*)"
  while [[ "$before" =~ $switch_re ]]; do
    branch="${BASH_REMATCH[6]}"
    before="${BASH_REMATCH[7]}"
  done
  if [ "$branch" = main ] || [ "$branch" = master ]; then
    block "you are on $branch; create a branch (git switch -c <name>) and push that."
  fi
fi
exit 0
