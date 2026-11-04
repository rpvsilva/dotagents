#!/usr/bin/env bash
# Approves cleaning up finished branches without asking, so build-epic and security-audit can run unattended.
# A finished branch is either:
#   - a task branch: an intermediate branch that merges into a ticket or fix branch, never into the default
#     branch: `task/<ticket-slug>/<NN...>` (build-epic) or `fix/security-<name>/<NN...>` (security-audit);
#   - a merged branch (ticket branches included): a local branch, not main or master, whose tip is the head
#     commit of a merged PR on GitHub (`gh pr list --head`). PRs are squash-merged, so git alone can't tell;
#     the tip check means no commit made after the merge is lost. Without gh, or offline, it isn't merged.
# Exit 0 when every part of the command (split on && and ;) is one of:
#   cd <dir>
#   git [-C <dir>] branch -D|-d|--delete [-f|--force|-q|--quiet] <finished branch>...
#   git [-C <dir>] worktree remove [-f|--force] <path>   (the worktree at <path> is on a finished branch)
#   git [-C <dir>] worktree prune
# Exit 1 otherwise: no opinion, the tool asks as usual. Anything with expansions, pipes, redirects or
# globs gets no opinion. Each adapter turns exit 0 into its own "allow" answer.
# Input: the command as $1, or a hook's JSON on stdin with .tool_input.command (and optional .cwd).
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
[ -n "$cmd" ] || exit 1

case "$cmd" in
*[\$\`\|\<\>\(\)\\\*\?\[\{]* | *$'\n'*) exit 1 ;;
esac

task_re='^(task/[^/[:space:]]+|fix/security-[^/[:space:]]+)/[^-/[:space:]][^/[:space:]]*$'
is_task() { [[ "$1" =~ $task_re ]]; }

# Succeeds when local branch $2 of the repo at $1 is merged: its tip is the head of a merged PR.
is_merged() {
  local tip heads
  case "$2" in main | master | '') return 1 ;; esac
  command -v gh >/dev/null 2>&1 || return 1
  tip="$(git -C "$1" rev-parse --verify -q "refs/heads/$2")" || return 1
  heads="$(cd "$1" && GH_PROMPT_DISABLED=1 gh pr list --head "$2" --state merged --limit 100 \
    --json headRefOid --jq '.[].headRefOid' 2>/dev/null)" || return 1
  grep -qx "$tip" <<<"$heads"
}

# Succeeds when branch $2 of the repo at $1 is finished: a task branch or a merged one.
is_finished() { is_task "$2" || is_merged "$1" "$2"; }

# Prints dir $2 resolved from dir $1, or fails when it does not exist.
resolve() {
  local d="${2/#\~/$HOME}"
  (cd "$1" 2>/dev/null && cd "$d" 2>/dev/null && pwd)
}

# Judges one simple command, its words in "$@"; dir is where it runs. Sets dir on cd.
judge() {
  [ $# -gt 0 ] || return 0
  if [ "$1" = cd ]; then
    [ $# -eq 2 ] || return 1
    dir="$(resolve "$dir" "$2")" || return 1
    return 0
  fi
  [ "$1" = git ] || return 1
  shift
  local here="$dir"
  if [ "${1:-}" = -C ]; then
    [ $# -ge 2 ] || return 1
    here="$(resolve "$dir" "$2")" || return 1
    shift 2
  fi
  case "${1:-}" in
  branch)
    shift
    local del=0 names=0 w
    for w in "$@"; do
      case "$w" in
      -D | -d | --delete) del=1 ;;
      -f | --force | -q | --quiet) ;;
      -*) return 1 ;;
      *) is_finished "$here" "$w" || return 1; names=$((names + 1)) ;;
      esac
    done
    [ "$del" -eq 1 ] && [ "$names" -gt 0 ]
    ;;
  worktree)
    shift
    case "${1:-}" in
    prune) [ $# -eq 1 ] ;;
    remove)
      shift
      local path="" w
      for w in "$@"; do
        case "$w" in
        -f | --force) ;;
        -*) return 1 ;;
        *) [ -z "$path" ] || return 1; path="$w" ;;
        esac
      done
      [ -n "$path" ] || return 1
      path="$(resolve "$here" "$path")" || return 1
      is_finished "$path" "$(git -C "$path" symbolic-ref --short HEAD 2>/dev/null)"
      ;;
    *) return 1 ;;
    esac
    ;;
  *) return 1 ;;
  esac
}

dir="$cwd"
# Quotes only group words; with no expansions allowed, dropping them changes nothing but word splitting,
# and a split that adds words only adds words that must pass too.
parts="$(tr -d "\"'" <<<"$cmd" | sed -e 's/&&/;/g')"
[[ "$parts" == *'&'* ]] && exit 1
IFS=';' read -r -a list <<<"$parts"
judged=0
for part in ${list[@]+"${list[@]}"}; do
  read -r -a words <<<"$part" || true
  [ "${#words[@]}" -gt 0 ] || continue
  judge "${words[@]}" || exit 1
  [ "${words[0]}" = cd ] || judged=1
done
[ "$judged" -eq 1 ]
