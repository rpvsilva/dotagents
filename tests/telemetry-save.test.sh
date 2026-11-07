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

# A throwaway home named $1, linked as install.sh links it: one link per file in ~/.agents/hooks, and
# ~/.agents/bin/settings. With $2 = folder, ~/.agents/hooks is one link to the repo folder, as older installs made it.
home() {
  local h="$SANDBOX/$1" f
  mkdir -p "$h/.agents/bin"
  if [ "${2:-}" = folder ]; then
    ln -s "$REPO/scripts/hooks" "$h/.agents/hooks"
  else
    mkdir -p "$h/.agents/hooks"
    for f in "$REPO"/scripts/hooks/*; do ln -s "$f" "$h/.agents/hooks/${f##*/}"; done
  fi
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
# A SessionEnd payload for transcript $1 (its session_id the transcript's name), cwd $2.
payload() {
  jq -n --arg t "$1" --arg s "$(basename "$1" .jsonl)" --arg c "${2:-$SANDBOX/proj}" \
    '{session_id: $s, transcript_path: $t, cwd: $c, hook_event_name: "SessionEnd", reason: "other"}'
}

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
# It finds marks/ from its own path (argv[0]), never from $SANDBOX written into its source: on Windows, Git Bash turns
# a /c/... argument into C:/... for a native python3, but a path inside the source stays /c/..., which Python cannot open.
stub() {
  mkdir -p "$SANDBOX/marks"
  printf 'import json, os, sys, time\nopen(os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "marks", "%s"), "w").close()\n%s\n' \
    "$1" "$2" >"$SANDBOX/$1.py"
  echo "$SANDBOX/$1.py"
}

# Waits up to 10 s for the test $1 to hold.
wait_for() {
  local i=0
  while ! eval "$1"; do
    i=$((i + 1))
    [ "$i" -lt 500 ] || return 1
    sleep 0.02
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

# The real extractor is found from the hook's own place in the repo whether the hook is run through a per-file link
# (above), through an old folder link, or from the repo itself.
h="$(home folder folder)"
t="$(transcript sess-folder)"
quiet "$h" "$(payload "$t")" "folder link"
wait_for '[ -s "$h/.agents/telemetry/sess-folder.json" ]' || fail "folder link: the hook found no extractor"
h="$(home direct)"
t="$(transcript sess-direct)"
out="$(HOME="$h" "$HOOK" <<<"$(payload "$t")" 2>&1)" || fail "from the repo: exit $?"
[ -z "$out" ] || fail "from the repo: printed: $out"
wait_for '[ -s "$h/.agents/telemetry/sess-direct.json" ]' || fail "from the repo: the hook found no extractor"

# The hook returns while a slow extractor still runs, and its output appears afterwards. The extractor gets the
# transcript, the session's folder and --whole. It blocks until the test writes its .go file (up to 10 s), which the
# test does only once the hook has returned: a hook that waited for it would return late, with .done already there.
h="$(home slow)"
t="$(transcript sess-slow)"
x="$(stub slow 'import os
for _ in range(200):
    if os.path.exists(sys.argv[0] + ".go"): break
    time.sleep(0.05)
open(sys.argv[0] + ".done", "w").close(); print(json.dumps({"argv": sys.argv[1:]}))')"
quiet "$h" "$(payload "$t")" "slow extractor" TELEMETRY_EXTRACT="$x"
[ ! -e "$x.done" ] || fail "slow extractor: the hook waited for the extractor to finish"
touch "$x.go"
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

# Two ends of one session at once each write their own temp file, so neither's output mixes into the other's.
# Both stubs block until the test writes $SANDBOX/both.go; while they wait, each must hold its own temp file.
h="$(home concurrent)"
t="$(transcript sess-concurrent)"
for n in 1 2; do
  x="$(stub "concurrent$n" "for _ in range(200):
    if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), 'both.go')): break
    time.sleep(0.05)
print(json.dumps({'n': $n}))")"
  quiet "$h" "$(payload "$t")" "concurrent end $n" TELEMETRY_EXTRACT="$x"
done
wait_for '[ -e "$SANDBOX/marks/concurrent1" ] && [ -e "$SANDBOX/marks/concurrent2" ]' || fail "concurrent ends: an extractor never started"
wait_for '[ "$(ls -A "$h/.agents/telemetry" | wc -l)" -eq 2 ]' \
  || fail "concurrent ends: the two ends share a temp file: $(ls -A "$h/.agents/telemetry")"
touch "$SANDBOX/both.go"
wait_for '[ "$(ls -A "$h/.agents/telemetry")" = sess-concurrent.json ]' \
  || fail "concurrent ends: left $(ls -A "$h/.agents/telemetry")"
jq -e '.n == 1 or .n == 2' "$h/.agents/telemetry/sess-concurrent.json" >/dev/null 2>&1 \
  || fail "concurrent ends: the file holds no single run: $(cat "$h/.agents/telemetry/sess-concurrent.json" 2>/dev/null)"

# The file is named after the payload's session_id, not the transcript's name: a native Windows transcript_path
# (C:\...\<id>.jsonl) does not split on / . On Windows that is the transcript's own native path; elsewhere a file
# whose name holds the backslashes.
h="$(home winpath)"
t="$(transcript sess-winpath)"
case "$(uname -s)" in
  MINGW* | MSYS* | CYGWIN*) w="$(cygpath -w "$t")" ;;
  *) w="$SANDBOX/sessions/C:\\Users\\me\\sess-winpath.jsonl"; cp "$t" "$w" ;;
esac
json="$(jq -n --arg t "$w" --arg c "$SANDBOX/proj" '{session_id: "sess-win.id_1", transcript_path: $t, cwd: $c}')"
quiet "$h" "$json" "Windows transcript path" TELEMETRY_EXTRACT="$(stub winpath 'print("{}")')"
wait_for '[ "$(ls -A "$h/.agents/telemetry" 2>/dev/null)" = sess-win.id_1.json ]' \
  || fail "Windows transcript path: saved $(ls -A "$h/.agents/telemetry" 2>/dev/null)"

# An unsafe session_id (a path, a dot-dot, a character outside [A-Za-z0-9._-]) is ignored: the transcript's name is
# used, and nothing is written outside ~/.agents/telemetry.
for c in 'up:../../escaped' 'slash:a/b' 'back:a\b' 'dots:..' 'dotdot:a..b' 'space:a b' 'empty:' 'number:7'; do
  name="${c%%:*}"
  h="$(home "unsafe-$name")"
  t="$(transcript "sess-unsafe-$name")"
  if [ "$name" = number ]; then
    json="$(jq -n --arg t "$t" --arg c "$SANDBOX/proj" '{session_id: 7, transcript_path: $t, cwd: $c}')"
  else
    json="$(jq -n --arg s "${c#*:}" --arg t "$t" --arg c "$SANDBOX/proj" '{session_id: $s, transcript_path: $t, cwd: $c}')"
  fi
  quiet "$h" "$json" "unsafe session_id $name" TELEMETRY_EXTRACT="$(stub "unsafe-$name" 'print("{}")')"
  wait_for '[ "$(ls -A "$h/.agents/telemetry" 2>/dev/null)" = "sess-unsafe-$name.json" ]' \
    || fail "unsafe session_id $name: saved $(ls -A "$h/.agents/telemetry" 2>/dev/null)"
  [ "$(ls -A "$h/.agents")" = "$(printf 'bin\nhooks\ntelemetry')" ] || fail "unsafe session_id $name: wrote $(ls -A "$h/.agents")"
  [ "$(ls -A "$h")" = .agents ] || fail "unsafe session_id $name: wrote $(ls -A "$h")"
done
[ ! -e "$SANDBOX/escaped.json" ] || fail "unsafe session_id: wrote $SANDBOX/escaped.json"

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

# A payload without cwd: the session's directory is the hook's working directory, passed on as --cwd.
h="$(home nocwd)"
t="$(transcript sess-nocwd)"
x="$(stub nocwd 'print(json.dumps({"argv": sys.argv[1:]}))')"
cd "$SANDBOX/proj"
quiet "$h" "$(jq -n --arg t "$t" '{transcript_path: $t}')" "payload without cwd" TELEMETRY_EXTRACT="$x"
cd - >/dev/null
wait_for '[ -s "$h/.agents/telemetry/sess-nocwd.json" ]' || fail "payload without cwd: no save"
jq -e '.argv[3] == "--cwd" and (.argv[4] | endswith("proj"))' "$h/.agents/telemetry/sess-nocwd.json" >/dev/null 2>&1 \
  || fail "payload without cwd: wrong --cwd: $(cat "$h/.agents/telemetry/sess-nocwd.json" 2>/dev/null)"

# No settings command (~/.agents/bin/settings missing) keeps the default (on).
h="$(home nosettings)"
rm "$h/.agents/bin/settings"
t="$(transcript sess-nosettings)"
quiet "$h" "$(payload "$t")" "no settings command" TELEMETRY_EXTRACT="$(stub nosettings 'print("{}")')"
wait_for '[ -s "$h/.agents/telemetry/sess-nosettings.json" ]' || fail "no settings command: no save on the default"

# Cases that must save nothing, each with a stub extractor that would leave a mark if it ran.
h="$(home off)"
t="$(transcript sess-off)"
env HOME="$h" "$h/.agents/bin/settings" set telemetry auto_save false --machine >/dev/null
quiet "$h" "$(payload "$t")" "Setting off" TELEMETRY_EXTRACT="$(stub off 'print("{}")')"

# The Setting off in the repo the session ran in (the payload's cwd, not the hook's working directory).
h="$(home repooff)"
t="$(transcript sess-repooff)"
mkdir -p "$SANDBOX/offrepo/sub"
git -C "$SANDBOX/offrepo" init -q
env HOME="$h" "$h/.agents/bin/settings" --dir "$SANDBOX/offrepo" set telemetry auto_save false --repo >/dev/null
quiet "$h" "$(payload "$t" "$SANDBOX/offrepo/sub")" "repo Setting off" TELEMETRY_EXTRACT="$(stub repooff 'print("{}")')"

# Neither the session_id nor the transcript's name is a safe file name.
h="$(home neither)"
t="$(transcript "sess neither")"
json="$(jq -n --arg t "$t" --arg c "$SANDBOX/proj" '{session_id: "a/b", transcript_path: $t, cwd: $c}')"
quiet "$h" "$json" "no safe name" TELEMETRY_EXTRACT="$(stub neither 'print("{}")')"

h="$(home malformed)"
quiet "$h" 'not json {' "malformed payload" TELEMETRY_EXTRACT="$(stub malformed 'print("{}")')"
quiet "$h" '' "empty payload" TELEMETRY_EXTRACT="$(stub malformed 'print("{}")')"
quiet "$h" '{"cwd": 3}' "payload without transcript_path" TELEMETRY_EXTRACT="$(stub malformed 'print("{}")')"

h="$(home missing)"
quiet "$h" "$(payload "$SANDBOX/sessions/nowhere.jsonl")" "missing transcript" TELEMETRY_EXTRACT="$(stub missing 'print("{}")')"

# No python3 (or no jq) on PATH: a PATH holding only the other commands the hook and settings use.
# Each is a /bin/sh script running the real one, not a link: on Windows a link to /usr/bin/bash points at a file
# without its .exe, so env fails to run the hook's `#!/usr/bin/env bash` through it (exit 127).
mkdir -p "$SANDBOX/nopy" "$SANDBOX/nojq"
for c in bash env jq git cat dirname basename readlink mkdir mktemp mv rm nohup sed paste tr head grep sleep ls; do
  p="$(command -v "$c")" || continue
  printf '#!/bin/sh\nexec "%s" "$@"\n' "$p" >"$SANDBOX/nopy/$c"
  [ "$c" = jq ] || cp "$SANDBOX/nopy/$c" "$SANDBOX/nojq/$c"
done
chmod +x "$SANDBOX/nopy"/* "$SANDBOX/nojq"/*
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
for m in off repooff neither malformed missing unwritable; do [ ! -e "$SANDBOX/marks/$m" ] || fail "$m: the extractor ran"; done
for d in off repooff neither malformed missing nopython nojq copied; do
  [ ! -e "$SANDBOX/$d/.agents/telemetry" ] || fail "$d: started a save"
done
[ "$(cat "$SANDBOX/unwritable/.agents/telemetry")" = keep ] || fail "unwritable folder: the file in its place changed"

[ "$fails" -eq 0 ] || { echo "telemetry-save.test.sh: $fails failure(s)"; exit 1; }
echo "telemetry-save.test.sh: ok"
