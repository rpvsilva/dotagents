#!/usr/bin/env bash
# shellcheck disable=SC2016 # the commands under test are literal text, never run
# Runs scripts/hooks/guard-secrets.sh on file paths, commands and throwaway repos, and checks what it blocks.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK="$REPO/scripts/hooks/guard-secrets.sh"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
fails=0

fail() { echo "FAIL: $*"; fails=$((fails + 1)); }

# Runs the hook on JSON $2; $1 is "block" or "allow"; $3 names the case; stderr lands in $err.
judge() {
  local want="$1" json="$2" name="$3" got
  if "$HOOK" <<<"$json" 2>"$err"; then got=allow; else
    [ $? -eq 2 ] && got=block || got=error
  fi
  [ "$got" = "$want" ] || fail "$name: wanted $want, got $got $(cat "$err")"
}
# judge, with stderr in $err. Between `background` and `settle` cases run at once instead (each
# hook call is mostly process start-up), so only cases that share no state go there.
err="$SANDBOX/err" bg="" jobs=0 pids=""
run() { spawn judge "$@"; }
# spawn FUNC ARGS...: runs FUNC now, or as one background case between `background` and `settle`,
# where it runs its own cases one at a time with stderr in its own $err.
spawn() {
  if [ -z "$bg" ]; then "$@"; return; fi
  jobs=$((jobs + 1))
  in_job "$bg/$jobs.err" "$@" >"$bg/$jobs.out" &
  pids="$pids $jobs:$!"
  if [ $((jobs % 16)) -eq 0 ]; then reap; fi
}
# Waits for each background case started since the last reap, keeping a nonzero exit in $bg/<n>.rc.
reap() {
  local job
  for job in $pids; do
    if wait "${job#*:}"; then :; else echo "$?" >"$bg/${job%%:*}.rc"; fi
  done
  pids=""
}
# in_job ERR FUNC ARGS...: FUNC with its stderr in ERR, running its own cases one at a time.
in_job() {
  local err="$1" bg=""
  shift
  "$@"
}
background() { bg="$(mktemp -d "$SANDBOX/bg.XXXXXX")"; }
# Waits for the background cases and counts their failures (a case that exits nonzero counts as one), then runs cases one at a time again.
settle() {
  local i found
  reap
  for ((i = 1; i <= jobs; i++)); do
    found=0
    if [ -s "$bg/$i.out" ]; then
      cat "$bg/$i.out"
      found="$(grep -c '^FAIL: ' "$bg/$i.out" || true)"
      fails=$((fails + found))
    fi
    # A case that died before reporting anything still counts.
    if [ -f "$bg/$i.rc" ] && [ "$found" -eq 0 ]; then
      fail "background case $i exited $(cat "$bg/$i.rc")"
    fi
  done
  bg="" jobs=0
}
# $1 as a JSON string, built in bash: a jq process per case would cost more than the hook itself.
# Escapes backslash, quote, newline, tab and CR, the only control characters the cases hold.
str() {
  local s="${1//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\n'/\\n}"
  s="${s//$'\t'/\\t}"
  s="${s//$'\r'/\\r}"
  printf '"%s"' "$s"
}
# A file tool on path $2, the way Claude Code calls it.
file() { run "$1" "{\"tool_name\": \"Read\", \"tool_input\": {\"file_path\": $(str "$2")}}" "path $2"; }
# A shell command $2, run in $SANDBOX/$3 (default: the sandbox).
cmd() { run "$1" "{\"tool_name\": \"Bash\", \"tool_input\": {\"command\": $(str "$2")}, \"cwd\": $(str "$SANDBOX/${3:-}")}" "command $2"; }
# The builder agrees with jq on every kind of character it escapes.
want="$(jq -n --arg c $'a\\b "q" \t\r\nz' '$c')"
[ "$(jq -c . <<<"$(str $'a\\b "q" \t\r\nz')")" = "$want" ] || fail "str: JSON escaping differs from jq"

# The path rules on file tools; run again below with a CRLF patterns file.
path_cases() {
  file block .env
  file block /repo/.env.local
  file block config/prod.env
  file block "$HOME/.config/dotagents/github-app.pem"
  file block certs/server.key
  file block "$HOME/.ssh/id_ed25519"
  file block secrets/db.txt
  file block "$HOME/.netrc"
  file block "$HOME/.aws/credentials"
  file allow .env.example
  file allow config/.env.template
  file allow "$HOME/.ssh/id_ed25519.pub"
  file allow src/env.ts
  file allow keys.ts
  file allow docs/environment.md
  run block '{"file_path": "/repo/.env", "hook_event_name": "beforeReadFile"}' "Cursor's top-level file_path"
  run block '{"tool_name": "Grep", "tool_input": {"pattern": "x", "path": "secrets/"}}' "Grep on secrets/"
  run block '{"tool_name": "NotebookEdit", "tool_input": {"notebook_path": "secrets/a.ipynb"}}' "NotebookEdit on secrets/"
  run block '{"command": "cat .env", "hook_event_name": "beforeShellExecution"}' "Cursor's top-level command"
  run allow '{"command": "cat .env.example", "hook_event_name": "beforeShellExecution"}' "Cursor's top-level command, an example"
}
background
path_cases

cmd block 'cat .env'
cmd block 'head -5 ~/.config/dotagents/github-app.pem'
cmd block 'grep TOKEN .env.local'
cmd block 'cp secrets/a.txt /tmp/b'
cmd block 'cat *.pem'
cmd block 'source .env && npm test'
cmd block 'cat <.env'
cmd block 'x=$(cat "./.env")'
cmd block $'cat >notes.md <<\'EOF\'\nsome text\nEOF\ncat .env'
cmd block 'jq . <<<"$x"; cat .env'
cmd allow 'cat .env.example'
cmd allow 'scripts/gh-app-token.sh'
cmd allow "jq -r '.mcpServers | to_entries[] | .key' servers.json"
cmd allow 'echo "load the .env file first"'
cmd allow $'cat >notes.md <<\'EOF\'\nkeep keys in .env\nEOF'
cmd allow 'git status'

# A segment that only tests or lists a path passes; a segment that reads it, or redirects to or from it, still blocks.
cmd allow 'test -f .env'
cmd allow '[ -f .env ]'
cmd allow '[[ -f .env ]]'
cmd allow 'ls .env'
cmd allow 'ls *.pem'
cmd allow 'ls -la ~/.config/dotagents/github-app.pem'
cmd allow 'stat .env'
cmd allow 'test -f ~/.config/dotagents/github-app.pem && echo yes'
cmd allow '! test -f .env || echo missing'
cmd allow 'X=1 ls secrets/'
cmd allow 'if [ -f .env ]; then echo yes; fi'
cmd allow 'if false; then :; elif [ -f .env ]; then echo y; fi'
cmd allow 'if false; then :; else ls .env; fi'
cmd allow 'while [ -f .env ]; do sleep 1; done'
cmd allow 'until test -f .env; do :; done'
cmd allow 'for f in a; do ls .env; done'
cmd allow 'time ls .env'
cmd allow '{ ls .env; }'
cmd allow 'ls .env 2>/dev/null'
cmd allow 'test -f .env 2>/dev/null || echo none'
cmd block 'test -f .env && cat .env'
cmd block '[ -f .env ] && source .env'
cmd block 'ls .env; head .env'
cmd block $'ls .env\nhead .env'
cmd block 'if [ -f .env ]; then source .env; fi'
cmd block 'test -f .env > .env'
cmd block 'ls > .env'
cmd block 'ls >.env'
cmd block 'test -f x < .env'
cmd block 'ls .env &> .env'
cmd block 'find . -name .env -exec cat {} \;'
cmd block 'x=$(cat .env); test -n "$x"'
# Redefining a path-only command as a function or alias makes it a reader: nothing counts as path-only.
cmd block 'ls(){ cat $1;}; ls .env'
cmd block 'function test { cat "$@"; }; test .env'
cmd block 'alias ls=cat; ls .env'
cmd block 'stat () { cat "$1"; }; stat .env'

# Commits: a staged secret file or token line blocks; the reason never echoes the token.
# Each case gets its own copy of one base repo (app.txt committed), so they run at once with the
# cases above.
git init -q "$SANDBOX/base"
echo "base" >"$SANDBOX/base/app.txt"
git -C "$SANDBOX/base" add app.txt
git -C "$SANDBOX/base" -c user.name=t -c user.email=t@t commit -q -m base
token="ghp_$(printf 'a%.0s' {1..36})"
# stage NAME FILE TEXT: a copy of the base repo at $SANDBOX/NAME, with TEXT in FILE, staged.
stage() {
  cp -R "$SANDBOX/base" "$SANDBOX/$1"
  printf '%s\n' "$3" >"$SANDBOX/$1/$2"
  git -C "$SANDBOX/$1" add -f "$2"
}
# commit_case WANT NAME FILE TEXT: `git commit` with TEXT staged in FILE; a blocked token is never echoed.
commit_case() {
  stage "$2" "$3" "$4"
  cmd "$1" "git commit -m $2" "$2"
  if [ "$1" = block ] && [ -n "$4" ] && grep -qF -e "$4" "$err"; then fail "$2: reason echoes the secret"; fi
}
# A token in app.txt, changed in the worktree but not staged.
unstaged() {
  cp -R "$SANDBOX/base" "$SANDBOX/unstaged"
  echo "token = $token" >>"$SANDBOX/unstaged/app.txt"
  cmd allow 'git commit -m "unstaged token is not committed"' unstaged
  cmd block 'git commit -am "commits the token"' unstaged
  cmd block 'git commit --all -m x' unstaged
  cmd block "cd $SANDBOX/unstaged && git commit -a -m x" ''
  grep -q "app.txt" "$err" || fail "token: reason does not name the file"
  if grep -qF "$token" "$err"; then fail "token: reason echoes the token"; fi
  # Two cds are not clear enough to follow: judged where it starts, outside any repo.
  cmd allow "cd $SANDBOX/unstaged && cd . && git commit -a -m x" ''
}
spawn commit_case allow clean new.txt "base"
spawn unstaged
spawn commit_case block staged app.txt "token = $token"
spawn commit_case block deploy deploy.txt "$(printf -- '-----BEGIN OPENSSH %s KEY-----' PRIVATE)"

# Every token kind blocks a commit. Each token is built here, so this file never holds one.
rep() { printf "%${2}s" '' | tr ' ' "$1"; }
for kind in "github_pat_ $(rep a 22)" "AKIA $(rep A 16)" "xoxb- $(rep 1 10)" "xoxp- $(rep a 12)" \
  "sk-ant- $(rep a 20)" "AIza $(rep a 35)" "gho_ $(rep a 36)"; do
  spawn commit_case block "${kind%% *}" conf.txt "key: ${kind/ /}"
done
spawn commit_case block bare-pem conf.txt "$(printf -- '-----BEGIN %s KEY-----' PRIVATE)"
# Near misses stay allowed.
spawn commit_case allow near-miss conf.txt "AKIA$(rep A 15) xox-$(rep a 10) sk-ant-short"
spawn commit_case block prod-env prod.env ""
settle

# The rules live only in secret-patterns.txt, beside lib.sh.
for f in lib.sh guard-secrets.sh; do
  if grep -qF -e '\.netrc' -e 'KEY-----' -e 'AKIA[' -e '(example|sample' "$REPO/scripts/hooks/$f"; then
    fail "$f holds a secret rule"
  fi
done

# Without the patterns file (or with a rule missing), the hooks stop loudly, never match every path.
cp -R "$REPO/scripts/hooks" "$SANDBOX/hooks"
rm "$SANDBOX/hooks/secret-patterns.txt"
code=0
"$SANDBOX/hooks/guard-secrets.sh" 'cat src/app.ts' 2>"$SANDBOX/err" || code=$?
[ "$code" -eq 2 ] || fail "no patterns file: wanted exit 2, got $code"
grep -q 'secret-patterns.txt' "$SANDBOX/err" || fail "no patterns file: message does not name it"
check_closed() {
  local out code=0
  out="$(bash -c '. "$1"; is_secret src/app.ts && echo matches' _ "$SANDBOX/hooks/lib.sh" 2>/dev/null)" || code=$?
  [ "$code" -eq 2 ] || fail "$1: sourcing lib.sh wanted exit 2, got $code"
  [ -z "$out" ] || fail "$1: is_secret matched every path"
}
check_closed "no patterns file"
grep -v '^path ' "$REPO/scripts/hooks/secret-patterns.txt" >"$SANDBOX/hooks/secret-patterns.txt"
check_closed "no path rule"
grep -v '^token ' "$REPO/scripts/hooks/secret-patterns.txt" >"$SANDBOX/hooks/secret-patterns.txt"
check_closed "no token rule"
grep -v '^example ' "$REPO/scripts/hooks/secret-patterns.txt" >"$SANDBOX/hooks/secret-patterns.txt"
check_closed "no example rule"
# crlf FILE: FILE's lines with CR LF endings on stdout, whatever FILE ends with. awk, not
# sed 's/$/\r/', which not every sed supports.
crlf() { awk '{ sub(/\r$/, ""); printf "%s\r\n", $0 }' "$1"; }
# has_crlf FILE: every line of FILE ends in CR LF. Counts CR bytes with tr, since grep on
# Git for Windows ignores a CR before LF and would never see one.
has_crlf() {
  local lines crs
  lines=$(($(wc -l <"$1")))
  crs=$(($(tr -dc '\r' <"$1" | wc -c)))
  [ "$lines" -gt 0 ] && [ "$crs" -eq "$lines" ]
}
grep -v '^path ' "$REPO/scripts/hooks/secret-patterns.txt" >"$SANDBOX/lf.txt"
crlf "$SANDBOX/lf.txt" >"$SANDBOX/hooks/secret-patterns.txt"
has_crlf "$SANDBOX/hooks/secret-patterns.txt" || fail "no path rule, CRLF endings: the copy has no CR endings"
check_closed "no path rule, CRLF endings"

# A patterns file with CRLF endings (a Windows checkout, a copied install) gives the same rules.
cp -R "$REPO/scripts/hooks" "$SANDBOX/crlf"
crlf "$REPO/scripts/hooks/secret-patterns.txt" >"$SANDBOX/crlf/secret-patterns.txt"
has_crlf "$SANDBOX/crlf/secret-patterns.txt" || fail "crlf: the copy has no CR endings"
HOOK="$SANDBOX/crlf/guard-secrets.sh"
background
path_cases
cmd allow 'cat .env.example'
spawn commit_case block crlf-staged app.txt "token = $token"
settle
HOOK="$REPO/scripts/hooks/guard-secrets.sh"

# The command as an argument works too.
"$HOOK" 'cat .env' 2>/dev/null && fail "argument form did not block"
# No input at all (no JSON on stdin) has nothing to judge, so it runs.
printf '' | "$HOOK" 2>/dev/null || fail "empty stdin did not allow (exit $?)"

[ "$fails" -eq 0 ] || { echo "guard-secrets.test.sh: $fails failure(s)"; exit 1; }
echo "guard-secrets.test.sh: ok"
