#!/usr/bin/env bash
# Runs one subagent task on a remote sandbox for compute only, and applies its changes to a local worktree. The
# sandbox comes from a backend (scripts/remote-backends/<name>.sh; the cluster by default) and never touches a
# repo: it gets this machine's setup and a snapshot of the worktree, and only a patch comes back.
# Usage: remote-agent [--attach PATH]... [--name NAME] <brief | @brief-file | -> [worktree]   (default worktree: the
# current one)
#        remote-agent setup   (asks for the backend and the Claude token, writes remote.env: remote-agent-setup.sh)
#        remote-agent check   (checks the backend's needs, then a dry run with no Claude call: remote-agent-check.sh)
# install.sh links it as ~/.agents/bin/remote-agent. --attach copies a file or folder the brief needs from outside
# the worktree (a ticket, a run folder) to ~/attached/<its name> in the sandbox, read-only: only the worktree's
# changes come back. Secret-looking files inside it are left out. --name names the task (e.g. a ticket's NN-slug),
# turned into a label value (lowercase, [a-z0-9-], at most 63 characters): the backend labels the sandbox with it
# (cluster: sandbox.users.io/remote-agent-task) and every progress line on stderr starts with [NAME] ([<run>] without it).
# Steps: stage the setup (scripts/remote-agent-pack.sh); list the worktree's tracked and unignored files, minus
# secret-looking ones; acquire a sandbox from the backend; put both in and the Claude token through stdin;
# run scripts/remote-agent-sandbox.sh there; get the patch back and apply it with `git apply`, uncommitted.
# The sandbox is released on exit, also on INT or TERM, once the run's processes here (the backend's client among
# them) are stopped; the backend's time limit (REMOTE_HOURS) removes it anyway if this script dies first.
# Prints Claude's progress on stderr and the summary on stdout. The progress also goes to the sandbox's own log (its
# main process's stdout, so `kubectl logs` shows it; ~/progress.log in the sandbox when there is none, and always in
# SSH bare mode), with how long the claim, the send and the first agent event took. Keeps the patch, the summary
# and Claude's full stream in <git dir>/remote-agent/<run>/, plus transcript.jsonl: Claude's raw stream-json events,
# written as they arrive (the token's value redacted), its path printed before the sandbox is claimed, and
# session.jsonl: Claude's session (the token redacted), also placed under ${CLAUDE_CONFIG_DIR:-~/.claude}/projects/ in
# the worktree's project folder, so `/resume` in the worktree opens it.
# Exit 3 when Claude changed nothing; exit 4 when the patch does not apply (it is kept for a manual look).
# Config: CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`) from the environment or ~/.config/dotagents/remote.env.
# Overrides: REMOTE_BACKEND (cluster), REMOTE_HOURS (2), REMOTE_MAX_MB (200, snapshot size cap), REMOTE_KEEP=1
# (keep the sandbox), and the backend's own settings (see its script; cluster: REMOTE_CONTEXT, REMOTE_NAMESPACE,
# REMOTE_POOL). Every REMOTE_* variable is exported to the backend; the Claude token is not.
set -euo pipefail

# Follow the link from ~/.agents/bin to this repo, where the sibling scripts live.
src="${BASH_SOURCE[0]}"
while [ -L "$src" ]; do
  dir="$(cd "$(dirname "$src")" && pwd)"
  src="$(readlink "$src")"
  [[ "$src" == /* ]] || src="$dir/$src"
done
REPO="$(cd "$(dirname "$src")/.." && pwd)"
case "${1:-}" in
  setup) shift; exec "$REPO/scripts/remote-agent-setup.sh" "$@" ;;
  check) shift; exec "$REPO/scripts/remote-agent-check.sh" "$@" ;;
esac
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/dotagents"
# shellcheck source=hooks/lib.sh disable=SC1091
. "$REPO/scripts/hooks/lib.sh"
# shellcheck disable=SC1091
[ -f "$CONFIG/remote.env" ] && . "$CONFIG/remote.env"

usage() { echo "usage: remote-agent [--attach PATH]... [--name NAME] <brief | @brief-file | -> [worktree]" >&2; exit 1; }
attach=()
name=""
while [ $# -gt 0 ]; do
  case "$1" in
    --attach)
      if [ $# -lt 2 ] || [ ! -e "$2" ]; then echo "remote-agent: --attach needs an existing path: ${2:-}" >&2; exit 1; fi
      attach+=("$2")
      ;;
    --name)
      [ $# -ge 2 ] || usage
      # A valid label value: lowercase, [a-z0-9-], starting and ending with a letter or digit, at most 63 characters.
      name="$(printf '%s' "$2" | tr '[:upper:]' '[:lower:]' | sed -e 's/[^a-z0-9-]/-/g' -e 's/--*/-/g' -e 's/^-//' \
        | cut -c1-63 | sed 's/-$//')"
      [ -n "$name" ] || { echo "remote-agent: --name needs a letter or digit: $2" >&2; exit 1; }
      ;;
    *) break ;;
  esac
  shift 2
done
if [ $# -lt 1 ] || [ $# -gt 2 ]; then usage; fi
case "$1" in
  -) brief="$(cat)" ;;
  @*) brief="$(cat "${1#@}")" ;;
  *) brief="$1" ;;
esac
[ -n "$brief" ] || { echo "remote-agent: empty brief" >&2; exit 1; }
root="$(git -C "${2:-$PWD}" rev-parse --show-toplevel 2>/dev/null)" || { echo "remote-agent: not in a git worktree: ${2:-$PWD}" >&2; exit 1; }
claude_token="${CLAUDE_CODE_OAUTH_TOKEN:?set CLAUDE_CODE_OAUTH_TOKEN (in $CONFIG/remote.env)}"

backend_name="${REMOTE_BACKEND:-cluster}"
known=()
for f in "$REPO"/scripts/remote-backends/*.sh; do
  [ "$(basename "$f")" = lib.sh ] || known+=("$(basename "$f" .sh)")
done
backend="$REPO/scripts/remote-backends/$backend_name.sh"
if [ "$backend_name" = lib ] || [[ "$backend_name" == */* ]] || [ ! -f "$backend" ]; then
  echo "remote-agent: unknown REMOTE_BACKEND=$backend_name (known: ${known[*]})" >&2
  exit 1
fi
# The backend reads its settings from REMOTE_* variables, sourced or not.
export REMOTE_TASK="$name"
while IFS= read -r v; do export "${v?}"; done < <(compgen -v REMOTE_)
max_mb="${REMOTE_MAX_MB:-200}"

stage="$(mktemp -d)"
run="agent-$(date +%Y%m%d-%H%M%S)-$RANDOM"
tag="[${name:-$run}]"
# What is kept of the run, outside the worktree: the live transcript from the start, the sandbox's ~/out at the end.
keep="$(git -C "$root" rev-parse --absolute-git-dir)/remote-agent/$run"
mkdir -p "$keep"
: >>"$keep/transcript.jsonl"

# Progress: each line on stdin goes to stderr behind the tag and, without --no-log, to $stage/log, which
# send_log copies to the sandbox's log. The sandbox logs its own run's lines itself.
progress() {
  local line
  while IFS= read -r line || [ -n "$line" ]; do
    printf '%s %s\n' "$tag" "$line" >&2
    [ "${1:-}" = --no-log ] || printf '%s\n' "$line" >>"$stage/log"
  done
}
say() { echo "remote-agent: $*" | progress; }
# Runs a command with its stderr through progress; its stdout and exit code are kept.
with_progress() {
  local opt=""
  if [ "$1" = --no-log ]; then opt="$1"; shift; fi
  { "$@" 2>&1 >&4 | progress ${opt:+"$opt"}; } 4>&1
}
# The sandbox's log is REMOTE_AGENT_LOG when the backend's run sets it, else its main process's stdout
# (/proc/1/fd/1 in a pod or container), else ~/progress.log; scripts/remote-agent-sandbox.sh picks it the same way.
send_log() {
  [ -n "$handle" ] && [ -s "$stage/log" ] || return 0
  # shellcheck disable=SC2016 # expanded in the sandbox
  "$backend" run "$run" "$handle" sh -c 'l="${REMOTE_AGENT_LOG:-/proc/1/fd/1}"; [ -w "$l" ] || l="$HOME/progress.log"; cat >>"$l"' \
    <"$stage/log" >/dev/null 2>&1 || true
  : >"$stage/log"
}

# Appends the JSON lines on stdin to file $1 as they come, each flushed, with the Claude token's value (through the
# environment, never argv) replaced by [redacted]; anything else (an ssh banner, a kubectl warning) is dropped.
# awk marks the first event on its stderr, which becomes the first-event time (t0: just before the run).
transcript_to() {
  # mawk (Debian's awk) reads a pipe in blocks unless told otherwise.
  local live=()
  case "$(awk -W version </dev/null 2>&1)" in *mawk*) live=(-W interactive) ;; esac
  scrub_token="$claude_token" awk ${live[@]+"${live[@]}"} '
    BEGIN { tok = ENVIRON["scrub_token"] }
    /^\{/ {
      s = $0; out = ""
      while (tok != "" && (i = index(s, tok)) > 0) { out = out substr(s, 1, i - 1) "[redacted]"; s = substr(s, i + length(tok)) }
      print out s; fflush()
      if (!seen++) { print "first" >"/dev/stderr"; fflush("/dev/stderr") }
    }' 2>&1 >>"$1" | while IFS= read -r line; do
    line="${line%$'\r'}"
    if [ "$line" = first ]; then say "first agent event after $((SECONDS - t0))s"; else say "$line"; fi
  done
}

acquired=""
handle=""
runner=""
cleanup() {
  # The run is its own process group (see below): stop all of it, the backend's client included.
  if [ -n "$runner" ]; then kill -TERM -- "-$runner" 2>/dev/null || kill "$runner" 2>/dev/null || true; fi
  send_log
  [ -z "$acquired" ] || with_progress "$backend" release "$run" || true
  rm -rf "$stage"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# The setup, the attachments and the snapshot are built before anything reaches the backend.
with_progress "$REPO/scripts/remote-agent-pack.sh" "$stage/home"
for a in "${attach[@]+"${attach[@]}"}"; do
  name="$(basename "$a")"
  [ ! -e "$stage/home/attached/$name" ] || { say "two attachments named $name"; exit 1; }
  mkdir -p "$stage/home/attached"
  cp -RL "$a" "$stage/home/attached/$name"
  while IFS= read -r -d '' f; do
    if is_secret "${f#"$stage/home/attached/"}"; then rm -f "$f"; say "left out attached/${f#"$stage/home/attached/"} (looks like a secret)"; fi
  done < <(find "$stage/home/attached/$name" -type f -print0)
  say "attached $a as ~/attached/$name"
done
: >"$stage/files"
while IFS= read -r -d '' f; do
  [ -f "$root/$f" ] || continue # tracked but deleted locally
  if is_secret "$f"; then say "left out $f (looks like a secret)"; continue; fi
  printf '%s\0' "$f" >>"$stage/files"
done < <(git -C "$root" ls-files -co --exclude-standard -z)
count="$(tr -cd '\0' <"$stage/files" | wc -c | tr -d ' ')"
kb=0
# Guarded: with no input, GNU xargs would still run `du` and measure the whole directory.
[ "$count" -eq 0 ] || kb="$(cd "$root" && xargs -0 du -k <"$stage/files" 2>/dev/null | awk '{s += $1} END {print s + 0}')"
[ "$kb" -le $((max_mb * 1024)) ] || { say "snapshot is $((kb / 1024)) MB, over REMOTE_MAX_MB=$max_mb"; exit 1; }
say "snapshot: $count files, $((kb / 1024)) MB from $root"

say "transcript: $keep/transcript.jsonl"
acquired=1
t0=$SECONDS
handle="$(with_progress "$backend" acquire "$run")"
say "claimed in $((SECONDS - t0))s"

# macOS tar would add AppleDouble files and xattr headers.
export COPYFILE_DISABLE=1
t0=$SECONDS
tar -C "$stage/home" -chf - . | with_progress "$backend" put "$run" "$handle" .
(cd "$root" && tar -cf - --null -T "$stage/files") | with_progress "$backend" put "$run" "$handle" work
say "sent setup and snapshot in $((SECONDS - t0))s"
send_log

# In the background, so INT or TERM reach the traps at once rather than when the run ends; with job control on, in
# a process group of its own (numbered $runner), so cleanup stops the backend's client (ssh, docker, kubectl) with it.
# The run's stdout is Claude's raw events: transcript_to keeps them, and says when the first one comes.
status=0
t0=$SECONDS
set -m
(
  { printf '%s\n' "$claude_token"; printf '%s' "$brief"; } \
    | with_progress --no-log "$backend" run "$run" "$handle" bash -c "$(cat "$REPO/scripts/remote-agent-sandbox.sh")" remote-agent-sandbox \
    | transcript_to "$keep/transcript.jsonl"
) &
runner=$!
set +m
wait "$runner" || status=$?
runner=""

# Drained after extracting, so the sender's trailing padding does not hit a closed pipe (tar stops at the end marker).
with_progress "$backend" get "$run" "$handle" out | { tar -xf - -C "$keep" 2>/dev/null; cat >/dev/null; } || true
# Claude's session goes under this machine's Claude config, in the project folder for the worktree's real path (every
# character outside [A-Za-z0-9] made -), its cwd made that path, so /resume in the worktree opens it. The copy in
# $keep stays as it came.
session_id=""
[ ! -s "$keep/session.jsonl" ] || session_id="$(jq -rn 'first(inputs | .sessionId? // empty)' "$keep/session.jsonl" 2>/dev/null | tr -d '\r')" || true
if [[ "$session_id" =~ ^[A-Za-z0-9-]+$ ]]; then
  real="$(cd "$root" && pwd -P)"
  dest="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/$(printf '%s' "$real" | LC_ALL=C sed 's/[^A-Za-z0-9]/-/g')"
  mkdir -p "$dest"
  # As a JSON string: Git Bash would turn an argument starting with / into a Windows path for jq.exe.
  w="$(printf '%s' "$real" | jq -Rs . | tr -d '\r')"
  if jq -c --argjson w "$w" 'if .cwd? then .cwd = $w else . end' "$keep/session.jsonl" | tr -d '\r' >"$dest/$session_id.jsonl"; then
    say "session: $dest/$session_id.jsonl (/resume in $real opens it)"
  else
    rm -f "$dest/$session_id.jsonl"
    say "session not placed (not JSON lines?); it is in $keep/session.jsonl"
  fi
else
  say "no session came back: the run cannot be resumed"
fi
[ "$status" -eq 0 ] || { say "the sandbox run failed (exit $status); what it left is in $keep"; exit "$status"; }

[ -f "$keep/summary.md" ] && cat "$keep/summary.md"
if [ ! -s "$keep/changes.patch" ]; then
  say "no changes"
  exit 3
fi
if ! with_progress git -C "$root" apply --whitespace=nowarn "$keep/changes.patch"; then
  say "the patch does not apply to $root (did the worktree change meanwhile?); it is in $keep/changes.patch"
  exit 4
fi
say "applied to $root (uncommitted): $(git -C "$root" apply --stat "$keep/changes.patch" | tail -1)"
