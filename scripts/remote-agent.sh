#!/usr/bin/env bash
# Runs one subagent task on a remote sandbox for compute only, and applies its changes to a local worktree. The
# sandbox comes from a backend (scripts/remote-backends/<name>.sh; the cluster by default) and never touches a
# repo: it gets this machine's setup and a snapshot of the worktree, and only a patch comes back.
# Usage: remote-agent [--attach PATH]... [--name NAME] [--] <brief | @brief-file | -> [worktree]   (default worktree:
# the current one; - reads the brief from stdin; -h or --help prints this usage on stdout and exits 0; any other
# argument starting with -- before the brief is refused with the usage, exit 1, and so is a brief, however given,
# whose text starts with -, which claude would read as an option)
#        remote-agent setup   (asks for the backend, its Settings and the Claude token: remote-agent-setup.sh)
#        remote-agent check   (checks the backend's needs, then a dry run with no Claude call: remote-agent-check.sh)
#        remote-agent resume <run> [worktree]   (places a run's kept session for /resume in the worktree: see below)
#        remote-agent result <name|run> [dir]   (prints the result.json of run <run>, or of the newest run named
#                                                <name> by --name, of the repo at dir, default the current one; one
#                                                line on stderr and exit 1 when none matches or it has not ended)
#        remote-agent prune --older-than <Nd|Nh> [--dry-run] [dir]   (removes the run records of the repo at dir older
#                                                than N days or hours, never a live run: remote-agent-prune.sh)
#        remote-agent ready [dir]   (whether skills may send the subagents of the repo at dir, default the current
#                                    one, to the backend: checks the Settings and the token, then probes the backend
#                                    (`<backend> ping`, at most a few seconds: kubectl version, docker info, an ssh
#                                    login); prints the backend on stdout and exits 0, else one line on stdout saying
#                                    why not, e.g. "cluster backend not reachable: <the connection error>", and exits 1)
# install.sh links it as ~/.agents/bin/remote-agent. --attach copies a file or folder the brief needs from outside
# the worktree (a ticket, a run folder) to ~/attached/<its name> in the sandbox, read-only: only the worktree's
# changes come back. Secret-looking files inside it are left out. --name names the task (e.g. a ticket's NN-slug),
# turned into a label value (lowercase, [a-z0-9-], at most 63 characters): the backend labels the sandbox with it
# (cluster: sandbox.users.io/remote-agent-task) and every progress line on stderr starts with [NAME] ([<run>] without it).
# Steps: stage the setup (scripts/remote-agent-pack.sh); list the worktree's tracked and unignored files, minus
# secret-looking ones; acquire a sandbox from the backend; put both in and the Claude token through stdin;
# run scripts/remote-agent-sandbox.sh there; get the patch back and apply it with `git apply`, uncommitted.
# The sandbox is released on exit, also on INT or TERM, once the run's processes here (the backend's client among
# them) are stopped; the backend's time limit (REMOTE_HOURS) removes it anyway if this script dies first (SSH bare
# mode: the first acquire 10 minutes past it kills the run and removes its folder).
# Prints Claude's progress on stderr and the summary on stdout. The progress also goes to the sandbox's own log (its
# main process's stdout, so `kubectl logs` shows it; ~/progress.log in the sandbox when there is none, and always in
# SSH bare mode), with how long the claim, the send and the first agent event took. Keeps the patch, the summary
# and Claude's full stream in <common git dir>/remote-agent/<run>/ (shared by every worktree of the repo, so it stays
# when a linked worktree is removed; <run> is never one another run took), plus transcript.jsonl: Claude's raw stream-json events,
# written as they arrive, its path printed before the sandbox is claimed, and session.jsonl: Claude's session. The
# token's value is redacted in every kept file. The session is not placed for /resume at the end: the sandbox wrote it,
# so it may hold forged user turns. `remote-agent resume <run>` places it under ${CLAUDE_CONFIG_DIR:-~/.claude}/projects/
# in the worktree's project folder, its cwd the worktree, after a warning saying so. Beside the transcript, for any reader:
# run.json (run, name, worktree, started, pid, pid_start, state, exit_code, ended, claim_s, send_s, first_event_s;
# state starting, running, then done on exit 0 or 3, else failed; rewritten atomically) and progress.log (every
# progress line, untagged), from the moment the folder is made; on every exit, result.json (scripts/remote-agent-result.sh:
# exit_code, status, report_status, changed, follow_up, report_path, duration_ms, num_turns, total_cost_usd, usage,
# model, complete; unknowns null), through a temporary file and `mv`, the token's value redacted.
# Exit 3 when Claude changed nothing; exit 4 when the patch does not apply (it is kept for a manual look); exit 5 when
# the run failed because the Claude account hit its usage limit, with one line on stderr: [<task>] usage limit: resets
# <time> (<time> as Claude gave it, e.g. 5:30pm (UTC), or unknown). Any other failed run exits with the sandbox's code,
# 1 in place of 3, 4 or 5, so those three always mean the above. The summary (Claude's final report) is printed once,
# on stdout only, and only on exit 0, 3 or 4; stderr carries the progress lines, never the report.
# Settings: CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`) from ~/.config/dotagents/remote.env or the environment;
# the remote Settings (remote/settings.json: backend, hours, max_mb, keep, and the backend's own) from
# scripts/settings.sh, each exported as its REMOTE_* variable, which also overrides it for one run (REMOTE_KEEP=1).
# Refused before anything is staged when no backend is set or the worktree's repo sets remote.allowed to false.
# Every REMOTE_* variable is exported to the backend; the Claude token is not. REMOTE_* lines left in remote.env from
# before settings.json are ignored, with a line saying so.
set -euo pipefail

# Follow the link from ~/.agents/bin to this repo, where the sibling scripts live.
src="${BASH_SOURCE[0]}"
while [ -L "$src" ]; do
  dir="$(cd "$(dirname "$src")" && pwd)"
  src="$(readlink "$src")"
  [[ "$src" == /* ]] || src="$dir/$src"
done
REPO="$(cd "$(dirname "$src")/.." && pwd)"

# Prints NAME as a label value: lowercase, [a-z0-9-], starting and ending with a letter or digit, at most 63
# characters; empty when NAME has no letter or digit.
task_label() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | sed -e 's/[^a-z0-9-]/-/g' -e 's/--*/-/g' -e 's/^-//' \
    | cut -c1-63 | sed 's/-$//'
}

# Prints the result.json of run $1, or of the newest run (by run.json's started) whose name is $1 as --name writes it,
# among the records of the repo at ${2:-$PWD}: its common git dir's, and those kept before records moved there in a
# linked worktree's own git dir. Fails with one line on stderr when no run matches, or the newest has no result yet.
result() {
  local q label common dir best="" best_t="" t n
  if [ $# -lt 1 ] || [ $# -gt 2 ]; then echo "usage: remote-agent result <name|run> [dir]" >&2; exit 1; fi
  q="$1"
  if [[ ! "$q" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then echo "remote-agent: not a task or run name: $q" >&2; exit 1; fi
  common="$(git -C "${2:-$PWD}" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" \
    || { echo "remote-agent: not in a git worktree: ${2:-$PWD}" >&2; exit 1; }
  label="$(task_label "$q")"
  for dir in "$common"/remote-agent/* "$common"/worktrees/*/remote-agent/*; do
    [ -f "$dir/run.json" ] || continue
    if [ "$(basename "$dir")" = "$q" ]; then best="$dir"; break; fi
    n="$(jq -r 'if (.name | type) == "string" and (.started | type) == "number" then "\(.name) \(.started)" else empty end' \
      "$dir/run.json" 2>/dev/null | tr -d '\r')" || continue
    [ "${n% *}" = "$label" ] || continue
    t="${n##* }"
    # The later start wins; on a tie, the run.json written last.
    if [ -z "$best" ] || [ "$t" -gt "$best_t" ] || { [ "$t" -eq "$best_t" ] && [ "$dir/run.json" -nt "$best/run.json" ]; }; then
      best="$dir" best_t="$t"
    fi
  done
  [ -n "$best" ] || { echo "remote-agent: no run named $q in $common/remote-agent" >&2; exit 1; }
  [ -f "$best/result.json" ] || { echo "remote-agent: $(basename "$best") has no result yet (still running?): $best" >&2; exit 1; }
  cat "$best/result.json"
}

# Places the session run $1 kept (<common git dir>/remote-agent/<run>/session.jsonl, or, for a run from before records
# moved there, <common git dir>/worktrees/<name>/remote-agent/<run>/session.jsonl) where Claude Code looks for worktree
# ${2:-$PWD}'s sessions: projects/<the worktree's real path, every character outside [A-Za-z0-9] made ->, its Windows
# path under Git Bash, as Claude Code there sees it>, its cwd lines made that path. Warns before placing.
resume() {
  local run root common keep old id real dir w tmp dest
  if [ $# -lt 1 ] || [ $# -gt 2 ]; then echo "usage: remote-agent resume <run> [worktree]" >&2; exit 1; fi
  run="$1"
  if [[ ! "$run" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then echo "remote-agent: not a run name: $run" >&2; exit 1; fi
  root="$(git -C "${2:-$PWD}" rev-parse --show-toplevel 2>/dev/null)" || { echo "remote-agent: not in a git worktree: ${2:-$PWD}" >&2; exit 1; }
  common="$(git -C "$root" rev-parse --path-format=absolute --git-common-dir)"
  keep="$common/remote-agent/$run"
  # A run from before records moved to the common git dir is in its linked worktree's own git dir.
  if [ ! -s "$keep/session.jsonl" ]; then
    for old in "$common"/worktrees/*/remote-agent/"$run"; do
      if [ -s "$old/session.jsonl" ]; then keep="$old"; break; fi
    done
  fi
  [ -s "$keep/session.jsonl" ] || { echo "remote-agent: no session kept in $keep" >&2; exit 1; }
  id="$(jq -rn 'first(inputs | .sessionId? // empty)' "$keep/session.jsonl" 2>/dev/null | tr -d '\r')" || true
  [[ "$id" =~ ^[A-Za-z0-9-]+$ ]] || { echo "remote-agent: no session id in $keep/session.jsonl" >&2; exit 1; }
  real="$(cd "$root" && pwd -P)"
  if command -v cygpath >/dev/null 2>&1; then real="$(cygpath -w "$real")"; fi
  dir="$(printf '%s' "$real" | LC_ALL=C sed 's/[^A-Za-z0-9]/-/g')"
  # Claude Code shortens a longer name with a hash of its own, so a folder named in full would never be read.
  if [ "${#dir}" -gt 200 ]; then
    echo "remote-agent: session not placed: the project folder name for $real is ${#dir} characters, over Claude Code's 200, so /resume would not find it; it is in $keep/session.jsonl" >&2
    exit 1
  fi
  echo "remote-agent: warning: this session comes from an untrusted sandbox. The remote agent could have written turns in it that read as yours; /resume loads them as your own history, run with your credentials. Read $keep/session.jsonl before you go on in it." >&2
  # As a JSON string: Git Bash would turn an argument starting with / into a Windows path for jq.exe.
  w="$(printf '%s' "$real" | jq -Rs . | tr -d '\r')"
  tmp="$keep/session.placing"
  if ! jq -c --argjson w "$w" 'if type == "object" and has("cwd") then .cwd = $w else . end' "$keep/session.jsonl" | tr -d '\r' >"$tmp"; then
    rm -f "$tmp"
    echo "remote-agent: session not placed (not JSON lines?); it is in $keep/session.jsonl" >&2
    exit 1
  fi
  dest="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/$dir"
  mkdir -p "$dest"
  mv "$tmp" "$dest/$id.jsonl"
  echo "remote-agent: session: $dest/$id.jsonl (/resume in $real opens it, or claude --resume $id)" >&2
}
case "${1:-}" in
  setup) shift; exec "$REPO/scripts/remote-agent-setup.sh" "$@" ;;
  check) shift; exec "$REPO/scripts/remote-agent-check.sh" "$@" ;;
  resume) shift; resume "$@"; exit 0 ;;
  result) shift; result "$@"; exit 0 ;;
  prune) shift; exec "$REPO/scripts/remote-agent-prune.sh" "$@" ;;
esac
# shellcheck source=hooks/lib.sh disable=SC1091
. "$REPO/scripts/hooks/lib.sh"
# shellcheck source=remote-settings.sh disable=SC1091
. "$REPO/scripts/remote-settings.sh"

# Sets backend to the script of REMOTE_BACKEND and exports every REMOTE_* variable, which the backend reads its
# Settings from; fails for an unknown backend, with why set to say so.
backend_script() {
  local f known=()
  for f in "$REPO"/scripts/remote-backends/*.sh; do
    f="${f##*/}"
    [ "$f" = lib.sh ] || known+=("${f%.sh}")
  done
  backend="$REPO/scripts/remote-backends/$REMOTE_BACKEND.sh"
  if [ "$REMOTE_BACKEND" = lib ] || [[ "$REMOTE_BACKEND" == */* ]] || [ ! -f "$backend" ]; then
    why="unknown REMOTE_BACKEND=$REMOTE_BACKEND (known: ${known[*]})"
    return 1
  fi
  while IFS= read -r v; do export "${v?}"; done < <(compgen -v REMOTE_)
}

if [ "${1:-}" = ready ]; then
  [ $# -le 2 ] || { echo "usage: remote-agent ready [dir]" >&2; exit 1; }
  dir="${2:-$PWD}"
  remote_settings "$dir" || exit 1
  why="$(remote_blocker "$dir")" || exit 1
  [ -z "$why" ] || { echo "$why"; exit 1; }
  [ -n "$(remote_token)" ] || { echo "no CLAUDE_CODE_OAUTH_TOKEN in $REMOTE_SECRET_FILE: run \`remote-agent setup\`"; exit 1; }
  backend_script || { echo "$why"; exit 1; }
  # The backend's cheap probe: its one line of error, e.g. the cluster's connection error.
  if ! why="$("$backend" ping </dev/null 2>/dev/null)"; then
    why="$(printf '%s\n' "$why" | grep . | tail -1 || true)"
    echo "$REMOTE_BACKEND backend not reachable: ${why:-its probe failed}"
    exit 1
  fi
  echo "$REMOTE_BACKEND"
  exit 0
fi

usage_text() {
  cat <<'EOF'
usage: remote-agent [--attach PATH]... [--name NAME] [--] <brief | @brief-file | -> [worktree]
       remote-agent setup | check | ready [dir] | resume <run> [worktree] | result <name|run> [dir]
       remote-agent prune --older-than <Nd|Nh> [--dry-run] [dir]
Runs one subagent task on the remote backend and applies its patch to the worktree, uncommitted. - reads the brief
from stdin. Exit 0 applied, 3 no changes, 4 the patch does not apply, 5 the Claude account hit its usage limit.
EOF
}
usage() { usage_text >&2; exit 1; }
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
      name="$(task_label "$2")"
      [ -n "$name" ] || { echo "remote-agent: --name needs a letter or digit: $2" >&2; exit 1; }
      ;;
    -h | --help) usage_text; exit 0 ;;
    --) shift; break ;;
    # An unknown option would otherwise become the brief and start a real run.
    --*) echo "remote-agent: unknown option: $1" >&2; usage ;;
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
# The sandbox runs `claude -p "$brief"`, which would read a leading "-" as its own option (`--help` prints Claude's
# usage and the run fails): refuse it here, before anything reaches the sandbox.
case "$brief" in -*) echo "remote-agent: the brief starts with \"-\", which claude would read as an option: ${brief%%$'\n'*}" >&2; exit 1 ;; esac
root="$(git -C "${2:-$PWD}" rev-parse --show-toplevel 2>/dev/null)" || { echo "remote-agent: not in a git worktree: ${2:-$PWD}" >&2; exit 1; }
claude_token="$(remote_token)"
[ -n "$claude_token" ] || { echo "remote-agent: set CLAUDE_CODE_OAUTH_TOKEN (in $REMOTE_SECRET_FILE): run \`remote-agent setup\`" >&2; exit 1; }
remote_settings "$root" || exit 1
why="$(remote_blocker "$root")" || exit 1
[ -z "$why" ] || { echo "remote-agent: $why" >&2; exit 1; }
leftovers="$(remote_leftovers)"

# The backend reads its settings from REMOTE_* variables, sourced or not.
export REMOTE_TASK="$name"
backend_script || { echo "remote-agent: $why" >&2; exit 1; }
max_mb="${REMOTE_MAX_MB:-200}"

# What is kept of the run, outside the worktree: the live transcript from the start, the sandbox's ~/out at the end.
# In the common git dir, shared by every worktree of the repo, so removing a linked worktree keeps it. The run folder
# is made without -p, which fails when it is there: an id another run took is drawn again.
records="$(git -C "$root" rev-parse --path-format=absolute --git-common-dir)/remote-agent"
mkdir -p "$records"
tries=0
while :; do
  n="$(od -An -N2 -tu2 /dev/urandom 2>/dev/null | tr -dc '0-9')" || n=""
  run="agent-$(date +%Y%m%d-%H%M%S)-${n:-$RANDOM}"
  keep="$records/$run"
  mkdir "$keep" 2>/dev/null && break
  tries=$((tries + 1))
  [ "$tries" -lt 20 ] || { echo "remote-agent: cannot make a run folder in $records" >&2; exit 1; }
done
stage="$(mktemp -d)"
tag="[${name:-$run}]"
: >>"$keep/transcript.jsonl"

# Rewrites run.json through jq filter $1 (jq options after it), by way of run.json.<run_json_tmp> and `mv`, so a
# reader never sees a partial file. One temporary file per writer: the run's own subshell writes first_event_s.
# Never fails the run, and holds only the fields below: never the brief or the token.
run_json_tmp=tmp
run_json() {
  local f="$keep/run.json" tmp="$keep/run.json.$run_json_tmp" filter="$1"
  shift
  { jq -c "$@" "$filter" "$f" | tr -d '\r' >"$tmp" && mv "$tmp" "$f"; } 2>/dev/null || rm -f "$tmp"
}
# run.json: what the run is and where it stands, for any reader. state: starting, running, then done (exit 0 or 3) or
# failed (any other exit); starting or running with a pid not alive (or alive with another pid_start) means
# remote-agent died without saying so. Timings are whole seconds, null until known.
{ printf '{}\n' >"$keep/run.json.tmp" && mv "$keep/run.json.tmp" "$keep/run.json"; } 2>/dev/null || true
run_name="$name" # before --attach reuses $name
# In the C locale: lstart follows it on macOS, and a reader compares it with its own ps.
pid_start="$(LC_ALL=C ps -o lstart= -p $$ 2>/dev/null | sed 's/^ *//; s/ *$//')" || pid_start=""
# The worktree as a JSON string: Git Bash would turn an argument starting with / into a Windows path for jq.exe.
# shellcheck disable=SC2016 # jq's variables
run_json '{run: $run, name: (if $name == "" then null else $name end), worktree: $w, started: $started, pid: $pid,
  pid_start: (if $ps == "" then null else $ps end), state: "starting", exit_code: null, ended: null, claim_s: null,
  send_s: null, first_event_s: null}' --arg run "$run" --arg name "$run_name" \
  --argjson w "$(printf '%s' "$root" | jq -Rs . | tr -d '\r')" --argjson started "$(date +%s)" --argjson pid "$$" \
  --arg ps "$pid_start"

# Progress: each line on stdin goes to stderr behind the tag, to $keep/progress.log untagged with the token's value
# redacted, and, without --no-log, to $stage/log, which send_log copies to the sandbox's log. The sandbox logs its own
# run's lines itself.
progress() {
  local line
  while IFS= read -r line || [ -n "$line" ]; do
    printf '%s %s\n' "$tag" "$line" >&2
    { printf '%s\n' "${line//"$claude_token"/[redacted]}" >>"$keep/progress.log"; } 2>/dev/null || true
    [ "${1:-}" = --no-log ] || printf '%s\n' "$line" >>"$stage/log"
  done
}
# A here-string, not a pipe: no subshell for each line.
say() { progress <<<"remote-agent: $*"; }
# Runs a command with its stderr through progress; its stdout and exit code are kept.
with_progress() {
  local opt=""
  if [ "$1" = --no-log ]; then opt="$1"; shift; fi
  { "$@" 2>&1 >&4 4>&- | progress ${opt:+"$opt"}; } 4>&1
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

# awk's redact(s): s with the Claude token's value (scrub_token in the environment, never argv) replaced by [redacted].
redact_awk='function redact(s,  out, i) {
  out = ""
  while (ENVIRON["scrub_token"] != "" && (i = index(s, ENVIRON["scrub_token"])) > 0) {
    out = out substr(s, 1, i - 1) "[redacted]"; s = substr(s, i + length(ENVIRON["scrub_token"]))
  }
  return out s
}'
# Writes file $1 to $2 with the token's value redacted.
scrub() {
  scrub_token="$claude_token" awk "$redact_awk"'
    { print redact($0) }' "$1" >"$2"
}
# Appends the JSON lines on stdin to file $1 as they come, each flushed, the token's value redacted; anything else
# (an ssh banner, a kubectl warning) is dropped. awk marks the first event on its stderr, which becomes the
# first-event time (t0: just before the run).
transcript_to() {
  # mawk (Debian's awk) reads a pipe in blocks unless told otherwise.
  local live=()
  case "$(awk -W version </dev/null 2>&1)" in *mawk*) live=(-W interactive) ;; esac
  scrub_token="$claude_token" awk ${live[@]+"${live[@]}"} "$redact_awk"'
    /^\{/ {
      print redact($0); fflush()
      if (!seen++) { print "first" >"/dev/stderr"; fflush("/dev/stderr") }
    }' 2>&1 >>"$1" | while IFS= read -r line; do
    line="${line%$'\r'}"
    if [ "$line" = first ]; then
      line="$((SECONDS - t0))"
      say "first agent event after ${line}s"
      run_json_tmp=first
      # shellcheck disable=SC2016 # jq's variables
      run_json '.first_event_s = $s' --argjson s "$line"
    else
      say "$line"
    fi
  done
}

# Prints when the account's usage limit resets (Claude's words after "resets", a "|<epoch>" as a UTC time, else
# unknown) when transcript $1 shows the run stopped on it: an error result or an API-error assistant message whose
# text says the limit was hit; prints nothing otherwise. Ordinary assistant text never counts.
usage_limit() {
  # shellcheck disable=SC2016 # a literal $ in jq's regex
  jq -rRn '[inputs | fromjson? | objects
      | select((.type == "result" and .is_error == true) or (.type == "assistant" and .error != null))
      | (.result // ([.message.content[]? | select(.type == "text") | .text] | join(" "))) | strings
      | select(test("hit your (usage )?limit|usage limit|limit reached"; "i"))] | last // empty
    | if test("\\|[0-9]{9,}") then (capture("\\|(?<e>[0-9]{9,})").e | tonumber | strftime("%Y-%m-%d %H:%M UTC"))
      elif test("resets"; "i") then (capture("(?i)resets (at )?(?<t>[^\\n]*)").t | sub("[\\s.]+$"; ""))
      else "" end
    | if . == "" then "unknown" else . end' "$1" 2>/dev/null | tr -d '\r' | head -1
}

acquired=""
handle=""
runner=""
cleanup() {
  local rc=$? state=failed
  # The run is its own process group (see below): stop all of it, the backend's client included.
  if [ -n "$runner" ]; then kill -TERM -- "-$runner" 2>/dev/null || kill "$runner" 2>/dev/null || true; fi
  send_log
  [ -z "$acquired" ] || with_progress "$backend" release "$run" || true
  # Last, so a first_event_s write the stopped run had under way lands before it.
  case "$rc" in 0 | 3) state="done" ;; esac
  # shellcheck disable=SC2016 # jq's variables
  run_json '.state = $state | .exit_code = $rc | .ended = $ended' --arg state "$state" --argjson rc "$rc" \
    --argjson ended "$(date +%s)"
  rm -f "$keep/run.json.tmp" "$keep/run.json.first"
  # What the run came to, from the files above, the token's value redacted; never fails the run.
  { "$REPO/scripts/remote-agent-result.sh" "$keep" "$rc" \
      | scrub_token="$claude_token" awk "$redact_awk"' { print redact($0) }' >"$keep/result.json.tmp" \
      && mv "$keep/result.json.tmp" "$keep/result.json"; } 2>/dev/null || rm -f "$keep/result.json.tmp"
  rm -rf "$stage"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

[ -z "$leftovers" ] || say "remote.env still sets $leftovers, now ignored: run \`remote-agent setup\` to move them to settings.json"
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
took=$((SECONDS - t0))
say "claimed in ${took}s"
# shellcheck disable=SC2016 # jq's variables
run_json '.claim_s = $s' --argjson s "$took"

# macOS tar would add AppleDouble files and xattr headers.
export COPYFILE_DISABLE=1
t0=$SECONDS
# shellcheck disable=SC2154 # remote_tar: set by remote-settings.sh
"${remote_tar[@]}" -C "$stage/home" -chf - . | with_progress "$backend" put "$run" "$handle" .
(cd "$root" && "${remote_tar[@]}" -cf - --null -T "$stage/files") | with_progress "$backend" put "$run" "$handle" work
took=$((SECONDS - t0))
say "sent setup and snapshot in ${took}s"
# shellcheck disable=SC2016 # jq's variables
run_json '.send_s = $s' --argjson s "$took"
send_log

# In the background, so INT or TERM reach the traps at once rather than when the run ends; with job control on, in
# a process group of its own (numbered $runner), so cleanup stops the backend's client (ssh, docker, kubectl) with it.
# The run's stdout is Claude's raw events: transcript_to keeps them, and says when the first one comes.
status=0
run_json '.state = "running"'
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

# The sandbox's ~/out is unpacked in $stage/out, never in $keep: only its regular files of the names below are copied
# into $keep, so a link in it cannot aim a write of this script (run.json, progress.log) at another file, nor can it
# replace run.json. Drained after extracting, so the sender's trailing padding does not hit a closed pipe (tar stops
# at the end marker).
mkdir -p "$stage/out"
with_progress "$backend" get "$run" "$handle" out | { tar -xf - -C "$stage/out" 2>/dev/null; cat >/dev/null; } || true
for f in changes.patch stream.jsonl session.jsonl summary.md; do
  if [ -L "$stage/out/$f" ] || [ ! -f "$stage/out/$f" ]; then continue; fi
  # The sandbox redacts the token itself; a sandbox that did not, or a run that failed before it could, is caught here.
  if [ "$f" = changes.patch ]; then cp "$stage/out/$f" "$keep/$f"; else scrub "$stage/out/$f" "$keep/$f"; fi
done
# The session stays here: the sandbox wrote it, so placing it for /resume is the human's call (remote-agent resume).
if [ -s "$keep/session.jsonl" ]; then
  say "session: $keep/session.jsonl (from the sandbox, not placed for /resume; to place it: remote-agent resume $run $(printf '%q' "$root"))"
else
  say "no session came back: the run cannot be resumed"
fi
if [ "$status" -ne 0 ]; then
  say "the sandbox run failed (exit $status); what it left is in $keep"
  # The account's usage limit: Claude's error result (or the API error it shows as an assistant message) says so.
  when="$(usage_limit "$keep/transcript.jsonl")" || true
  if [ -n "$when" ]; then echo "usage limit: resets $when" | progress; exit 5; fi
  # 3, 4 and 5 are this script's own answers (no changes, stale patch, usage limit).
  case "$status" in 3 | 4 | 5) exit 1 ;; esac
  exit "$status"
fi

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
