#!/usr/bin/env bash
# Removes old remote-agent run records, run as `remote-agent prune --older-than <Nd|Nh> [--dry-run] [dir]`: every run
# folder of the repo at dir (default the current one) in <common git dir>/remote-agent/, and in the old per-worktree
# locations (<common git dir>/worktrees/<name>/remote-agent/), whose age is over N days (d) or hours (h), N capped at
# 999999. Its age runs from run.json's ended, else its started, else the folder's mtime. A live run is never removed:
# state starting or running with its pid alive and the same pid_start (without a ps that prints start times, `kill -0`
# alone decides); one whose pid is gone, or taken by another process, stopped and goes. A folder whose name is no run
# name (^[A-Za-z0-9][A-Za-z0-9._-]*$) is left, with one line on stderr; a link is never followed nor removed. Prints one
# line per folder removed (`removed <path>`; `would remove <path>` with --dry-run, which removes nothing), then the
# count (`removed N runs`). Exit 1 with the usage on a bad argument, or when a folder could not be removed.
set -euo pipefail

usage() { echo "usage: remote-agent prune --older-than <Nd|Nh> [--dry-run] [dir]" >&2; exit 1; }
age="" dry="" dir=""
while [ $# -gt 0 ]; do
  case "$1" in
    --older-than) [ $# -ge 2 ] || usage; age="$2"; shift ;;
    --dry-run) dry=1 ;;
    -*) usage ;;
    *) [ -z "$dir" ] || usage; dir="$1" ;;
  esac
  shift
done
[[ "$age" =~ ^[0-9]+[dh]$ ]] || usage
n="${age%?}"
# Six digits at most (over 2700 years): a longer number would overflow the shell's arithmetic into a negative age.
n="${n#"${n%%[!0]*}"}"
[ ${#n} -le 6 ] || n=999999
n=$((10#${n:-0}))
case "$age" in *d) secs=$((n * 86400)) ;; *) secs=$((n * 3600)) ;; esac
common="$(git -C "${dir:-$PWD}" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" \
  || { echo "remote-agent: not in a git worktree: ${dir:-$PWD}" >&2; exit 1; }
now="$(date +%s)"

# Whether ps prints start times here (Git Bash's ps has no -o).
has_lstart=""
[ -z "$(LC_ALL=C ps -o lstart= -p $$ 2>/dev/null)" ] || has_lstart=1
# Whether pid $1, recorded as started at $2 (empty when unknown), is the same process still alive. Compares as the
# agent-flow mod does: whitespace squashed, in the C locale remote-agent wrote it in.
alive() {
  local now_start
  if [ -z "$has_lstart" ]; then kill -0 "$1" 2>/dev/null; return; fi
  now_start="$(LC_ALL=C ps -o lstart= -p "$1" 2>/dev/null | tr -s ' \t' ' ' | sed 's/^ //; s/ $//')"
  [ -n "$now_start" ] || return 1
  [ -z "$2" ] || [ "$now_start" = "$(printf '%s' "$2" | tr -s ' \t' ' ' | sed 's/^ //; s/ $//')" ]
}

# Whether run folder $1 is to go: older than the age and not live.
old() {
  local f="$1/run.json" t="" state="" pid="" ps=""
  if [ -f "$f" ] && [ ! -L "$f" ]; then
    # One value per line, empty when missing: pid_start may hold any spaces.
    # shellcheck disable=SC2016 # jq's variables
    { IFS= read -r t; IFS= read -r state; IFS= read -r pid; IFS= read -r ps; } < <(jq -r '
      def num: if type == "number" then floor | tostring else "" end;
      def str: if type == "string" then gsub("[\r\n]"; " ") else "" end;
      ((if (.ended | type) == "number" then .ended else .started end) | num), (.state | str), (.pid | num),
      (.pid_start | str)' "$f" 2>/dev/null | tr -d '\r') || true
  fi
  if { [ "$state" = starting ] || [ "$state" = running ]; } && [ -n "$pid" ] && alive "$pid" "$ps"; then return 1; fi
  if [ -n "$t" ]; then
    [ $((now - t)) -gt "$secs" ]
  else
    [ -n "$(find "$1" -maxdepth 0 -mmin +$((secs / 60)) 2>/dev/null)" ]
  fi
}

count=0 rc=0
for base in "$common/remote-agent" "$common"/worktrees/*/remote-agent; do
  # A link here, or to the worktree's folder, would lead out of the repo's records.
  [ -d "$base" ] && [ ! -L "$base" ] && [ ! -L "$(dirname "$base")" ] || continue
  for run in "$base"/*; do
    [ -e "$run" ] || [ -L "$run" ] || continue
    name="$(basename "$run")"
    if [[ ! "$name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then echo "remote-agent: left, not a run name: $run" >&2; continue; fi
    [ -d "$run" ] && [ ! -L "$run" ] || continue
    old "$run" || continue
    if [ -n "$dry" ]; then
      echo "would remove $run"
    elif rm -rf -- "$run" 2>/dev/null && [ ! -e "$run" ]; then
      echo "removed $run"
    else
      echo "remote-agent: could not remove $run" >&2
      rc=1
      continue
    fi
    count=$((count + 1))
  done
done
if [ -n "$dry" ]; then echo "would remove $count runs"; else echo "removed $count runs"; fi
exit "$rc"
