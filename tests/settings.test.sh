#!/usr/bin/env bash
# Runs scripts/settings.sh in a throwaway XDG_CONFIG_HOME and git repo: the Level order (flag > repo > machine >
# default), a skill's own key over a shared one, validation on set, machine-only keys, and loud failure on bad
# hand-edits. Uses the repo's real Declarations.
# shellcheck disable=SC2015 # `a && b || fail`: fail only reports, so it may run when either check fails
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
fails=0

fail() { echo "FAIL: $*"; fails=$((fails + 1)); }

MACHINE="$SANDBOX/config/dotagents/settings.json"
R="$SANDBOX/repo"
git init -q "$R"
mkdir -p "$R/sub"

# Runs settings in the repo's sub folder with a clean environment plus any VAR=value given before --;
# stdout in $SANDBOX/out, stderr in $SANDBOX/err.
s() {
  local vars=()
  while [ $# -gt 0 ] && [ "$1" != -- ]; do vars+=("$1"); shift; done
  shift
  (cd "$R/sub" && env -i HOME="$SANDBOX" PATH="$PATH" XDG_CONFIG_HOME="$SANDBOX/config" ${vars[@]+"${vars[@]}"} \
    "$REPO/scripts/settings.sh" "$@") >"$SANDBOX/out" 2>"$SANDBOX/err"
}
out() { cat "$SANDBOX/out"; }
err() { cat "$SANDBOX/err"; }
# Expects `settings get $1 $2` to print $3.
expect() {
  s -- get "$1" "$2" || { fail "get $1 $2: exited non-zero: $(err)"; return; }
  [ "$(out)" = "$3" ] || fail "get $1 $2: printed '$(out)', not '$3'"
}

# --- get: the Declaration's default, for a shared key read through a skill.
expect build-epic max_parallel 4

# --- Level order: machine over default, repo over machine, a skill's own entry over the shared one at its Level.
mkdir -p "$(dirname "$MACHINE")" "$R/.agents"
echo '{"max_parallel": 3}' >"$MACHINE"
expect build-epic max_parallel 3
expect security-audit max_parallel 3
echo '{"max_parallel": 2}' >"$R/.agents/settings.json"
expect build-epic max_parallel 2
echo '{"max_parallel": 3, "build-epic": {"max_parallel": 6}}' >"$MACHINE"
expect build-epic max_parallel 2
rm "$R/.agents/settings.json"
expect build-epic max_parallel 6
expect security-audit max_parallel 3
expect shared max_parallel 3
# --machine and --repo read one Level only; nothing printed when it holds no value.
s -- get build-epic max_parallel --repo || fail "get --repo: exited non-zero: $(err)"
[ -z "$(out)" ] || fail "get --repo: printed '$(out)' for a Level that holds nothing"
s -- get shared max_parallel --machine; [ "$(out)" = 3 ] || fail "get --machine: printed '$(out)'"
rm "$MACHINE"

# --- set writes the chosen Level; get reads it back; unset removes it and drops an emptied section.
s -- set build-epic max_parallel 5 --repo || fail "set --repo: exited non-zero: $(err)"
jq -e '."build-epic".max_parallel == 5' "$R/.agents/settings.json" >/dev/null || fail "set --repo: file holds $(cat "$R/.agents/settings.json")"
expect build-epic max_parallel 5
s -- set shared max_parallel 7 --machine || fail "set shared: exited non-zero: $(err)"
jq -e '.max_parallel == 7' "$MACHINE" >/dev/null || fail "set shared: file holds $(cat "$MACHINE")"
s -- set remote backend docker --machine || fail "set enum: exited non-zero: $(err)"
s -- set remote keep yes --machine || fail "set bool: exited non-zero: $(err)"
jq -e '.remote == {"backend": "docker", "keep": true} and .max_parallel == 7' "$MACHINE" >/dev/null || fail "set: machine file holds $(cat "$MACHINE")"
expect remote keep true
s -- unset build-epic max_parallel --repo || fail "unset: exited non-zero: $(err)"
jq -e '. == {}' "$R/.agents/settings.json" >/dev/null || fail "unset: repo file holds $(cat "$R/.agents/settings.json")"
expect build-epic max_parallel 7
find "$(dirname "$MACHINE")" "$R/.agents" -name ".settings.json.*" | grep -q . && fail "set: temp file left behind"

# --- set refuses bad values, unknown keys and a Level the key does not allow, and leaves the file as it was.
cp "$MACHINE" "$SANDBOX/before"
refuse() {
  local why="$1"; shift
  local code=0; s -- set "$@" || code=$?
  [ "$code" != 0 ] || fail "set $*: accepted ($why)"
  [ -s "$SANDBOX/err" ] || fail "set $*: no message ($why)"
  cmp -s "$SANDBOX/before" "$MACHINE" || fail "set $*: the machine file changed ($why)"
}
refuse "below min" build-epic max_parallel 0 --machine
refuse "not a number" build-epic max_parallel lots --machine
refuse "not a choice" remote backend k8s --machine
refuse "not a bool" remote keep maybe --machine
refuse "machine-only key" remote backend docker --repo
refuse "unknown key" build-epic max_paralel 3 --machine
refuse "unknown section" nope max_parallel 3 --machine
refuse "no Level" build-epic max_parallel 3
refuse "a shared key the section does not read" remote tracker jira --machine
refuse "a shared key the skill does not read" fix-pr max_parallel 2 --machine
refuse "a repo-only key" remote allowed false --machine
s -- set remote backend k8s --machine || true
grep -q "one of: cluster, docker, ssh" "$SANDBOX/err" || fail "set enum: message does not list the choices"
code=0; s -- get build-epic max_paralel || code=$?
[ "$code" = 2 ] || fail "get unknown key: exit $code, not 2"

# --- A list from a comma-separated string, read back one item per line.
s -- set to-epic labels 'needs triage, epic' --repo || fail "set list: exited non-zero: $(err)"
jq -e '."to-epic".labels == ["needs triage", "epic"]' "$R/.agents/settings.json" >/dev/null || fail "set list: file holds $(cat "$R/.agents/settings.json")"
expect to-epic labels $'needs triage\nepic'
rm "$R/.agents/settings.json" "$MACHINE"

# --- A bad hand-edit fails loudly, naming the file and key, instead of falling back to the default.
loud() {
  local why="$1" file="$2" key="$3"; shift 3
  local code=0; s -- get "$@" || code=$?
  [ "$code" = 1 ] || fail "get $* with $why: exit $code, not 1"
  # From the sandbox's own folder on: Windows prints the path as C:/.../Temp/tmp.XXXX/..., not /tmp/tmp.XXXX/...
  file="${SANDBOX##*/}/${file#"$SANDBOX"/}"
  grep -qF "$file" "$SANDBOX/err" && grep -qF "$key" "$SANDBOX/err" || fail "get $* with $why: message does not name $file and $key: $(err)"
}
echo '{"max_parallel": "four"}' >"$MACHINE"
loud "a string for an int" "$MACHINE" max_parallel build-epic max_parallel
echo '{"remote": {"backend": "k8s"}}' >"$MACHINE"
loud "an unknown choice" "$MACHINE" remote.backend remote backend
echo '{"max_parallel": ' >"$MACHINE"
loud "invalid JSON" "$MACHINE" settings.json build-epic max_parallel
rm "$MACHINE"
# A repo cannot set a machine-only key: a cloned repo must not redirect where snapshots go.
echo '{"remote": {"ssh_host": "evil@box"}}' >"$R/.agents/settings.json"
loud "a machine-only key in the repo file" "$R/.agents/settings.json" remote.ssh_host remote ssh_host
# ...but it can forbid remote runs.
echo '{"remote": {"allowed": false}}' >"$R/.agents/settings.json"
expect remote allowed false

# --- The env variable is the flag Level: it beats both files, and a bad one fails loudly.
echo '{"remote": {"hours": 4, "backend": "ssh", "ssh_host": "me@box"}}' >"$MACHINE"
s REMOTE_HOURS=1 -- get remote hours; [ "$(out)" = 1 ] || fail "env Level: printed '$(out)', not 1"
code=0; s REMOTE_HOURS=soon -- get remote hours || code=$?
[ "$code" = 1 ] && grep -q REMOTE_HOURS "$SANDBOX/err" || fail "bad env value: exit $code, $(err)"
s REMOTE_HOURS=1 -- get remote hours --machine; [ "$(out)" = 4 ] || fail "get --machine: the env Level counted"

# --- env: export lines for scripts, with defaults, the env Level, true bools as 1 and false ones unset (the
# backends treat any non-empty value as true, so REMOTE_KEEP=0 must not survive).
s REMOTE_KEEP=1 -- env remote || fail "env: exited non-zero: $(err)"
for line in "export REMOTE_BACKEND='ssh'" "export REMOTE_SSH_HOST='me@box'" "export REMOTE_HOURS='4'" \
  "export REMOTE_KEEP='1'" "export REMOTE_MAX_MB='200'" "export REMOTE_SSH_MODE='container'"; do
  grep -qxF "$line" "$SANDBOX/out" || fail "env: no line $line in: $(out)"
done
grep -q 'REMOTE_PLATFORM' "$SANDBOX/out" && fail "env: an unset key was exported"
s -- env remote; grep -qxF "unset REMOTE_KEEP" "$SANDBOX/out" || fail "env: a false bool not unset: $(out)"
s REMOTE_KEEP=0 -- env remote
( REMOTE_KEEP=0; eval "$(out)"; [ -z "${REMOTE_KEEP+set}" ] ) || fail "env: REMOTE_KEEP=0 survived eval: $(out)"
echo '{"remote": {"ssh_host": "a b'"'"'c"}}' >"$MACHINE"
s -- env remote; ( eval "$(out)"; [ "$REMOTE_SSH_HOST" = "a b'c" ] ) || fail "env: a value with a quote did not survive eval: $(out)"

# --- --dir reads the repo Level of another folder; outside a git repo there is none.
mkdir -p "$SANDBOX/plain"
s -- --dir "$SANDBOX/plain" get remote allowed; [ "$(out)" = true ] || fail "--dir outside a repo: printed '$(out)'"
(cd "$SANDBOX/plain" && env -i HOME="$SANDBOX" PATH="$PATH" XDG_CONFIG_HOME="$SANDBOX/config" \
  "$REPO/scripts/settings.sh" --dir "$R" get remote allowed) >"$SANDBOX/out" 2>&1
[ "$(out)" = false ] || fail "--dir into the repo: printed '$(out)', not the repo's false"
code=0; (cd "$SANDBOX/plain" && env -i HOME="$SANDBOX" PATH="$PATH" XDG_CONFIG_HOME="$SANDBOX/config" \
  "$REPO/scripts/settings.sh" set remote allowed false --repo) 2>/dev/null || code=$?
[ "$code" != 0 ] || fail "set --repo outside a repo: accepted"

# --- list: every declared key a section reads, with its value and Level.
echo '{"max_parallel": 3}' >"$MACHINE"
s -- list build-epic || fail "list: exited non-zero: $(err)"
grep -qF $'build-epic.max_parallel\t3\tmachine\tSubagents running at once' "$SANDBOX/out" || fail "list: max_parallel line: $(out)"
grep -qF $'build-epic.in_progress\t\tdefault\t' "$SANDBOX/out" || fail "list: in_progress line: $(out)"
grep -q $'^remote.backend\t.*one of: cluster, docker, ssh' <(s -- list remote; out) || fail "list: no accepted values in the meaning column"
s -- list; grep -q '^remote.backend' "$SANDBOX/out" && grep -q '^shared.max_parallel' "$SANDBOX/out" || fail "list with no section: $(out)"
s -- list remote; grep -q max_parallel "$SANDBOX/out" && fail "list remote: shows a shared key remote does not read: $(out)"
echo '{"max_parallel": 0}' >"$MACHINE"
code=0; s -- list build-epic || code=$?
[ "$code" = 1 ] && grep -q $'build-epic.max_parallel\tERROR: ' "$SANDBOX/out" || fail "list with a bad value: exit $code, $(out)"

# --- check: unknown keys and bad values in either file.
echo '{"max_parallel": 3, "build-epic": {"in_progres": "x"}, "colour": "red"}' >"$MACHINE"
code=0; s -- check || code=$?
[ "$code" = 1 ] || fail "check: exit $code with unknown keys"
grep -q 'build-epic.in_progres' "$SANDBOX/err" && grep -q 'colour' "$SANDBOX/err" || fail "check: unknown keys not named: $(err)"
echo '{"max_parallel": 3}' >"$MACHINE"
s -- check && [ "$(out)" = ok ] || fail "check: a valid setup: $(out) $(err)"
rm -f "$MACHINE" "$R/.agents/settings.json"

[ "$fails" -eq 0 ] || { echo "settings.test.sh: $fails failure(s)"; exit 1; }
echo "settings.test.sh: ok"
