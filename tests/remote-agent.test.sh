#!/usr/bin/env bash
# Runs scripts/remote-agent-pack.sh on a throwaway $HOME, and scripts/remote-agent.sh against a stub kubectl
# that plays the pod with a local folder: what reaches the sandbox, how the token travels, and that the
# patch comes back and the claim goes away.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
fails=0

fail() { echo "FAIL: $*"; fails=$((fails + 1)); }

# A $HOME with a setup worth filtering.
H="$SANDBOX/home"
mkdir -p "$H/.claude/skills" "$H/.claude/projects/p" "$H/.agents/hooks" "$H/src/skill-a"
printf -- '---\nname: skill-a\n---\n' >"$H/src/skill-a/SKILL.md"
printf 'KEY=1\n' >"$H/src/skill-a/.env"
ln -s "$H/src/skill-a" "$H/.claude/skills/skill-a"
printf 'global instructions\n' >"$H/src/AGENTS.md"
ln -s "$H/src/AGENTS.md" "$H/.claude/CLAUDE.md"
printf '#!/bin/sh\n' >"$H/.agents/hooks/guard.sh"
printf '{"token":"x"}' >"$H/.claude/.credentials.json"
printf 'history\n' >"$H/.claude/history.jsonl"
printf 'transcript\n' >"$H/.claude/projects/p/s.jsonl"
cat >"$H/.claude/settings.json" <<'EOF'
{
  "env": {"SOME_TOKEN": "secret"},
  "statusLine": {"type": "command", "command": "/Users/me/status.sh"},
  "model": "opus",
  "permissions": {"allow": ["Bash(ls:*)"]},
  "enabledPlugins": {"p@official": true},
  "extraKnownMarketplaces": {
    "official": {"source": {"source": "github", "repo": "o/plugins"}},
    "mine": {"source": {"source": "directory", "path": "/Users/me/mp"}}
  },
  "hooks": {
    "PreToolUse": [{"matcher": "Bash", "hooks": [
      {"type": "command", "command": "\"$HOME/.agents/hooks/guard.sh\""},
      {"type": "command", "command": "/Users/me/.config/iterm2/status"}]}],
    "Stop": [{"hooks": [{"type": "command", "command": "/Users/me/notify"}]}]
  }
}
EOF
cat >"$H/.claude.json" <<'EOF'
{"oauthAccount": {"email": "me@example.com"},
 "mcpServers": {"open": {"type": "http", "url": "https://mcp.example.com"},
                "keyed": {"type": "stdio", "command": "x", "env": {"API_KEY": "secret"}}}}
EOF

# --- remote-agent-pack.sh
P="$SANDBOX/packed"
HOME="$H" "$REPO/scripts/remote-agent-pack.sh" "$P" 2>"$SANDBOX/pack.err" || fail "pack: exited non-zero: $(cat "$SANDBOX/pack.err")"
if [ ! -f "$P/.claude/CLAUDE.md" ] || [ -L "$P/.claude/CLAUDE.md" ]; then fail "pack: CLAUDE.md not copied as a file"; fi
[ -f "$P/.claude/skills/skill-a/SKILL.md" ] || fail "pack: linked skill not copied"
[ -f "$P/.agents/hooks/guard.sh" ] || fail "pack: hook scripts not copied"
for f in .claude/.credentials.json .claude/history.jsonl .claude/projects .claude/skills/skill-a/.env .claude.json; do
  [ ! -e "$P/$f" ] || fail "pack: $f was copied"
done
s="$P/.claude/settings.json"
jq -e '.env == null and .statusLine == null' "$s" >/dev/null || fail "pack: env or statusLine kept"
jq -e '.model == "opus" and .permissions.allow == ["Bash(ls:*)"] and .enabledPlugins == {"p@official": true}' "$s" >/dev/null || fail "pack: model, permissions or plugins lost"
jq -e '.extraKnownMarketplaces | keys == ["official"]' "$s" >/dev/null || fail "pack: non-GitHub marketplace kept"
jq -e '.hooks | keys == ["PreToolUse"] and (.PreToolUse[0].hooks | length == 1) and (.PreToolUse[0].hooks[0].command | test("agents/hooks"))' "$s" >/dev/null \
  || fail "pack: hooks not cut down to ~/.agents/hooks ones: $(jq -c .hooks "$s")"
jq -e 'keys == ["open"]' "$P/.claude/remote-mcp.json" >/dev/null || fail "pack: MCP server with env kept, or open one lost"
grep -q "SOME_TOKEN\|secret" -r "$P" && fail "pack: a secret value was copied"
grep -q "env.SOME_TOKEN" "$SANDBOX/pack.err" || fail "pack: dropped env not reported"
grep -q "keyed" "$SANDBOX/pack.err" || fail "pack: dropped MCP server not reported"

# --- remote-agent.sh against a stub kubectl. The "pod" is $POD (its $HOME). Exec runs the given sh -c
# command locally with HOME=$POD, except the sandbox script, which is replaced by a fake run that writes
# $POD/out from $SANDBOX/patch (empty file = no changes).
POD="$SANDBOX/pod"
mkdir -p "$SANDBOX/bin" "$SANDBOX/config/dotagents"
cat >"$SANDBOX/bin/kubectl" <<EOF
#!/usr/bin/env bash
args="\$*"
printf '%s\n' "\${args//\$'\\n'/ }" >>"$SANDBOX/calls"
case " \$* " in
  *" apply "*) cat >"$SANDBOX/claim.yaml" ;;
  *" get sandboxclaim "*) printf 'claude-runner-abc' ;;
  *" get sandbox "*) printf '' ;;
  *" remote-agent-sandbox"*)
    cat >"$SANDBOX/stdin"
    mkdir -p "$POD/out"; cp "$SANDBOX/patch" "$POD/out/changes.patch"; echo "did the thing" >"$POD/out/summary.md"
    exit "\$(cat "$SANDBOX/code")" ;;
  *" exec "*)
    while [ "\$1" != "-c" ]; do shift; done
    mkdir -p "$POD"; HOME="$POD" sh -c "\${2//--warning=no-unknown-keyword/}" ;;
esac
EOF
chmod +x "$SANDBOX/bin/kubectl"
echo "CLAUDE_CODE_OAUTH_TOKEN=sk-claude-secret" >"$SANDBOX/config/dotagents/remote.env"

# A worktree with a tracked file, an untracked one, an ignored one and a secret-looking one.
W="$SANDBOX/work"
git init -q "$W"
git -C "$W" config user.email t@example.com
git -C "$W" config user.name t
printf 'ignored.txt\n' >"$W/.gitignore"
printf 'one\n' >"$W/tracked.txt"
git -C "$W" add -A
git -C "$W" commit -qm base
printf 'local\n' >"$W/untracked.txt"
printf 'ignored\n' >"$W/ignored.txt"
printf 'KEY=1\n' >"$W/.env"

# Runs remote-agent.sh in $W with the fake run exiting $1 and returning patch $2.
run() {
  echo "$1" >"$SANDBOX/code"
  printf '%s' "$2" >"$SANDBOX/patch"
  rm -rf "$POD" "$SANDBOX/calls" "$SANDBOX/stdin"
  (cd "$W" && env -u CLAUDE_CODE_OAUTH_TOKEN HOME="$H" PATH="$SANDBOX/bin:$PATH" XDG_CONFIG_HOME="$SANDBOX/config" \
    "$REPO/scripts/remote-agent.sh" "add a hello file") >"$SANDBOX/out" 2>&1
}
patch='diff --git a/hello.txt b/hello.txt
new file mode 100644
index 0000000..ce01362
--- /dev/null
+++ b/hello.txt
@@ -0,0 +1 @@
+hello
'

run 0 "$patch" || fail "success: exited non-zero: $(cat "$SANDBOX/out")"
if [ ! -f "$POD/work/tracked.txt" ] || [ ! -f "$POD/work/untracked.txt" ]; then fail "tracked or untracked file not sent"; fi
[ ! -e "$POD/work/ignored.txt" ] || fail "ignored file sent"
[ ! -e "$POD/work/.env" ] || fail "secret-looking file sent"
grep -q "left out .env" "$SANDBOX/out" || fail "left-out secret not reported"
if [ ! -f "$POD/.claude/skills/skill-a/SKILL.md" ] || [ -e "$POD/.claude/.credentials.json" ]; then fail "setup not sent, or credentials sent"; fi
[ "$(sed -n 1p "$SANDBOX/stdin")" = "sk-claude-secret" ] || fail "stdin line 1 is not the Claude token"
[ "$(sed -n '2,$p' "$SANDBOX/stdin")" = "add a hello file" ] || fail "brief does not follow the token"
grep -q "sk-claude-secret" "$SANDBOX/calls" "$SANDBOX/claim.yaml" "$SANDBOX/out" && fail "the token leaked into kubectl arguments, the claim or the output"
grep -q "gh[ps]_\|GH_TOKEN" "$SANDBOX/calls" "$SANDBOX/stdin" && fail "a GitHub token was sent"
[ "$(cat "$W/hello.txt" 2>/dev/null)" = "hello" ] || fail "patch not applied to the worktree"
git -C "$W" diff --quiet HEAD -- tracked.txt || fail "worktree changed beyond the patch"
grep -q "did the thing" "$SANDBOX/out" || fail "summary not printed"
claim="$(sed -n 's/^  name: //p' "$SANDBOX/claim.yaml" | head -1)"
[ -f "$W/.git/remote-agent/$claim/changes.patch" ] || fail "patch not kept under .git/remote-agent/$claim"
grep -q "delete sandboxclaim $claim " "$SANDBOX/calls" || fail "claim not deleted"
grep -q "shutdownPolicy: Delete" "$SANDBOX/claim.yaml" || fail "claim does not delete on shutdown"
rm "$W/hello.txt"

# No changes: exit 3, claim still deleted.
code=0; run 0 "" || code=$?
[ "$code" = 3 ] || fail "no changes: wanted exit 3, got $code"
grep -q "delete sandboxclaim" "$SANDBOX/calls" || fail "no changes: claim not deleted"

# A failed run: non-zero, nothing applied, claim deleted.
code=0; run 1 "$patch" || code=$?
[ "$code" = 1 ] || fail "failed run: wanted exit 1, got $code"
[ ! -e "$W/hello.txt" ] || fail "failed run: patch applied anyway"
grep -q "delete sandboxclaim" "$SANDBOX/calls" || fail "failed run: claim not deleted"

# A patch that does not apply (the worktree changed meanwhile): exit 4, worktree untouched.
stale='diff --git a/tracked.txt b/tracked.txt
--- a/tracked.txt
+++ b/tracked.txt
@@ -1 +1 @@
-not what the file says
+two
'
code=0; run 0 "$stale" || code=$?
[ "$code" = 4 ] || fail "stale patch: wanted exit 4, got $code"
[ "$(cat "$W/tracked.txt")" = "one" ] || fail "stale patch: worktree changed"

# --attach sends files from outside the worktree to ~/attached, minus secrets; it works through a link,
# as install.sh puts it at ~/.agents/bin/remote-agent.
mkdir -p "$SANDBOX/run/notes"
printf 'ticket\n' >"$SANDBOX/run/01-ticket.md"
printf 'map\n' >"$SANDBOX/run/notes/map.md"
printf 'KEY=1\n' >"$SANDBOX/run/notes/.env"
# A real link, as install.sh makes it: Git Bash on Windows copies on `ln -s` unless told otherwise.
MSYS="${MSYS:+$MSYS }winsymlinks:nativestrict" ln -s "$REPO/scripts/remote-agent.sh" "$SANDBOX/bin/remote-agent"
echo 0 >"$SANDBOX/code"
printf '%s' "$patch" >"$SANDBOX/patch"
rm -rf "$POD" "$SANDBOX/calls"
(cd "$W" && env -u CLAUDE_CODE_OAUTH_TOKEN HOME="$H" PATH="$SANDBOX/bin:$PATH" XDG_CONFIG_HOME="$SANDBOX/config" \
  remote-agent --attach "$SANDBOX/run/01-ticket.md" --attach "$SANDBOX/run/notes" "build it") >"$SANDBOX/out" 2>&1 \
  || fail "attach: exited non-zero: $(cat "$SANDBOX/out")"
[ "$(cat "$POD/attached/01-ticket.md" 2>/dev/null)" = ticket ] || fail "attach: file not under ~/attached"
[ "$(cat "$POD/attached/notes/map.md" 2>/dev/null)" = map ] || fail "attach: folder not under ~/attached"
[ ! -e "$POD/attached/notes/.env" ] || fail "attach: secret-looking file sent"
rm -f "$W/hello.txt"
code=0; (cd "$W" && PATH="$SANDBOX/bin:$PATH" remote-agent --attach "$SANDBOX/nope" "x") >/dev/null 2>&1 || code=$?
[ "$code" = 1 ] || fail "attach: a missing path was not refused"

# Naming the cluster backend explicitly gives the same run.
printf 'CLAUDE_CODE_OAUTH_TOKEN=sk-claude-secret\nREMOTE_BACKEND=cluster\n' >"$SANDBOX/config/dotagents/remote.env"
run 0 "$patch" || fail "explicit cluster: exited non-zero: $(cat "$SANDBOX/out")"
[ "$(cat "$W/hello.txt" 2>/dev/null)" = "hello" ] || fail "explicit cluster: patch not applied"
grep -q "delete sandboxclaim agent-" "$SANDBOX/calls" || fail "explicit cluster: claim not deleted"
rm -f "$W/hello.txt"

# An unknown backend is refused before anything is staged or sent, naming the known ones.
printf 'CLAUDE_CODE_OAUTH_TOKEN=sk-claude-secret\nREMOTE_BACKEND=nope\n' >"$SANDBOX/config/dotagents/remote.env"
code=0; run 0 "$patch" || code=$?
[ "$code" != 0 ] || fail "unknown backend: exited 0"
[ ! -e "$SANDBOX/calls" ] || fail "unknown backend: kubectl was called"
grep -q "cluster" "$SANDBOX/out" || fail "unknown backend: message does not list cluster: $(cat "$SANDBOX/out")"
grep -qw "lib" "$SANDBOX/out" && fail "unknown backend: lib.sh listed as a backend"
grep -q "snapshot:" "$SANDBOX/out" && fail "unknown backend: something was staged"
echo "CLAUDE_CODE_OAUTH_TOKEN=sk-claude-secret" >"$SANDBOX/config/dotagents/remote.env"

# TERM mid-run still releases the sandbox through the exit cleanup: the stub's sandbox run hangs.
mv "$SANDBOX/bin/kubectl" "$SANDBOX/kubectl.real"
cat >"$SANDBOX/bin/kubectl" <<EOF
#!/usr/bin/env bash
case " \$* " in
  *" remote-agent-sandbox"*) : >"$SANDBOX/started"; sleep 2; exit 1 ;;
esac
exec "$SANDBOX/kubectl.real" "\$@"
EOF
chmod +x "$SANDBOX/bin/kubectl"
rm -rf "$POD" "$SANDBOX/calls" "$SANDBOX/started"
(cd "$W" && exec env -u CLAUDE_CODE_OAUTH_TOKEN HOME="$H" PATH="$SANDBOX/bin:$PATH" XDG_CONFIG_HOME="$SANDBOX/config" \
  "$REPO/scripts/remote-agent.sh" "add a hello file") >"$SANDBOX/out" 2>&1 &
pid=$!
for _ in $(seq 100); do [ -e "$SANDBOX/started" ] && break; sleep 0.1; done
kill -TERM "$pid"
code=0; wait "$pid" || code=$?
[ "$code" = 143 ] || fail "TERM: wanted exit 143, got $code: $(cat "$SANDBOX/out")"
grep -q "delete sandboxclaim agent-" "$SANDBOX/calls" || fail "TERM: claim not deleted"
mv "$SANDBOX/kubectl.real" "$SANDBOX/bin/kubectl"

# Without a token nothing reaches the cluster.
rm "$SANDBOX/config/dotagents/remote.env"
code=0; run 0 "$patch" || code=$?
if [ "$code" = 0 ] || [ -e "$SANDBOX/calls" ]; then fail "no token: ran anyway or called kubectl"; fi

[ "$fails" -eq 0 ] || { echo "remote-agent.test.sh: $fails failure(s)"; exit 1; }
echo "remote-agent.test.sh: ok"
