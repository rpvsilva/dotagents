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

# Runs the hook on JSON $2; $1 is "block" or "allow"; $3 names the case. stderr lands in $SANDBOX/err.
run() {
  local want="$1" json="$2" name="$3" got
  if "$HOOK" <<<"$json" 2>"$SANDBOX/err"; then got=allow; else
    [ $? -eq 2 ] && got=block || got=error
  fi
  [ "$got" = "$want" ] || fail "$name: wanted $want, got $got $(cat "$SANDBOX/err")"
}
# A file tool on path $2, the way Claude Code calls it.
file() { run "$1" "$(jq -n --arg p "$2" '{tool_name: "Read", tool_input: {file_path: $p}}')" "path $2"; }
# A shell command $2, run in $SANDBOX/$3 (default: the sandbox).
cmd() { run "$1" "$(jq -n --arg c "$2" --arg d "$SANDBOX/${3:-}" '{tool_name: "Bash", tool_input: {command: $c}, cwd: $d}')" "command $2"; }

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
git init -q "$SANDBOX/r"
git -C "$SANDBOX/r" -c user.name=t -c user.email=t@t commit -q --allow-empty -m init
token="ghp_$(printf 'a%.0s' {1..36})"
echo "base" >"$SANDBOX/r/app.txt"
git -C "$SANDBOX/r" add app.txt
cmd allow 'git commit -m "clean change"' r
git -C "$SANDBOX/r" -c user.name=t -c user.email=t@t commit -q -m base

echo "token = $token" >>"$SANDBOX/r/app.txt"
cmd allow 'git commit -m "unstaged token is not committed"' r
cmd block 'git commit -am "commits the token"' r
cmd block "cd $SANDBOX/r && git commit -a -m x" ''
grep -q "app.txt" "$SANDBOX/err" || fail "token: reason does not name the file"
if grep -qF "$token" "$SANDBOX/err"; then fail "token: reason echoes the token"; fi
git -C "$SANDBOX/r" add app.txt
cmd block 'git commit -m x' r
git -C "$SANDBOX/r" reset -q --hard

printf -- '-----BEGIN OPENSSH %s KEY-----\n' PRIVATE >"$SANDBOX/r/deploy.txt"
git -C "$SANDBOX/r" add deploy.txt
cmd block 'git commit -m key' r
git -C "$SANDBOX/r" rm -q --cached deploy.txt

# Every token kind blocks a commit. Each token is built here, so this file never holds one.
rep() { printf "%${2}s" '' | tr ' ' "$1"; }
for kind in "github_pat_ $(rep a 22)" "AKIA $(rep A 16)" "xoxb- $(rep 1 10)" "xoxp- $(rep a 12)" \
  "sk-ant- $(rep a 20)" "AIza $(rep a 35)" "gho_ $(rep a 36)"; do
  tok="${kind/ /}"
  echo "key: $tok" >"$SANDBOX/r/conf.txt"
  git -C "$SANDBOX/r" add conf.txt
  cmd block "git commit -m ${kind%% *}" r
  if grep -qF "$tok" "$SANDBOX/err"; then fail "token ${kind%% *}: reason echoes the token"; fi
  git -C "$SANDBOX/r" rm -q --cached conf.txt
done
printf -- '-----BEGIN %s KEY-----\n' PRIVATE >"$SANDBOX/r/conf.txt"
git -C "$SANDBOX/r" add conf.txt
cmd block 'git commit -m bare-pem' r
git -C "$SANDBOX/r" rm -q --cached conf.txt
# Near misses stay allowed.
echo "AKIA$(rep A 15) xox-$(rep a 10) sk-ant-short" >"$SANDBOX/r/conf.txt"
git -C "$SANDBOX/r" add conf.txt
cmd allow 'git commit -m near-miss' r
git -C "$SANDBOX/r" rm -q --cached conf.txt

touch "$SANDBOX/r/prod.env"
git -C "$SANDBOX/r" add -f prod.env
cmd block 'git commit -m x' r
git -C "$SANDBOX/r" rm -q --cached prod.env

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

# The command as an argument works too.
"$HOOK" 'cat .env' 2>/dev/null && fail "argument form did not block"

[ "$fails" -eq 0 ] || { echo "guard-secrets.test.sh: $fails failure(s)"; exit 1; }
echo "guard-secrets.test.sh: ok"
