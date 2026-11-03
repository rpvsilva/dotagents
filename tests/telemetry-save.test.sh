#!/usr/bin/env bash
# shellcheck disable=SC2016 # wait_for conditions are single-quoted on purpose: eval expands them when checked
# Runs scripts/hooks/telemetry-save.sh (the Claude Code SessionEnd hook) on hand-written payloads in throwaway homes,
# against the real extractor and stub ones, and checks what it saves and that it stays quiet and returns at once.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK="$REPO/scripts/hooks/telemetry-save.sh"
FIXTURES="$REPO/adapters/claude/telemetry/fixtures"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
SANDBOX="$(cd "$SANDBOX" && pwd -P)"
unset XDG_CONFIG_HOME TELEMETRY_EXTRACT
# Git Bash on Windows copies on `ln -s` unless told otherwise, as install.sh says.
export MSYS="${MSYS:+$MSYS }winsymlinks:nativestrict"
fails=0

fail() { echo "FAIL: $*"; fails=$((fails + 1)); }

# A throwaway home named $1, linked as install.sh links it: ~/.agents/hooks and ~/.agents/bin/settings.
home() {
  local h="$SANDBOX/$1"
  mkdir -p "$h/.agents/bin"
  ln -s "$REPO/scripts/hooks" "$h/.agents/hooks"
  ln -s "$REPO/scripts/settings.sh" "$h/.agents/bin/settings"
  echo "$h"
}

# A session transcript (a copy of the spans fixture, its subagents beside it) named $1, in a git repo at $SANDBOX/proj.
mkdir -p "$SANDBOX/proj" "$SANDBOX/sessions"
git -C "$SANDBOX/proj" init -q
transcript() {
  cp "$FIXTURES/spans.jsonl" "$SANDBOX/sessions/$1.jsonl"
  cp -R "$FIXTURES/spans" "$SANDBOX/sessions/$1"
  echo "$SANDBOX/sessions/$1.jsonl"
}
payload() { jq -n --arg t "$1" --arg c "${2:-$SANDBOX/proj}" '{session_id: "x", transcript_path: $t, cwd: $c, hook_event_name: "SessionEnd", reason: "other"}'; }

# Runs the hook through the home's ~/.agents/hooks link as $HOME=$1 on payload $2, $3 naming the case (extra env as
# KEY=value in $4...); checks it exits 0 and prints nothing.
# Command substitution also proves the background save holds neither stdout nor stderr open.
quiet() {
  local h="$1" json="$2" name="$3" out rc=0
  shift 3
  out="$(env HOME="$h" "$@" "$h/.agents/hooks/telemetry-save.sh" <<<"$json" 2>&1)" || rc=$?
  [ "$rc" -eq 0 ] || fail "$name: exit $rc"
  [ -z "$out" ] || fail "$name: printed: $out"
}

# A stub extractor: writes $SANDBOX/marks/$1 when it starts, then runs the Python in $2.
stub() {
  mkdir -p "$SANDBOX/marks"
  printf 'import json, sys, time\nopen("%s/marks/%s", "w").close()\n%s\n' "$SANDBOX" "$1" "$2" >"$SANDBOX/$1.py"
  echo "$SANDBOX/$1.py"
}

# Waits up to 10 s for the test $1 to hold.
wait_for() {
  local i=0
  while ! eval "$1"; do
    i=$((i + 1))
    [ "$i" -lt 100 ] || return 1
    sleep 0.1
  done
}

# A whole-session save lands as ~/.agents/telemetry/<id>.json, made by the real extractor through the linked hook.
h="$(home real)"
t="$(transcript sess-real)"
quiet "$h" "$(payload "$t")" "real save"
saved="$h/.agents/telemetry/sess-real.json"
if wait_for '[ -s "$saved" ]'; then
  jq -e '.whole == true' "$saved" >/dev/null || fail "real save: whole is not true"
  # Only the tail: on Windows, repo and the paths Python sees are native (C:\...).
  jq -e '.repo | test("[/\\\\]proj$")' "$saved" >/dev/null || fail "real save: repo is not $SANDBOX/proj: $(jq -c .repo "$saved")"
  jq -e '.sessionId == "sess-real"' "$saved" >/dev/null || fail "real save: wrong sessionId"
else
  fail "real save: $saved never appeared"
fi

# The hook returns while a slow extractor still runs, and its output appears afterwards. The extractor gets the
# transcript, the session's folder and --whole.
h="$(home slow)"
t="$(transcript sess-slow)"
x="$(stub slow 'time.sleep(3); open(sys.argv[0] + ".done", "w").close(); print(json.dumps({"argv": sys.argv[1:]}))')"
quiet "$h" "$(payload "$t")" "slow extractor" TELEMETRY_EXTRACT="$x"
[ ! -e "$x.done" ] || fail "slow extractor: the hook waited for the extractor to finish"
saved="$h/.agents/telemetry/sess-slow.json"
if wait_for '[ -s "$saved" ]'; then
  jq -e '.argv | length == 5 and .[0] == "--whole" and .[1] == "--session" and (.[2] | endswith("sess-slow.jsonl"))
    and .[3] == "--cwd" and (.[4] | endswith("proj"))' "$saved" >/dev/null \
    || fail "slow extractor: wrong arguments: $(cat "$saved")"
else
  fail "slow extractor: $saved never appeared"
fi

# A resumed session that ends again overwrites its file and leaves no temp file.
h="$(home again)"
t="$(transcript sess-again)"
for n in 1 2; do
  x="$(stub "again$n" "print(json.dumps({'n': $n}))")"
  quiet "$h" "$(payload "$t")" "end $n" TELEMETRY_EXTRACT="$x"
  wait_for "jq -e '.n == $n' '$h/.agents/telemetry/sess-again.json' >/dev/null 2>&1" || fail "end $n: file does not hold run $n"
done
wait_for '[ -z "$(ls -A "$h/.agents/telemetry" | grep -v "^sess-again.json$")" ]' || fail "second end left: $(ls -A "$h/.agents/telemetry")"

# An extractor failing (exit 1 or 2) or printing nothing saves nothing and leaves no temp file.
for c in 'exit1:print("partial"); sys.exit(1)' 'exit2:print("partial"); sys.exit(2)' 'empty:pass'; do
  name="${c%%:*}"
  h="$(home "$name")"
  t="$(transcript "sess-$name")"
  x="$(stub "$name" "${c#*:}")"
  quiet "$h" "$(payload "$t")" "extractor $name" TELEMETRY_EXTRACT="$x"
  wait_for '[ -e "$SANDBOX/marks/$name" ] && [ -z "$(ls -A "$h/.agents/telemetry" 2>/dev/null)" ]' \
    || fail "extractor $name: left $(ls -A "$h/.agents/telemetry" 2>/dev/null)"
done

# A bad settings file falls back to the default (on), quietly.
h="$(home badsettings)"
t="$(transcript sess-bad)"
mkdir -p "$h/.config/dotagents"
echo '{"telemetry": {"auto_save": "maybe"}}' >"$h/.config/dotagents/settings.json"
x="$(stub badsettings 'print("{}")')"
quiet "$h" "$(payload "$t")" "bad settings file" TELEMETRY_EXTRACT="$x"
wait_for '[ -s "$h/.agents/telemetry/sess-bad.json" ]' || fail "bad settings file: no save on the default"

# Cases that must save nothing, each with a stub extractor that would leave a mark if it ran.
h="$(home off)"
t="$(transcript sess-off)"
env HOME="$h" "$h/.agents/bin/settings" set telemetry auto_save false --machine >/dev/null
quiet "$h" "$(payload "$t")" "Setting off" TELEMETRY_EXTRACT="$(stub off 'print("{}")')"

h="$(home malformed)"
quiet "$h" 'not json {' "malformed payload" TELEMETRY_EXTRACT="$(stub malformed 'print("{}")')"
quiet "$h" '' "empty payload" TELEMETRY_EXTRACT="$(stub malformed 'print("{}")')"
quiet "$h" '{"cwd": 3}' "payload without transcript_path" TELEMETRY_EXTRACT="$(stub malformed 'print("{}")')"

h="$(home missing)"
quiet "$h" "$(payload "$SANDBOX/sessions/nowhere.jsonl")" "missing transcript" TELEMETRY_EXTRACT="$(stub missing 'print("{}")')"

# No python3 (or no jq) on PATH: a PATH holding only the other commands the hook and settings use.
mkdir -p "$SANDBOX/nopy" "$SANDBOX/nojq"
for c in bash env jq git cat dirname basename readlink mkdir mv rm nohup sed paste tr head grep sleep ls; do
  p="$(command -v "$c")" || continue
  ln -s "$p" "$SANDBOX/nopy/$c"
  [ "$c" = jq ] || ln -s "$p" "$SANDBOX/nojq/$c"
done
h="$(home nopython)"
t="$(transcript sess-nopython)"
quiet "$h" "$(payload "$t")" "no python3" PATH="$SANDBOX/nopy"
h="$(home nojq)"
t="$(transcript sess-nojq)"
quiet "$h" "$(payload "$t")" "no jq" PATH="$SANDBOX/nojq"

# The hook copied without the repo (a remote sandbox): no extractor beside it.
mkdir -p "$SANDBOX/copied"
cp "$HOOK" "$SANDBOX/copied/"
h="$(home copied)"
t="$(transcript sess-copied)"
out="$(env HOME="$h" "$SANDBOX/copied/telemetry-save.sh" <<<"$(payload "$t")" 2>&1)" || fail "missing extractor: exit $?"
[ -z "$out" ] || fail "missing extractor: printed: $out"
quiet "$h" "$(payload "$t")" "TELEMETRY_EXTRACT missing" TELEMETRY_EXTRACT="$SANDBOX/no-such-extract.py"

# An unwritable ~/.agents/telemetry (a file in its place).
h="$(home unwritable)"
t="$(transcript sess-unwritable)"
echo keep >"$h/.agents/telemetry"
quiet "$h" "$(payload "$t")" "unwritable folder" TELEMETRY_EXTRACT="$(stub unwritable 'print("{}")')"

# A last save that does run, so the background work of the cases above has had its turn; then none of them saved.
h="$(home last)"
t="$(transcript sess-last)"
quiet "$h" "$(payload "$t")" "last save" TELEMETRY_EXTRACT="$(stub last 'print("{}")')"
wait_for '[ -s "$h/.agents/telemetry/sess-last.json" ]' || fail "last save: never appeared"
for m in off malformed missing unwritable; do [ ! -e "$SANDBOX/marks/$m" ] || fail "$m: the extractor ran"; done
for d in off malformed missing nopython nojq copied; do
  [ ! -e "$SANDBOX/$d/.agents/telemetry" ] || fail "$d: started a save"
done
[ "$(cat "$SANDBOX/unwritable/.agents/telemetry")" = keep ] || fail "unwritable folder: the file in its place changed"

[ "$fails" -eq 0 ] || { echo "telemetry-save.test.sh: $fails failure(s)"; exit 1; }
echo "telemetry-save.test.sh: ok"
