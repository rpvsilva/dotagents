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
# shellcheck source=lib/remote-stubs.sh disable=SC1091
. "$REPO/tests/lib/remote-stubs.sh"

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
# $POD/out from $SANDBOX/patch (empty file = no changes); with $SANDBOX/real, the real script runs, with a fake
# claude that writes hello.txt. The pod's main-process stdout (/proc/1/fd/1) is the file $POD/main-stdout, absent
# with $SANDBOX/nomain.
POD="$SANDBOX/pod"
mkdir -p "$SANDBOX/bin" "$SANDBOX/config/dotagents"
cat >"$SANDBOX/bin/kubectl" <<EOF
#!/usr/bin/env bash
args="\$*"
# tr for a multi-line argument (a script): bash 3.2's \${x//\$'\\n'/ } takes seconds on one.
case "\$args" in
  *\$'\\n'*) { printf '%s' "\$args" | tr '\\n' ' '; echo; } >>"$SANDBOX/calls" ;;
  *) printf '%s\n' "\$args" >>"$SANDBOX/calls" ;;
esac
mkdir -p "$POD"
[ -e "$SANDBOX/nomain" ] || touch "$POD/main-stdout"
case " \$* " in
  *" apply "*)
    cat >"$SANDBOX/claim.yaml"
    # As agent-sandbox's controller: a pod label outside the allowed domain (sandbox.users.io or a subdomain)
    # makes the claim fail with InvalidMetadata, and it never gets a sandbox.
    bad="\$(sed -n '/additionalPodMetadata/,\$ s/^    labels: {\(.*\)}\$/\1/p' "$SANDBOX/claim.yaml" | tr ',' '\n' \
      | sed 's/^ *//; s/:.*//' | grep -Eiv '^([^/]*\.)?sandbox\.users\.io/' | head -1)"
    if [ -n "\$bad" ]; then
      printf 'Ready\tFalse\tInvalidMetadata\tinvalid additionalPodMetadata: label %s: domain not in the allowlist\n' "\$bad"
    else
      printf 'Ready\tTrue\tDependenciesReady\t\n'
    fi >"$SANDBOX/state" ;;
  *" get sandboxclaim "*conditions*) cat "$SANDBOX/conditions" 2>/dev/null || cat "$SANDBOX/state" ;;
  *" get sandboxclaim "*) printf 'claude-runner-abc' ;;
  *" get sandbox "*) printf '' ;;
  *" remote-agent-sandbox"*)
    if [ -e "$SANDBOX/real" ]; then
      while [ "\$1" != "-c" ]; do shift; done
      # A pod does not get this machine's CLAUDE_CONFIG_DIR.
      tee "$SANDBOX/stdin" | env -u CLAUDE_CONFIG_DIR HOME="$POD" PATH="$SANDBOX/podbin:\$PATH" bash -c "\${2//\/proc\/1\/fd\/1/$POD/main-stdout}" "\$3"
      exit
    fi
    cat >"$SANDBOX/stdin"
    cp "$POD/main-stdout" "$SANDBOX/log-at-run" 2>/dev/null || true
    mkdir -p "$POD/out"; cp "$SANDBOX/patch" "$POD/out/changes.patch"; echo "did the thing" >"$POD/out/summary.md"
    # With $SANDBOX/evil, a hostile sandbox: links that aim the host's writes at $SANDBOX/victim, one that would have
    # it read the victim in as the session, and a forged run.json.
    if [ -e "$SANDBOX/evil" ]; then
      for l in run.json.tmp run.json.first progress.log stream.jsonl.scrubbed summary.md.scrubbed session.jsonl; do ln -s "$SANDBOX/victim" "$POD/out/\$l"; done
      echo '{"type":"result"}' >"$POD/out/stream.jsonl"
      echo '{"run":"forged","name":"forged","state":"done","exit_code":0,"pid":1}' >"$POD/out/run.json"
    fi
    exit "\$(cat "$SANDBOX/code")" ;;
  *" exec "*)
    while [ "\$1" != "-c" ]; do shift; done
    c="\${2//--warning=no-unknown-keyword/}"
    HOME="$POD" sh -c "\${c//\/proc\/1\/fd\/1/$POD/main-stdout}" ;;
esac
EOF
chmod +x "$SANDBOX/bin/kubectl"
mkdir -p "$SANDBOX/podbin"
# It also starts a session as Claude does, under $HOME/.claude/projects/<munged cwd>, holding the token and cwd
# lines; not with $SANDBOX/nosession.
cat >"$SANDBOX/podbin/claude" <<EOF
#!/usr/bin/env bash
[ "\$1" = -p ] || exit 0
if [ ! -e "$SANDBOX/nosession" ]; then
  id=0b1e5e55-1d00-4c1a-9a7e-5e551011d000
  echo "{\"type\":\"system\",\"subtype\":\"init\",\"session_id\":\"\$id\",\"cwd\":\"\$PWD\"}"
  d="\$HOME/.claude/projects/\$(printf '%s' "\$PWD" | sed 's/[^A-Za-z0-9]/-/g')"
  mkdir -p "\$d"
  printf '{"type":"user","cwd":"%s","sessionId":"%s","message":"env: CLAUDE_CODE_OAUTH_TOKEN=%s"}\n' "\$PWD" "\$id" "\$CLAUDE_CODE_OAUTH_TOKEN" >"\$d/\$id.jsonl"
  printf '{"type":"assistant","cwd":"%s","sessionId":"%s"}\n{"type":"last-prompt","sessionId":"%s"}\n' "\$PWD" "\$id" "\$id" >>"\$d/\$id.jsonl"
fi
EOF
cat >>"$SANDBOX/podbin/claude" <<'EOF'
echo "{\"type\":\"user\",\"text\":\"env: CLAUDE_CODE_OAUTH_TOKEN=$CLAUDE_CODE_OAUTH_TOKEN\"}"
echo '{"type":"assistant","message":{"content":[{"type":"text","text":"writing the hello file"},{"type":"tool_use","name":"Write","input":{"file_path":"hello.txt"}}]}}'
printf 'hello\n' >hello.txt
echo '{"type":"result","result":"did the thing","is_error":false}'
EOF
chmod +x "$SANDBOX/podbin/claude"
remote_config "$SANDBOX/config" REMOTE_BACKEND=cluster

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

# This machine's Claude config, where `remote-agent resume` places a run's session.
CC="$SANDBOX/claude-config"
# Runs remote-agent.sh in $W with the fake run exiting $1 and returning patch $2; further arguments are options.
# Stdout goes to $SANDBOX/report, stderr to $SANDBOX/err, both to $SANDBOX/out.
run() {
  echo "$1" >"$SANDBOX/code"
  printf '%s' "$2" >"$SANDBOX/patch"
  shift 2
  rm -rf "$POD" "$SANDBOX/calls" "$SANDBOX/stdin" "$SANDBOX/log-at-run"
  local code=0
  (cd "$W" && env -u CLAUDE_CODE_OAUTH_TOKEN HOME="$H" PATH="$SANDBOX/bin:$PATH" XDG_CONFIG_HOME="$SANDBOX/config" \
    CLAUDE_CONFIG_DIR="$CC" "$REPO/scripts/remote-agent.sh" "$@" "add a hello file") >"$SANDBOX/report" 2>"$SANDBOX/err" || code=$?
  cat "$SANDBOX/report" "$SANDBOX/err" >"$SANDBOX/out"
  return "$code"
}
# Fails ($3 names the case) unless every line of $SANDBOX/err starts with tag $1 and, without it, is in file $2.
logged_all() {
  local line
  while IFS= read -r line; do
    # jq.exe ends its lines with CRLF, and grep on Windows drops the CR from the log it reads.
    line="${line%$'\r'}"
    case "$line" in
      "$1 "*) grep -qxF -- "${line#"$1 "}" "$2" 2>/dev/null || fail "$3: not in the sandbox log: $line" ;;
      *) fail "$3: progress line without $1: $line" ;;
    esac
  done <"$SANDBOX/err"
}
# The run folder of the last run (its claim's name, from the stub's claim.yaml).
kept() { echo "$W/.git/remote-agent/$(sed -n 's/^  name: //p' "$SANDBOX/claim.yaml" | head -1)"; }
# Fails ($1 names the case) unless the last run's run.json is valid JSON saying state $2 and exit code $3, with its
# name $4 (null without), its run id, pid, start and end, and the claim and send times; no run.json.tmp is left; its
# progress.log holds stderr's lines without the tag; the token is in neither file.
run_data() {
  local k run
  k="$(kept)"
  run="$(basename "$k")"
  jq -e --arg st "$2" --argjson rc "$3" --arg name "${4:-}" --arg run "$run" '.state == $st and .exit_code == $rc
    and .name == (if $name == "" then null else $name end) and .run == $run and (.pid | type) == "number"
    and (.started | type) == "number" and .ended >= .started and (.claim_s | type) == "number"
    and (.send_s | type) == "number" and (.worktree | type) == "string"' "$k/run.json" >/dev/null 2>&1 \
    || fail "$1: run.json is not $2 with exit $3 and name ${4:-null}: $(cat "$k/run.json" 2>/dev/null)"
  [ -z "$(find "$k" -name 'run.json.*')" ] || fail "$1: a temporary run.json left: $(find "$k" -name 'run.json.*')"
  [ "$(tr -d '\r' <"$k/progress.log" 2>/dev/null)" = "$(tr -d '\r' <"$SANDBOX/err" | sed "s/^\[${4:-$run}\] //")" ] \
    || fail "$1: progress.log is not stderr without the tag: $(cat "$k/progress.log" 2>/dev/null)"
  grep -q "sk-claude-secret" "$k/run.json" "$k/progress.log" && fail "$1: the token is in run.json or progress.log"
  return 0
}
# The labels the claim asks for on its pod.
pod_labels() { sed -n '/additionalPodMetadata/,$ s/^    labels: {\(.*\)}$/\1/p' "$SANDBOX/claim.yaml"; }
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
[ "$(cat "$SANDBOX/report")" = "did the thing" ] || fail "stdout is not just the summary: $(cat "$SANDBOX/report")"
claim="$(sed -n 's/^  name: //p' "$SANDBOX/claim.yaml" | head -1)"
logged_all "[$claim]" "$POD/main-stdout" "progress"
grep -q "claimed $claim" "$SANDBOX/log-at-run" || fail "sandbox log: steps before the run not there during the run"
grep -q "applied to " "$POD/main-stdout" || fail "sandbox log: no final line"
grep -q "sk-claude-secret" "$POD/main-stdout" && fail "the token reached the sandbox log"
grep -qF "sandbox.users.io/remote-agent-run: $claim" "$SANDBOX/claim.yaml" || fail "claim not labelled with its run"
[ "$(pod_labels)" = "sandbox.users.io/remote-agent-run: $claim" ] || fail "pod labels: wanted only the run, got $(pod_labels)"
[ -f "$W/.git/remote-agent/$claim/changes.patch" ] || fail "patch not kept under .git/remote-agent/$claim"
grep -q "delete sandboxclaim $claim " "$SANDBOX/calls" || fail "claim not deleted"
grep -q "remote-agent: no session came back" "$SANDBOX/err" || fail "no session: not reported: $(cat "$SANDBOX/err")"
grep -q "shutdownPolicy: Delete" "$SANDBOX/claim.yaml" || fail "claim does not delete on shutdown"
# Beside the transcript: run.json says what the run is and how it ended, progress.log every progress line.
run_data "success run data" "done" 0
rm "$W/hello.txt"

# No changes: exit 3, claim still deleted.
code=0; run 0 "" || code=$?
[ "$code" = 3 ] || fail "no changes: wanted exit 3, got $code"
grep -q "delete sandboxclaim" "$SANDBOX/calls" || fail "no changes: claim not deleted"
run_data "no changes run data" "done" 3

# A failed run: non-zero, nothing applied, claim deleted.
code=0; run 1 "$patch" || code=$?
[ "$code" = 1 ] || fail "failed run: wanted exit 1, got $code"
[ ! -e "$W/hello.txt" ] || fail "failed run: patch applied anyway"
grep -q "delete sandboxclaim" "$SANDBOX/calls" || fail "failed run: claim not deleted"
run_data "failed run data" failed 1

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
run_data "stale patch run data" failed 4

# A hostile sandbox's out archive cannot write outside the run folder through links, nor replace run.json: its links
# named after the files the host writes (run.json.tmp, progress.log, the scrub's temporaries) aim at $SANDBOX/victim.
printf 'untouched\n' >"$SANDBOX/victim"
: >"$SANDBOX/evil"
run 0 "$patch" || fail "hostile out: exited non-zero: $(cat "$SANDBOX/out")"
rm -f "$SANDBOX/evil"
[ "$(cat "$SANDBOX/victim")" = "untouched" ] || fail "hostile out: a file outside the run folder was written: $(cat "$SANDBOX/victim")"
run_data "hostile out run data" "done" 0
k="$(kept)"
[ -z "$(find "$k" -type l)" ] || fail "hostile out: links kept in the run folder: $(find "$k" -type l)"
[ "$(cat "$k/summary.md" 2>/dev/null)" = "did the thing" ] || fail "hostile out: summary.md not kept"
[ -s "$k/changes.patch" ] || fail "hostile out: changes.patch not kept"
[ -s "$k/stream.jsonl" ] || fail "hostile out: stream.jsonl not kept"
# Where ln -s and tar make a link a copy of its target (Git Bash without native symlinks), the archive's session.jsonl
# is a regular file, read in as data; so this holds only where a probe link survives a tar round trip.
mkdir -p "$SANDBOX/probe/in" "$SANDBOX/probe/out"
ln -s "$SANDBOX/victim" "$SANDBOX/probe/in/link" 2>/dev/null || true
(cd "$SANDBOX/probe/in" && tar -cf - .) | (cd "$SANDBOX/probe/out" && tar -xf -) 2>/dev/null || true
if [ -L "$SANDBOX/probe/out/link" ]; then
  [ ! -e "$k/session.jsonl" ] || fail "hostile out: a linked session.jsonl was read in: $(cat "$k/session.jsonl")"
else
  echo "remote-agent.test.sh: links are copies here (ln -s or tar), hostile out's linked session.jsonl check skipped"
fi
rm -rf "$SANDBOX/probe" "$W/hello.txt"

# The real sandbox script copies its own progress (Claude's lines) to the pod's log; the token stays out of it.
: >"$SANDBOX/real"
run 0 "" || fail "real sandbox: exited non-zero: $(cat "$SANDBOX/out")"
claim="$(sed -n 's/^  name: //p' "$SANDBOX/claim.yaml" | head -1)"
[ "$(cat "$W/hello.txt" 2>/dev/null)" = "hello" ] || fail "real sandbox: patch not applied: $(cat "$SANDBOX/out")"
logged_all "[$claim]" "$POD/main-stdout" "real sandbox"
grep -q "→ Write hello.txt" "$POD/main-stdout" || fail "real sandbox: Claude's progress not in the sandbox log"
grep -q "sk-claude-secret" "$POD/main-stdout" "$SANDBOX/out" && fail "real sandbox: the token reached a log"
# Its raw events come back on the run's stdout into the transcript, not onto stdout.
t="$W/.git/remote-agent/$claim/transcript.jsonl"
[ "$(sed 's/\r$//' "$t" 2>/dev/null | jq -r .type 2>/dev/null | tr -d '\r' | tr '\n' ' ')" = "system user assistant result " ] \
  || fail "real sandbox: transcript does not hold Claude's events: $(cat "$t" 2>/dev/null)"
grep -q "first agent event after [0-9]*s" "$SANDBOX/err" || fail "real sandbox: no first-event time"
[ "$(cat "$SANDBOX/report")" = "did the thing" ] || fail "real sandbox: stdout is not just the summary: $(cat "$SANDBOX/report")"
# No file the run keeps holds the token: the stub's stream and session both echo it.
k="$W/.git/remote-agent/$claim"
grep -rl "sk-claude-secret" "$k" && fail "real sandbox: the token is in a kept file"
grep -qF 'CLAUDE_CODE_OAUTH_TOKEN=[redacted]' "$k/stream.jsonl" || fail "real sandbox: the token not replaced by [redacted] in the stream"
# The session comes back and is kept with the run, but nothing is placed for /resume: the sandbox wrote it.
[ -f "$k/session.jsonl" ] || fail "real sandbox: session not kept with the run"
[ ! -e "$CC/projects" ] || fail "real sandbox: session placed without being asked: $(find "$CC" -type f)"
# The script prints the resolved path (macOS: /var is /private/var), so match its tail.
grep -q "remote-agent: session: .*/.git/remote-agent/$claim/session.jsonl .*remote-agent resume $claim " "$SANDBOX/err" \
  || fail "real sandbox: how to resume not printed: $(cat "$SANDBOX/err")"
grep -q "sessionId\|sk-claude-secret" "$k/changes.patch" && fail "real sandbox: the session is in the patch"
git -C "$W" status --porcelain | grep -q "session" && fail "real sandbox: the session shows in the worktree"
# `remote-agent resume <run>` places it under this machine's Claude config in the worktree's project folder (from its
# Windows path under Git Bash), its cwd that path, after warning that it came from an untrusted sandbox.
# Runs `remote-agent resume` in $W with the arguments given; stderr to $SANDBOX/err.
resume() {
  (cd "$W" && env HOME="$H" PATH="${RESUME_PATH:-$PATH}" CLAUDE_CONFIG_DIR="$CC" "$REPO/scripts/remote-agent.sh" resume "$@") \
    >/dev/null 2>"$SANDBOX/err"
}
real_w="$(cd "$W" && pwd -P)"
if command -v cygpath >/dev/null 2>&1; then real_w="$(cygpath -w "$real_w")"; fi
s="$CC/projects/$(printf '%s' "$real_w" | sed 's/[^A-Za-z0-9]/-/g')/0b1e5e55-1d00-4c1a-9a7e-5e551011d000.jsonl"
resume "$claim" || fail "resume: exited non-zero: $(cat "$SANDBOX/err")"
[ -f "$s" ] || fail "resume: session not placed at $s: $(find "$CC" -type f 2>/dev/null) $(cat "$SANDBOX/err")"
[ "$(sed 's/\r$//' "$s" 2>/dev/null | jq -r 'select(.cwd) | .cwd' | tr -d '\r' | sort | uniq -c | sed 's/^ *//')" = "2 $real_w" ] \
  || fail "resume: session cwd not the worktree's real path: $(cat "$s" 2>/dev/null)"
[ "$(grep -c . "$s" 2>/dev/null)" = 3 ] || fail "resume: session lines lost: $(cat "$s" 2>/dev/null)"
grep -q "sk-claude-secret" "$s" && fail "resume: the token is in the session"
grep -qF 'CLAUDE_CODE_OAUTH_TOKEN=[redacted]' "$s" || fail "resume: the token not replaced by [redacted] in the session"
at() { grep -n "$1" "$SANDBOX/err" | head -1 | cut -d: -f1 || true; }
warned="$(at "untrusted sandbox")"
placed="$(at "remote-agent: session: $s")"
if [ -z "$warned" ] || [ -z "$placed" ] || [ "$warned" -ge "$placed" ]; then
  fail "resume: no untrusted-sandbox warning before the placed path: $(cat "$SANDBOX/err")"
fi
# Hand-made runs: a session with non-object lines keeps them; one that is not JSON lines leaves nothing placed; under
# Git Bash (a stub cygpath) the folder and cwd come from the Windows path; a folder name over 200 characters is refused.
hand="$W/.git/remote-agent/agent-hand"
mkdir -p "$hand"
printf '%s\n' '{"sessionId":"5e55-0001","cwd":"/sandbox/work"}' '"a string"' '[1,2]' '{"type":"x"}' >"$hand/session.jsonl"
rm -rf "$CC"
resume agent-hand || fail "non-object lines: exited non-zero: $(cat "$SANDBOX/err")"
s="$(find "$CC/projects" -name 5e55-0001.jsonl 2>/dev/null | head -1)"
[ "$(sed 's/\r$//' "$s" 2>/dev/null | jq -c . | tr -d '\r' | tail -3 | tr '\n' ' ')" = '"a string" [1,2] {"type":"x"} ' ] \
  || fail "non-object lines: not kept as they were: $(cat "$s" 2>/dev/null)"
printf '%s\n' '{"sessionId":"5e55-0002","cwd":"/sandbox/work"}' 'not json' >"$hand/session.jsonl"
rm -rf "$CC"
code=0; resume agent-hand || code=$?
[ "$code" != 0 ] || fail "not JSON lines: exited 0"
grep -q "not JSON lines" "$SANDBOX/err" || fail "not JSON lines: not said: $(cat "$SANDBOX/err")"
[ -z "$(find "$CC/projects" 2>/dev/null)" ] || fail "not JSON lines: left under projects/: $(find "$CC/projects")"
[ -z "$(find "$hand" -type f ! -name session.jsonl)" ] || fail "not JSON lines: left a partial file: $(find "$hand" -type f)"
mkdir -p "$SANDBOX/winbin"
cat >"$SANDBOX/winbin/cygpath" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$STUB_WINPATH"
EOF
chmod +x "$SANDBOX/winbin/cygpath"
printf '%s\n' '{"sessionId":"5e55-0003","cwd":"/sandbox/work"}' >"$hand/session.jsonl"
rm -rf "$CC"
STUB_WINPATH='C:\Users\x\proj' RESUME_PATH="$SANDBOX/winbin:$PATH" resume agent-hand \
  || fail "windows path: exited non-zero: $(cat "$SANDBOX/err")"
s="$CC/projects/C--Users-x-proj/5e55-0003.jsonl"
[ "$(sed 's/\r$//' "$s" 2>/dev/null | jq -r .cwd | tr -d '\r')" = 'C:\Users\x\proj' ] \
  || fail "windows path: not placed at $s with the Windows cwd: $(find "$CC" -type f 2>/dev/null) $(cat "$SANDBOX/err")"
rm -rf "$CC"
code=0; STUB_WINPATH="C:\\$(printf 'a%.0s' $(seq 200))" RESUME_PATH="$SANDBOX/winbin:$PATH" resume agent-hand || code=$?
[ "$code" != 0 ] || fail "long path: exited 0"
grep -q "over Claude Code's 200" "$SANDBOX/err" || fail "long path: not said: $(cat "$SANDBOX/err")"
[ ! -e "$CC/projects" ] || fail "long path: something placed: $(find "$CC" -type f)"
rm -rf "$hand" "$CC"
# A run whose session is missing still applies its patch, and says so. Without a main-process stdout (SSH bare mode)
# the sandbox's log is ~/progress.log.
rm -f "$W/hello.txt"
: >"$SANDBOX/nosession"
: >"$SANDBOX/nomain"
run 0 "" || fail "real no session: exited non-zero: $(cat "$SANDBOX/out")"
[ "$(cat "$W/hello.txt" 2>/dev/null)" = "hello" ] || fail "real no session: patch not applied"
grep -q "== no session file" "$POD/progress.log" 2>/dev/null || fail "real no session: the sandbox did not report it: $(cat "$SANDBOX/err")"
grep -q "remote-agent: no session came back" "$SANDBOX/err" || fail "real no session: not reported: $(cat "$SANDBOX/err")"
[ ! -e "$CC/projects" ] || fail "real no session: something placed: $(find "$CC" -type f)"
grep -q "→ Write hello.txt" "$POD/progress.log" 2>/dev/null || fail "progress.log: Claude's progress missing"
grep -q "snapshot: " "$POD/progress.log" 2>/dev/null || fail "progress.log: client progress missing"
rm -f "$W/hello.txt" "$SANDBOX/nosession" "$SANDBOX/nomain" "$SANDBOX/real"

# --name labels the claim and its pod with the task and tags the progress; it is made a valid label value. run.json
# keeps the name though --attach comes after it.
mkdir -p "$SANDBOX/att"
printf 'ticket\n' >"$SANDBOX/att/ticket.md"
run 0 "$patch" --name "07-Visible runs!" --attach "$SANDBOX/att/ticket.md" || fail "name: exited non-zero: $(cat "$SANDBOX/out")"
claim="$(sed -n 's/^  name: //p' "$SANDBOX/claim.yaml" | head -1)"
[ "$(pod_labels)" = "sandbox.users.io/remote-agent-run: $claim, sandbox.users.io/remote-agent-task: 07-visible-runs" ] || fail "name: pod labels are $(pod_labels)"
grep -qF "sandbox.users.io/remote-agent-task: 07-visible-runs" "$SANDBOX/claim.yaml" || fail "name: claim not labelled"
logged_all "[07-visible-runs]" "$POD/main-stdout" "name"
[ "$(cat "$SANDBOX/report")" = "did the thing" ] || fail "name: stdout changed: $(cat "$SANDBOX/report")"
run_data "name run data" "done" 0 07-visible-runs
rm -f "$W/hello.txt"
# A long name is cut to 63 characters. Settings left in remote.env from before settings.json are ignored, and the run
# names them and how to move them.
echo "REMOTE_BACKEND=docker" >>"$SANDBOX/config/dotagents/remote.env"
run 0 "$patch" --name "$(printf 'A%.0s' $(seq 80))_x" || fail "long name, leftover: exited non-zero: $(cat "$SANDBOX/out")"
task="$(pod_labels | sed -n 's/.*sandbox\.users\.io\/remote-agent-task: //p')"
[ "$task" = "$(printf 'a%.0s' $(seq 63))" ] || fail "long name: wanted 63 a's, got $task"
[ "$(cat "$W/hello.txt" 2>/dev/null)" = "hello" ] || fail "leftover: patch not applied: $(cat "$SANDBOX/out")"
grep -q "delete sandboxclaim agent-" "$SANDBOX/calls" || fail "leftover: the old REMOTE_BACKEND was used, not settings.json"
grep -q "REMOTE_BACKEND.*remote-agent setup" "$SANDBOX/err" || fail "leftover: not named: $(cat "$SANDBOX/err")"
rm -f "$W/hello.txt"
remote_config "$SANDBOX/config" REMOTE_BACKEND=cluster
code=0; run 0 "$patch" --name "!!" || code=$?
if [ "$code" != 1 ] || [ -e "$SANDBOX/calls" ]; then fail "name without letters or digits: not refused"; fi

# A claim the controller fails (e.g. a label it does not allow) stops the run at once with the controller's reason;
# one that is never Ready stops at REMOTE_CLAIM_TIMEOUT with its conditions. Either way the claim is deleted.
printf 'Ready\tFalse\tInvalidMetadata\tinvalid additionalPodMetadata: label domain "x" is not in the allowlist\n' >"$SANDBOX/conditions"
start=$SECONDS; code=0; run 0 "$patch" || code=$?
[ "$code" = 1 ] || fail "claim failed: wanted exit 1, got $code"
[ $((SECONDS - start)) -lt 20 ] || fail "claim failed: waited $((SECONDS - start))s instead of stopping at once"
grep -q 'InvalidMetadata: invalid additionalPodMetadata: label domain "x" is not in the allowlist' "$SANDBOX/err" \
  || fail "claim failed: controller's reason not printed: $(cat "$SANDBOX/err")"
grep -q "delete sandboxclaim agent-" "$SANDBOX/calls" || fail "claim failed: claim not deleted"
printf 'Ready\tFalse\tSandboxNotReady\tSandbox is not ready\n' >"$SANDBOX/conditions"
code=0; REMOTE_CLAIM_TIMEOUT=1 run 0 "$patch" || code=$?
[ "$code" = 1 ] || fail "claim timeout: wanted exit 1, got $code"
grep -q "not Ready after 1s" "$SANDBOX/err" || fail "claim timeout: no timeout message: $(cat "$SANDBOX/err")"
grep -q "Ready=False SandboxNotReady: Sandbox is not ready" "$SANDBOX/err" || fail "claim timeout: conditions not printed"
grep -q "delete sandboxclaim agent-" "$SANDBOX/calls" || fail "claim timeout: claim not deleted"
rm "$SANDBOX/conditions"

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

# Refused before anything is staged or sent: an unknown backend (hand-edited, naming the known ones), no backend
# (remote is off; pointing to setup), and a repo that sets remote.allowed to false.
refused() {
  local code=0
  run 0 "$patch" || code=$?
  [ "$code" != 0 ] || fail "$1: exited 0"
  [ ! -e "$SANDBOX/calls" ] || fail "$1: kubectl was called"
  grep -q "snapshot:" "$SANDBOX/out" && fail "$1: something was staged"
  grep -q "$2" "$SANDBOX/out" || fail "$1: message does not name $2: $(cat "$SANDBOX/out")"
}
echo '{"remote": {"backend": "nope"}}' >"$SANDBOX/config/dotagents/settings.json"
refused "unknown backend" "cluster, docker, ssh"
grep -qw "lib" "$SANDBOX/out" && fail "unknown backend: lib.sh listed as a backend"
remote_config "$SANDBOX/config"
refused "no backend" "remote-agent setup"
remote_config "$SANDBOX/config" REMOTE_BACKEND=cluster
mkdir -p "$W/.agents"
echo '{"remote": {"allowed": false}}' >"$W/.agents/settings.json"
refused "not allowed" "remote.allowed"
rm -rf "$W/.agents"

# ready: whether skills may send this repo's subagents to the backend; the backend on stdout, else why not.
ready() { (cd "$W" && env -u CLAUDE_CODE_OAUTH_TOKEN HOME="$H" PATH="$SANDBOX/bin:$PATH" XDG_CONFIG_HOME="$SANDBOX/config" \
  "$REPO/scripts/remote-agent.sh" ready) >"$SANDBOX/out" 2>&1; }
ready && [ "$(cat "$SANDBOX/out")" = cluster ] || fail "ready: $(cat "$SANDBOX/out")"
mkdir -p "$W/.agents"
echo '{"remote": {"allowed": false}}' >"$W/.agents/settings.json"
ready && fail "ready: yes though the repo does not allow remote runs"
grep -q "remote.allowed" "$SANDBOX/out" || fail "ready, not allowed: $(cat "$SANDBOX/out")"
rm -rf "$W/.agents"
remote_config "$SANDBOX/config"
ready && fail "ready: yes with no backend"
grep -q "remote-agent setup" "$SANDBOX/out" || fail "ready, no backend: $(cat "$SANDBOX/out")"
remote_config "$SANDBOX/config" REMOTE_BACKEND=cluster
rm "$SANDBOX/config/dotagents/remote.env"
ready && fail "ready: yes with no token"
grep -q "CLAUDE_CODE_OAUTH_TOKEN" "$SANDBOX/out" || fail "ready, no token: $(cat "$SANDBOX/out")"
remote_config "$SANDBOX/config" REMOTE_BACKEND=cluster

# The transcript is written live: the stub's sandbox run prints a banner and two events (one echoing the token),
# touches $SANDBOX/started and waits for $SANDBOX/go; the events are in transcript.jsonl while the run is alive,
# the banner never, the token redacted. Its path is printed before the claim.
mv "$SANDBOX/bin/kubectl" "$SANDBOX/kubectl.real"
cat >"$SANDBOX/bin/kubectl" <<EOF
#!/usr/bin/env bash
case " \$* " in
  *" remote-agent-sandbox"*)
    cat >/dev/null
    echo "Warning: not an event"
    echo '{"type":"system","subtype":"init"}'
    echo '{"type":"user","text":"env: CLAUDE_CODE_OAUTH_TOKEN=sk-claude-secret"}'
    : >"$SANDBOX/started"
    for _ in \$(seq 300); do [ -e "$SANDBOX/go" ] && break; sleep 0.1; done
    mkdir -p "$POD/out"; cp "$SANDBOX/patch" "$POD/out/changes.patch"; echo "did the thing" >"$POD/out/summary.md"
    exit 0 ;;
esac
exec "$SANDBOX/kubectl.real" "\$@"
EOF
chmod +x "$SANDBOX/bin/kubectl"
printf '%s' "$patch" >"$SANDBOX/patch"
rm -rf "$POD" "$SANDBOX/calls" "$SANDBOX/started" "$SANDBOX/go" "$W/.git/remote-agent"
# Under a German locale where there is one: ps -o lstart= follows it on macOS, and pid_start must not.
de="$(locale -a 2>/dev/null | grep -iE '^de_DE\.utf-?8$' | head -1)" || de=""
[ -n "$de" ] || echo "remote-agent.test.sh: no de_DE.UTF-8 locale, pid_start locale case runs in the current one"
(cd "$W" && exec env -u CLAUDE_CODE_OAUTH_TOKEN ${de:+LC_ALL="$de"} HOME="$H" PATH="$SANDBOX/bin:$PATH" XDG_CONFIG_HOME="$SANDBOX/config" \
  "$REPO/scripts/remote-agent.sh" "add a hello file") >"$SANDBOX/report" 2>"$SANDBOX/err" &
pid=$!
for _ in $(seq 100); do [ -e "$SANDBOX/started" ] && break; sleep 0.1; done
live=""
for _ in $(seq 100); do
  if grep -qs '"subtype":"init"' "$W"/.git/remote-agent/*/transcript.jsonl; then live=1; break; fi
  sleep 0.1
done
[ -n "$live" ] || fail "live transcript: event not in transcript.jsonl during the run"
# While the run goes on, run.json (valid at every read) says running, with the three timings, and progress.log already
# holds their lines.
live=""
for _ in $(seq 100); do
  for k in "$W"/.git/remote-agent/*/; do
    jq -e . "$k/run.json" >/dev/null 2>&1 || fail "live run data: run.json not valid JSON: $(cat "$k/run.json" 2>/dev/null)"
  done
  if jq -e '.state == "running" and ([.claim_s, .send_s, .first_event_s] | all(type == "number"))' \
    "$W"/.git/remote-agent/*/run.json >/dev/null 2>&1 \
    && [ "$(grep -cE '^remote-agent: (claimed in|sent setup and snapshot in|first agent event after) [0-9]+s$' \
      "$W"/.git/remote-agent/*/progress.log 2>/dev/null)" = 3 ]; then live=1; break; fi
  sleep 0.1
done
[ -n "$live" ] || fail "live run data: not running with its timings during the run: $(cat "$W"/.git/remote-agent/*/run.json "$W"/.git/remote-agent/*/progress.log 2>/dev/null)"
kill -0 "$pid" 2>/dev/null || fail "live transcript: the run ended before the check"
# pid and pid_start name the live remote-agent, so a reader can tell it from a reused pid (null where ps has no lstart),
# in the C locale whatever remote-agent's.
ps_start="$(LC_ALL=C ps -o lstart= -p "$pid" 2>/dev/null | sed 's/^ *//; s/ *$//')" || ps_start=""
jq -e --argjson pid "$pid" --arg ps "$ps_start" '.pid == $pid and .pid_start == (if $ps == "" then null else $ps end)' \
  "$W"/.git/remote-agent/*/run.json >/dev/null 2>&1 \
  || fail "live run data: pid and pid_start not the run's ($pid, $ps_start): $(cat "$W"/.git/remote-agent/*/run.json 2>/dev/null)"
: >"$SANDBOX/go"
wait "$pid" || fail "live transcript: exited non-zero: $(cat "$SANDBOX/err")"
claim="$(sed -n 's/^  name: //p' "$SANDBOX/claim.yaml" | head -1)"
t="$W/.git/remote-agent/$claim/transcript.jsonl"
[ "$(grep -c . "$t" 2>/dev/null)" = 2 ] || fail "live transcript: wanted the 2 events, got: $(cat "$t" 2>/dev/null)"
grep -q "not an event" "$t" 2>/dev/null && fail "live transcript: a non-JSON line was kept"
grep -q "sk-claude-secret" "$t" 2>/dev/null && fail "live transcript: the token was kept"
grep -qF 'CLAUDE_CODE_OAUTH_TOKEN=[redacted]' "$t" 2>/dev/null || fail "live transcript: the token not replaced by [redacted]"
grep -q "sk-claude-secret" "$SANDBOX/report" "$SANDBOX/err" && fail "live transcript: the token reached the output"
grep -q "Warning: not an event" "$SANDBOX/report" "$SANDBOX/err" && fail "live transcript: the banner reached the output"
[ "$(cat "$SANDBOX/report")" = "did the thing" ] || fail "live transcript: stdout is not just the summary: $(cat "$SANDBOX/report")"
p="$(at "remote-agent: transcript: .*/remote-agent/$claim/transcript.jsonl\$")"
c="$(at "remote-agent: claimed in [0-9]*s\$")"
if [ -z "$p" ] || [ -z "$c" ] || [ "$p" -ge "$c" ]; then fail "live transcript: path line not before the claim line: $(cat "$SANDBOX/err")"; fi
[ -n "$(at "remote-agent: sent setup and snapshot in [0-9]*s\$")" ] || fail "live transcript: no send time: $(cat "$SANDBOX/err")"
[ -n "$(at "remote-agent: first agent event after [0-9]*s\$")" ] || fail "live transcript: no first-event time: $(cat "$SANDBOX/err")"
[ "$(grep -c "first agent event" "$SANDBOX/err")" = 1 ] || fail "live transcript: first-event time printed more than once"
logged_all "[$claim]" "$POD/main-stdout" "live transcript"
git -C "$W" status --porcelain | grep -q "transcript" && fail "live transcript: the transcript shows in the worktree"
grep -q "transcript" "$W/.git/remote-agent/$claim/changes.patch" && fail "live transcript: the transcript is in the patch"
run_data "live run data" "done" 0
jq -e '.first_event_s | type == "number"' "$(kept)/run.json" >/dev/null 2>&1 || fail "live run data: first_event_s lost at the end"
rm -f "$W/hello.txt" "$SANDBOX/started" "$SANDBOX/go"
mv "$SANDBOX/kubectl.real" "$SANDBOX/bin/kubectl"

# TERM mid-run still releases the sandbox through the exit cleanup, and stops the backend's `kubectl exec` too: the
# stub's sandbox run hangs, its pid in $SANDBOX/backend.pid.
mv "$SANDBOX/bin/kubectl" "$SANDBOX/kubectl.real"
cat >"$SANDBOX/bin/kubectl" <<EOF
#!/usr/bin/env bash
case " \$* " in
  *" remote-agent-sandbox"*) echo \$\$ >"$SANDBOX/backend.pid"; : >"$SANDBOX/started"; exec sleep 30 ;;
esac
exec "$SANDBOX/kubectl.real" "\$@"
EOF
chmod +x "$SANDBOX/bin/kubectl"
rm -rf "$POD" "$SANDBOX/calls" "$SANDBOX/started"
(cd "$W" && exec env -u CLAUDE_CODE_OAUTH_TOKEN HOME="$H" PATH="$SANDBOX/bin:$PATH" XDG_CONFIG_HOME="$SANDBOX/config" \
  "$REPO/scripts/remote-agent.sh" "add a hello file") >"$SANDBOX/out" 2>&1 &
pid=$!
for _ in $(seq 100); do [ -e "$SANDBOX/started" ] && break; sleep 0.1; done
start=$SECONDS
kill -TERM "$pid"
code=0; wait "$pid" || code=$?
[ "$code" = 143 ] || fail "TERM: wanted exit 143, got $code: $(cat "$SANDBOX/out")"
[ $((SECONDS - start)) -lt 10 ] || fail "TERM: waited for the sandbox run to end"
grep -q "delete sandboxclaim agent-" "$SANDBOX/calls" || fail "TERM: claim not deleted"
if alive "$(cat "$SANDBOX/backend.pid" 2>/dev/null)"; then
  fail "TERM: the backend's kubectl exec outlived remote-agent"
  kill "$(cat "$SANDBOX/backend.pid")"
fi
jq -e '.state == "failed" and .exit_code == 143 and (.ended | type) == "number"' "$(kept)/run.json" >/dev/null 2>&1 \
  || fail "TERM: run.json not failed with exit 143: $(cat "$(kept)/run.json" 2>/dev/null)"
# INT does the same, exit 130. A background job of a script starts with INT ignored, which bash cannot undo: perl
# sets it back to the default before running remote-agent.
if command -v perl >/dev/null 2>&1; then
  rm -rf "$POD" "$SANDBOX/calls" "$SANDBOX/started"
  # shellcheck disable=SC2016 # perl's variables
  (cd "$W" && exec env -u CLAUDE_CODE_OAUTH_TOKEN HOME="$H" PATH="$SANDBOX/bin:$PATH" XDG_CONFIG_HOME="$SANDBOX/config" \
    perl -e '$SIG{INT} = "DEFAULT"; exec @ARGV or die' "$REPO/scripts/remote-agent.sh" "add a hello file") >"$SANDBOX/out" 2>&1 &
  pid=$!
  for _ in $(seq 100); do [ -e "$SANDBOX/started" ] && break; sleep 0.1; done
  kill -INT "$pid"
  code=0; wait "$pid" || code=$?
  [ "$code" = 130 ] || fail "INT: wanted exit 130, got $code: $(cat "$SANDBOX/out")"
  grep -q "delete sandboxclaim agent-" "$SANDBOX/calls" || fail "INT: claim not deleted"
  jq -e '.state == "failed" and .exit_code == 130 and (.ended | type) == "number"' "$(kept)/run.json" >/dev/null 2>&1 \
    || fail "INT: run.json not failed with exit 130: $(cat "$(kept)/run.json" 2>/dev/null)"
  if alive "$(cat "$SANDBOX/backend.pid" 2>/dev/null)"; then kill "$(cat "$SANDBOX/backend.pid")"; fi
else
  echo "remote-agent.test.sh: no perl, INT case skipped"
fi
# kill -9 runs no trap: run.json stays running, with a pid no longer alive (a reader's stopped run). The backend's
# stderr here carries the token: progress.log has it redacted.
cat >"$SANDBOX/bin/kubectl" <<EOF
#!/usr/bin/env bash
case " \$* " in
  *" remote-agent-sandbox"*)
    echo "client says sk-claude-secret" >&2
    echo \$\$ >"$SANDBOX/backend.pid"; : >"$SANDBOX/started"; exec sleep 30 ;;
esac
exec "$SANDBOX/kubectl.real" "\$@"
EOF
rm -rf "$POD" "$SANDBOX/calls" "$SANDBOX/started"
(cd "$W" && exec env -u CLAUDE_CODE_OAUTH_TOKEN HOME="$H" PATH="$SANDBOX/bin:$PATH" XDG_CONFIG_HOME="$SANDBOX/config" \
  "$REPO/scripts/remote-agent.sh" "add a hello file") >"$SANDBOX/out" 2>&1 &
pid=$!
for _ in $(seq 100); do [ -e "$SANDBOX/started" ] && break; sleep 0.1; done
for _ in $(seq 50); do grep -qs "client says" "$(kept)/progress.log" && break; sleep 0.1; done
kill -9 "$pid"
wait "$pid" 2>/dev/null || true
k="$(kept)"
jq -e --argjson pid "$pid" '.state == "running" and .pid == $pid and .exit_code == null' "$k/run.json" >/dev/null 2>&1 \
  || fail "kill -9: run.json not running with pid $pid: $(cat "$k/run.json" 2>/dev/null)"
alive "$(jq -r .pid "$k/run.json" 2>/dev/null | tr -d '\r')" && fail "kill -9: the recorded pid is still alive"
grep -qF "client says [redacted]" "$k/progress.log" || fail "kill -9: the backend's line not in progress.log, redacted: $(cat "$k/progress.log")"
grep -q "sk-claude-secret" "$k/run.json" "$k/progress.log" && fail "kill -9: the token is in run.json or progress.log"
# What kill -9 left running: the backend's client. It is meant to be alive here, so stop it without waiting.
kill "$(cat "$SANDBOX/backend.pid" 2>/dev/null)" 2>/dev/null || true
mv "$SANDBOX/kubectl.real" "$SANDBOX/bin/kubectl"

# Refused before anything reaches the cluster, each naming why: an empty brief, two attachments with one name, a
# snapshot over REMOTE_MAX_MB.
refused() {
  local want="$1" code=0
  shift
  rm -f "$SANDBOX/calls"
  (cd "$W" && env -u CLAUDE_CODE_OAUTH_TOKEN HOME="$H" PATH="$SANDBOX/bin:$PATH" XDG_CONFIG_HOME="$SANDBOX/config" \
    "$@") >"$SANDBOX/out" 2>&1 || code=$?
  [ "$code" = 1 ] || fail "refused ($want): wanted exit 1, got $code: $(cat "$SANDBOX/out")"
  grep -q -- "$want" "$SANDBOX/out" || fail "refused ($want): not said: $(cat "$SANDBOX/out")"
  [ ! -e "$SANDBOX/calls" ] || fail "refused ($want): kubectl was called: $(cat "$SANDBOX/calls")"
}
refused "empty brief" "$REPO/scripts/remote-agent.sh" ""
mkdir -p "$SANDBOX/a/notes"
refused "two attachments named notes" "$REPO/scripts/remote-agent.sh" --attach "$SANDBOX/run/notes" --attach "$SANDBOX/a/notes" "x"
# 2 MB of random bytes against the smallest limit the Setting allows (1 MB; 0 is refused as a bad value).
head -c 2097152 /dev/urandom >"$W/big.bin"
refused "over REMOTE_MAX_MB=1" env REMOTE_MAX_MB=1 "$REPO/scripts/remote-agent.sh" "x"
rm -f "$W/big.bin"

# Without a token nothing reaches the cluster.
rm "$SANDBOX/config/dotagents/remote.env"
code=0; run 0 "$patch" || code=$?
if [ "$code" = 0 ] || [ -e "$SANDBOX/calls" ]; then fail "no token: ran anyway or called kubectl"; fi

[ "$fails" -eq 0 ] || { echo "remote-agent.test.sh: $fails failure(s)"; exit 1; }
echo "remote-agent.test.sh: ok"
