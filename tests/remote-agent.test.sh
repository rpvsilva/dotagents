#!/usr/bin/env bash
# Runs scripts/remote-agent-pack.sh on a throwaway $HOME, and scripts/remote-agent.sh against a stub kubectl
# that plays the pod with a local folder: what reaches the sandbox, how the token travels, and that the
# patch comes back and the claim goes away.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
fails=0
# A pod starts with no shell prefix: drop the one this suite may inherit from a capped sandbox it runs in.
unset CLAUDE_CODE_SHELL_PREFIX

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
# With a home argument it stages that home's files and nothing of $HOME's.
H2="$SANDBOX/home2"
mkdir -p "$H2/.claude/skills/only-here" "$H2/.agents/hooks"
printf -- '---\nname: only-here\n---\n' >"$H2/.claude/skills/only-here/SKILL.md"
printf 'other instructions\n' >"$H2/.claude/CLAUDE.md"
printf '{"model":"sonnet"}' >"$H2/.claude/settings.json"
printf '{"mcpServers":{"other":{"type":"http","url":"https://o.example.com"}}}' >"$H2/.claude.json"
P2="$SANDBOX/packed2"
HOME="$H" "$REPO/scripts/remote-agent-pack.sh" "$P2" "$H2" 2>"$SANDBOX/pack2.err" || fail "pack home: exited non-zero: $(cat "$SANDBOX/pack2.err")"
[ -f "$P2/.claude/skills/only-here/SKILL.md" ] || fail "pack home: the given home's skill not copied"
[ ! -e "$P2/.claude/skills/skill-a" ] && [ ! -e "$P2/.agents/hooks/guard.sh" ] || fail "pack home: a file of \$HOME was copied"
[ "$(cat "$P2/.claude/CLAUDE.md")" = "other instructions" ] || fail "pack home: CLAUDE.md not the given home's"
jq -e '.model == "sonnet" and .enabledPlugins == null' "$P2/.claude/settings.json" >/dev/null || fail "pack home: settings not the given home's"
jq -e 'keys == ["other"]' "$P2/.claude/remote-mcp.json" >/dev/null || fail "pack home: MCP servers not the given home's"
[ -x "$P2/.agents/bin/capped" ] || fail "pack home: capped missing"
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
# claude that writes hello.txt, and its arguments after the script (the caps); with $SANDBOX/nocapped, the setup sent
# has no capped. The pod's main-process stdout (/proc/1/fd/1) is the file $POD/main-stdout, absent with
# $SANDBOX/nomain. With $SANDBOX/vanish, the fake run prints that file on stderr and fails, as a run whose container
# went away mid-run.
POD="$SANDBOX/pod"
# The sandbox reads its memory limit from this cgroup folder: none here, unless a case writes one.
export REMOTE_AGENT_CGROUP="$SANDBOX/cgroup"
mkdir -p "$SANDBOX/bin" "$SANDBOX/config/dotagents"
cat >"$SANDBOX/bin/kubectl" <<EOF
#!/usr/bin/env bash
args="\$*"
# tr for a multi-line argument (a script): bash 3.2's \${x//\$'\\n'/ } takes seconds on one.
case "\$args" in
  *\$'\\n'*) { printf '%s' "\$args" | tr '\\n' ' '; echo; } >>"$SANDBOX/calls" ;;
  *) printf '%s\n' "\$args" >>"$SANDBOX/calls" ;;
esac
# A task with its own \$SANDBOX/code.<task> (and patch.<task>) runs in its own pod, so two runs can go at once.
pod="$POD" code="$SANDBOX/code" patch="$SANDBOX/patch"
if [ -n "\${REMOTE_TASK:-}" ] && [ -e "$SANDBOX/code.\$REMOTE_TASK" ]; then
  pod="$POD.\$REMOTE_TASK" code="$SANDBOX/code.\$REMOTE_TASK" patch="$SANDBOX/patch.\$REMOTE_TASK"
fi
# Builtins where they do: this stub runs about ten times a run.
[ -d "\$pod" ] || mkdir -p "\$pod"
[ -e "$SANDBOX/nomain" ] || : >>"\$pod/main-stdout"
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
  *" --request-timeout=5s version "*)
    # The probe of \`remote-agent ready\`: with \$SANDBOX/unreachable, as kubectl when the cluster has no route.
    [ ! -e "$SANDBOX/unreachable" ] || { echo "Client Version: v1.31.0"; echo "Unable to connect to the server: dial tcp 10.0.0.1:6443: connect: no route to host" >&2; exit 1; } ;;
  *" get sandboxclaim "*conditions*) cat "$SANDBOX/conditions" 2>/dev/null || cat "$SANDBOX/state" ;;
  *" get sandboxclaim "*) printf 'test-pool-abc' ;;
  *" get sandbox "*) printf '' ;;
  *" remote-agent-sandbox"*)
    if [ -e "$SANDBOX/real" ]; then
      while [ "\$1" != "-c" ]; do shift; done
      [ ! -e "$SANDBOX/nocapped" ] || rm -f "\$pod/.agents/bin/capped"
      # A pod does not get this machine's CLAUDE_CONFIG_DIR.
      tee "$SANDBOX/stdin" | env -u CLAUDE_CONFIG_DIR HOME="\$pod" PATH="$SANDBOX/podbin:\$PATH" bash -c "\${2//\/proc\/1\/fd\/1/\$pod/main-stdout}" "\${@:3}"
      exit
    fi
    cat >"$SANDBOX/stdin"
    cp "\$pod/main-stdout" "$SANDBOX/log-at-run" 2>/dev/null || true
    if [ -e "$SANDBOX/vanish" ]; then cat "$SANDBOX/vanish" >&2; exit 1; fi
    # With $SANDBOX/events, Claude's raw events on stdout (the transcript); with $SANDBOX/summary, the report.
    [ ! -e "$SANDBOX/events" ] || cat "$SANDBOX/events"
    mkdir -p "\$pod/out"; cp "\$patch" "\$pod/out/changes.patch"
    if [ -e "$SANDBOX/summary" ]; then cp "$SANDBOX/summary" "\$pod/out/summary.md"; else echo "did the thing" >"\$pod/out/summary.md"; fi
    # With $SANDBOX/reportfile, the report file the agent wrote as ~/out/report.md.
    [ ! -e "$SANDBOX/reportfile" ] || cp "$SANDBOX/reportfile" "\$pod/out/report.md"
    # With $SANDBOX/evil, a hostile sandbox: links that aim the host's writes at $SANDBOX/victim, one that would have
    # it read the victim in as the session, and a forged run.json.
    if [ -e "$SANDBOX/evil" ]; then
      for l in run.json.tmp run.json.first progress.log stream.jsonl.scrubbed summary.md.scrubbed session.jsonl; do ln -s "$SANDBOX/victim" "\$pod/out/\$l"; done
      echo '{"type":"result"}' >"\$pod/out/stream.jsonl"
      echo '{"run":"forged","name":"forged","state":"done","exit_code":0,"pid":1}' >"\$pod/out/run.json"
    fi
    exit "\$(cat "\$code")" ;;
  *" exec "*)
    while [ "\$1" != "-c" ]; do shift; done
    c="\${2//--warning=no-unknown-keyword/}"
    HOME="\$pod" sh -c "\${c//\/proc\/1\/fd\/1/\$pod/main-stdout}" ;;
esac
EOF
chmod +x "$SANDBOX/bin/kubectl"
mkdir -p "$SANDBOX/podbin"
# It also starts a session as Claude does, under $HOME/.claude/projects/<munged cwd>, holding the token and cwd
# lines; not with $SANDBOX/nosession. It saves the shell prefix it was given to $SANDBOX/prefix, and its CAPPED_MB and
# CAPPED_SECS to $SANDBOX/capped-env. With $SANDBOX/killmsg,
# it appends that file to ~/out/capped.log, as the prefix does with capped's kill line. With $SANDBOX/quote, it prints
# that file's events, and stops there with exit 1 when one is a result. With $SANDBOX/loop, it runs a red test that loops (a minute: a broken cap fails, not hangs, this test) as
# Claude Code runs a Bash command under CLAUDE_CODE_SHELL_PREFIX: the prefix, then the whole command line as one
# argument, its output kept for the tool result, not on Claude's own stderr; the test's exit code goes to
# $SANDBOX/loop-rc.
cat >"$SANDBOX/podbin/claude" <<EOF
#!/usr/bin/env bash
[ "\$1" = -p ] || exit 0
printf '%s\n' "\$@" >"$SANDBOX/claude-args"
printf '%s' "\${CLAUDE_CODE_SHELL_PREFIX:-}" >"$SANDBOX/prefix"
printf '%s %s\n' "\${CAPPED_MB:-}" "\${CAPPED_SECS:-}" >"$SANDBOX/capped-env"
[ ! -e "$SANDBOX/killmsg" ] || cat "$SANDBOX/killmsg" >>"\$HOME/out/capped.log"
if [ -e "$SANDBOX/loop" ]; then
  "\$CLAUDE_CODE_SHELL_PREFIX" "shopt -u extglob 2>/dev/null || true && eval 'while [ \\\$SECONDS -lt 60 ]; do :; done' && pwd -P >| /dev/null" >/dev/null 2>&1
  echo "\$?" >"$SANDBOX/loop-rc"
fi
# With \$SANDBOX/limit, the account is over its usage limit: Claude says so, as an API error and an error result.
if [ -e "$SANDBOX/limit" ]; then
  echo '{"type":"assistant","error":"rate_limit","message":{"content":[{"type":"text","text":"You have hit your limit - resets 5:30pm (UTC)"}]}}'
  echo '{"type":"result","is_error":true,"total_cost_usd":0.42,"num_turns":3,"result":"You have hit your limit - resets 5:30pm (UTC)"}'
  exit 1
fi
if [ ! -e "$SANDBOX/nosession" ]; then
  id=0b1e5e55-1d00-4c1a-9a7e-5e551011d000
  echo "{\"type\":\"system\",\"subtype\":\"init\",\"session_id\":\"\$id\",\"cwd\":\"\$PWD\"}"
  d="\$HOME/.claude/projects/\$(printf '%s' "\$PWD" | sed 's/[^A-Za-z0-9]/-/g')"
  mkdir -p "\$d"
  printf '{"type":"user","cwd":"%s","sessionId":"%s","message":"env: CLAUDE_CODE_OAUTH_TOKEN=%s"}\n' "\$PWD" "\$id" "\$CLAUDE_CODE_OAUTH_TOKEN" >"\$d/\$id.jsonl"
  printf '{"type":"assistant","cwd":"%s","sessionId":"%s"}\n{"type":"last-prompt","sessionId":"%s"}\n' "\$PWD" "\$id" "\$id" >>"\$d/\$id.jsonl"
fi
if [ -e "$SANDBOX/quote" ]; then
  cat "$SANDBOX/quote"
  ! grep -q '"type":"result"' "$SANDBOX/quote" || exit 1
fi
EOF
cat >>"$SANDBOX/podbin/claude" <<'EOF'
echo "{\"type\":\"user\",\"text\":\"env: CLAUDE_CODE_OAUTH_TOKEN=$CLAUDE_CODE_OAUTH_TOKEN\"}"
echo '{"type":"assistant","message":{"content":[{"type":"text","text":"writing the hello file"},{"type":"tool_use","name":"Write","input":{"file_path":"hello.txt"}}]}}'
printf 'hello\n' >hello.txt
# The final report comes as an assistant message too, before the result, as Claude sends it.
echo '{"type":"assistant","message":{"content":[{"type":"text","text":"did the thing"}]}}'
echo '{"type":"result","result":"did the thing","is_error":false}'
EOF
chmod +x "$SANDBOX/podbin/claude"
remote_config "$SANDBOX/config" REMOTE_BACKEND=cluster REMOTE_CONTEXT=test-ctx REMOTE_POOL=test-pool

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
# progress.log holds stderr's lines without the tag, as a multiset: during the run two writers (the sandbox's
# progress and the first-event line) each write a line to stderr then to progress.log, so their lines may cross;
# the token is in neither file.
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
  [ "$(tr -d '\r' <"$k/progress.log" 2>/dev/null | LC_ALL=C sort)" \
    = "$(tr -d '\r' <"$SANDBOX/err" | sed "s/^\[${4:-$run}\] //" | LC_ALL=C sort)" ] \
    || fail "$1: progress.log is not stderr without the tag: $(diff <(tr -d '\r' <"$k/progress.log" 2>/dev/null \
      | LC_ALL=C sort) <(tr -d '\r' <"$SANDBOX/err" | sed "s/^\[${4:-$run}\] //" | LC_ALL=C sort) 2>&1)"
  grep -q "sk-claude-secret" "$k/run.json" "$k/progress.log" && fail "$1: the token is in run.json or progress.log"
  return 0
}
# Fails ($1 names the case) unless the last run's result.json is valid JSON matching jq condition $2, with no
# result.json.* left beside it and no token in it.
result_is() {
  local k
  k="$(kept)"
  jq -e "$2" "$k/result.json" >/dev/null 2>&1 || fail "$1: result.json does not hold $2: $(cat "$k/result.json" 2>/dev/null)"
  [ -z "$(find "$k" -name 'result.json.*')" ] || fail "$1: a temporary result.json left: $(find "$k" -name 'result.json.*')"
  grep -q "sk-claude-secret" "$k/result.json" 2>/dev/null && fail "$1: the token is in result.json"
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

# result.json: what the run came to, for a caller that lost stdout, stderr or the exit code. Its figures come from
# the transcript's last result event and the init event's model, the changed files from the patch, the report's
# status and follow-ups from the summary. The events and the summary carry the token, which never reaches it.
{
  echo '{"type":"system","subtype":"init","model":"claude-test-1","claude_code_version":"9.8.7","session_id":"s"}'
  echo '{"type":"result","is_error":false,"num_turns":1,"total_cost_usd":0.01,"usage":{"input_tokens":1}}'
  echo '{"type":"assistant","message":{"content":[{"type":"text","text":"key sk-claude-secret"}]}}'
  echo '{"type":"result","is_error":false,"duration_ms":4200,"num_turns":7,"total_cost_usd":1.25,"result":"sk-claude-secret","usage":{"input_tokens":10,"output_tokens":20,"cache_read_input_tokens":300}}'
} >"$SANDBOX/events"
printf 'status: done\nchanged: abc hello.txt\nfollow-up: document --flag\nfollow-up: token sk-claude-secret\n' >"$SANDBOX/summary"
run 0 "$patch" --name "08-result" || fail "result on exit 0: exited non-zero: $(cat "$SANDBOX/out")"
result_is "result on exit 0" '.exit_code == 0 and .status == "done" and .complete == true and .name == "08-result"
  and (.run | startswith("agent-")) and .num_turns == 7 and .total_cost_usd == 1.25 and .duration_ms == 4200
  and .usage == {"input_tokens":10,"output_tokens":20,"cache_read_input_tokens":300} and .model == "claude-test-1"
  and .claude_version == "9.8.7" and .changed == ["hello.txt"] and .report_status == "done"
  and .follow_up == ["document --flag", "token [redacted]"] and (.report_path | endswith("/summary.md"))'
rm -f "$W/hello.txt"

# A report file the agent wrote as ~/out/report.md: stdout and report_path give it, then the final message (the
# block); the status and follow-ups still come from the final message. The token is redacted in it too.
printf 'Built hello.txt.\nkey sk-claude-secret\n' >"$SANDBOX/reportfile"
printf 'status: done\nchanged: hello.txt\nwhy:\nfollow-up: document --flag\n' >"$SANDBOX/summary"
run 0 "$patch" --name "08-report-file" || fail "report file: exited non-zero: $(cat "$SANDBOX/out")"
want="$(printf 'Built hello.txt.\nkey [redacted]\n\nstatus: done\nchanged: hello.txt\nwhy:\nfollow-up: document --flag')"
[ "$(cat "$SANDBOX/report")" = "$want" ] || fail "report file: stdout is not the report then the block: $(cat "$SANDBOX/report")"
result_is "report file" '.report_status == "done" and .follow_up == ["document --flag"]
  and (.report_path | endswith("/report.md"))'
rf="$(jq -r .report_path "$(kept)/result.json" | tr -d '\r')"
[ "$(cat "$rf")" = "$want" ] || fail "report file: report_path does not hold the report then the block: $(cat "$rf")"
rm -f "$W/hello.txt" "$SANDBOX/reportfile"

# No changes: exit 3, claim still deleted.
code=0; run 0 "" || code=$?
[ "$code" = 3 ] || fail "no changes: wanted exit 3, got $code"
grep -q "delete sandboxclaim" "$SANDBOX/calls" || fail "no changes: claim not deleted"
run_data "no changes run data" "done" 3
result_is "result on exit 3" '.exit_code == 3 and .status == "done" and .complete == true and .changed == []
  and .caps == null'

# A patch that does not apply (the worktree changed meanwhile): exit 4, worktree untouched. The patch is kept as it
# came, so a file named after the token is redacted in result.json only.
stale='diff --git a/tracked.txt b/tracked.txt
--- a/tracked.txt
+++ b/tracked.txt
@@ -1 +1 @@
-not what the file says
+two
'
code=0; run 0 "$stale"'diff --git a/sk-claude-secret.txt b/sk-claude-secret.txt
new file mode 100644
--- /dev/null
+++ b/sk-claude-secret.txt
@@ -0,0 +1 @@
+x
' || code=$?
[ "$code" = 4 ] || fail "stale patch: wanted exit 4, got $code"
[ "$(cat "$W/tracked.txt")" = "one" ] || fail "stale patch: worktree changed"
[ ! -e "$W/sk-claude-secret.txt" ] || fail "stale patch: partly applied"
run_data "stale patch run data" failed 4
result_is "result on exit 4" '.exit_code == 4 and .status == "failed" and .complete == true
  and .changed == ["tracked.txt", "[redacted].txt"]'
rm -f "$SANDBOX/events" "$SANDBOX/summary"

# A failed run: non-zero, nothing applied, claim deleted. With no result event: every figure unknown, the time from
# run.json, and no report status.
code=0; run 1 "$patch" || code=$?
[ "$code" = 1 ] || fail "failed run: wanted exit 1, got $code"
[ ! -e "$W/hello.txt" ] || fail "failed run: patch applied anyway"
grep -q "delete sandboxclaim" "$SANDBOX/calls" || fail "failed run: claim not deleted"
run_data "failed run data" failed 1
result_is "result on exit 1" '.exit_code == 1 and .status == "failed" and .complete == false and .total_cost_usd == null
  and .num_turns == null and .usage == null and .model == null and .claude_version == null and .report_status == null and .follow_up == []
  and (.duration_ms | type) == "number"'
# A rename to a name of another length lists both its paths, and a non-ASCII name, which git C-quotes
# ("b/caf\303\251.txt"), is listed as it is on disk.
code=0; run 0 'diff --git a/tracked.txt b/tracked-renamed.txt
similarity index 100%
rename from tracked.txt
rename to tracked-renamed.txt
diff --git "a/caf\303\251.txt" "b/caf\303\251.txt"
new file mode 100644
index 0000000..ce01362
--- /dev/null
+++ "b/caf\303\251.txt"
@@ -0,0 +1 @@
+hello
' || code=$?
[ "$code" = 0 ] || fail "result of a rename: exited $code: $(cat "$SANDBOX/out")"
result_is "result of a rename and a non-ASCII name" '.changed == ["tracked.txt", "tracked-renamed.txt", "café.txt"]'
[ ! -f "$W/tracked-renamed.txt" ] || mv "$W/tracked-renamed.txt" "$W/tracked.txt"
rm -f "$W"/caf*.txt
# A report and a last result event each over 200 KB, more than one argument may hold, still give a result.
big="$(head -c 210000 /dev/zero | tr '\0' x)"
printf '{"type":"result","is_error":false,"num_turns":2,"result":"%s"}\n' "$big" >"$SANDBOX/events"
{ printf 'status: done\nfollow-up: read it all\n'; printf '%s\n' "$big"; } >"$SANDBOX/summary"
run 0 "$patch" --name 08-big || fail "result of a big report: exited non-zero: $(tail -c 2000 "$SANDBOX/err")"
result_is "result of a big report" '.complete == true and .num_turns == 2 and .report_status == "done"
  and .follow_up == ["read it all"] and .changed == ["hello.txt"]'
rm -f "$W/hello.txt" "$SANDBOX/events" "$SANDBOX/summary"
# A sandbox exiting 3, 4 or 5 is a failed run too (exit 1): those codes are remote-agent's own answers.
for c in 3 4 5; do
  code=0; run "$c" "$patch" || code=$?
  [ "$code" = 1 ] || fail "failed run exiting $c: wanted exit 1, got $code"
done

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
[ "$(sed 's/\r$//' "$t" 2>/dev/null | jq -r .type 2>/dev/null | tr -d '\r' | tr '\n' ' ')" = "system user assistant assistant result " ] \
  || fail "real sandbox: transcript does not hold Claude's events: $(cat "$t" 2>/dev/null)"
grep -q "first agent event after [0-9]*s" "$SANDBOX/err" || fail "real sandbox: no first-event time"
[ "$(cat "$SANDBOX/report")" = "did the thing" ] || fail "real sandbox: stdout is not just the summary: $(cat "$SANDBOX/report")"
# The report is printed once, on stdout: not also among the progress lines, which keep Claude's other text.
grep -q "did the thing" "$SANDBOX/err" && fail "real sandbox: the report is also on stderr: $(cat "$SANDBOX/err")"
grep -q "writing the hello file" "$SANDBOX/err" || fail "real sandbox: Claude's progress text not on stderr"
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

# The account's usage limit: exit 5, one line saying when it resets, nothing on stdout, nothing applied.
: >"$SANDBOX/real"
: >"$SANDBOX/limit"
code=0; run 0 "" || code=$?
[ "$code" = 5 ] || fail "usage limit: wanted exit 5, got $code: $(cat "$SANDBOX/out")"
if [ "$(grep -c "usage limit" "$SANDBOX/err")" != 1 ] || ! grep -q '^\[agent-[^]]*\] usage limit: resets 5:30pm (UTC)$' "$SANDBOX/err"; then
  fail "usage limit: not one line [<task>] usage limit: resets 5:30pm (UTC): $(cat "$SANDBOX/err")"
fi
[ ! -s "$SANDBOX/report" ] || fail "usage limit: something on stdout: $(cat "$SANDBOX/report")"
[ ! -e "$W/hello.txt" ] || fail "usage limit: patch applied"
run_data "usage limit run data" failed 5
result_is "usage limit result" '.exit_code == 5 and .status == "failed" and .complete == false and .total_cost_usd == 0.42
  and .num_turns == 3'
rm -f "$SANDBOX/real" "$SANDBOX/limit"

# Caps: the sandbox runs the agent's shell commands under scripts/capped, with remote.cap_mb and remote.cap_secs as
# arguments after the script. A red test that loops forever is killed at the time cap, the agent carries on, and
# the run ends done with its patch; the kill shows as one line on stderr and in result.json, apart from a lost sandbox.
# The sandbox runs on Linux. On Git Bash, capped is Windows Python, which refuses --mb (no process groups), so the
# probe fails and the run goes uncapped: the case is skipped there.
: >"$SANDBOX/real"
if [[ "$OSTYPE" == msys* || "$OSTYPE" == cygwin* ]]; then
  echo "remote-agent.test.sh: caps loop and prefix cases skipped on Git Bash (capped refuses --mb on Windows)"
else
  : >"$SANDBOX/loop"
  rm -f "$SANDBOX/loop-rc" "$SANDBOX/prefix"
  # Where this machine has no ps, capped reads the groups from /proc, so the caps still run (tests/capped.test.sh
  # covers both).
  t=$SECONDS
  code=0; REMOTE_CAP_SECS=1 run 0 "" --name caps-loop || code=$?
  [ "$code" = 0 ] || fail "caps loop: wanted exit 0, got $code: $(cat "$SANDBOX/out")"
  [ $((SECONDS - t)) -lt 60 ] || fail "caps loop: took $((SECONDS - t))s"
  [ "$(cat "$SANDBOX/loop-rc" 2>/dev/null)" = 124 ] || fail "caps loop: the looping test was not killed at the time cap: $(cat "$SANDBOX/loop-rc" 2>/dev/null)"
  grep -q "remote-agent-sandbox auto 1\$" "$SANDBOX/calls" || fail "caps loop: the caps are not the arguments after the script: $(grep remote-agent-sandbox "$SANDBOX/calls" | tail -c 200)"
  case "$(cat "$SANDBOX/prefix" 2>/dev/null)" in /*) ;; *) fail "caps loop: no absolute CLAUDE_CODE_SHELL_PREFIX: $(cat "$SANDBOX/prefix" 2>/dev/null)" ;; esac
  [ -f "$POD/.agents/bin/capped" ] || fail "caps loop: capped not sent with the setup"
  grep -q "^\[caps-loop\] == capped: shell prefix set" "$SANDBOX/err" || fail "caps loop: shell prefix not reported: $(cat "$SANDBOX/err")"
  grep -qxF "[caps-loop] killed: timeout while [ \$SECONDS -lt 60 ]; do :; done" "$SANDBOX/err" || fail "caps loop: no kill line: $(cat "$SANDBOX/err")"
  grep -q "capped: killed: timeout" "$POD/out/capped.log" 2>/dev/null || fail "caps loop: the kill not in ~/out/capped.log"
  [ -f "$(kept)/capped.log" ] || fail "caps loop: capped.log not kept"
  grep -q "^== capped: killed: timeout while" "$(kept)/progress.log" || fail "caps loop: the kill did not come live on the sandbox's stderr"
  [ "$(cat "$W/hello.txt" 2>/dev/null)" = "hello" ] || fail "caps loop: patch not applied"
  # shellcheck disable=SC2016 # jq, with a literal $SECONDS in the expected command
  result_is "caps loop result" '.status == "done" and .killed == "timeout" and .lost == false
    and .capped_kills == [{"kind": "timeout", "cmd": "while [ $SECONDS -lt 60 ]; do :; done"}]'
  # The prefix the run left: a Bash-tool command runs under capped, stdin closed; anything else (a hook, an MCP server)
  # runs as it is, with its stdin and arguments.
  pre="$POD/.agents/bin/capped-shell"
  [ "$(echo in | "$pre" "cat && pwd -P >| /dev/null" 2>/dev/null)" = "" ] || fail "prefix: a Bash-tool command read the caller's stdin"
  [ "$(echo in | "$pre" "cat" 2>/dev/null)" = in ] || fail "prefix: a hook command lost its stdin"
  [ "$(echo in | "$pre" printf '%s-%s' a 'b c' 2>/dev/null)" = "a-b c" ] || fail "prefix: several arguments not run as a command"
  rm -f "$W/hello.txt" "$SANDBOX/loop"
  # With no memory limit to read, the cap is 2000 MB; a test command's own capped gets the same caps.
  grep -q "^\[caps-loop\] == capped: shell prefix set (--mb 2000 --secs 1; sandbox limit: none MB)" "$SANDBOX/err" \
    || fail "caps loop: not the 2000 MB fallback: $(grep 'prefix set' "$SANDBOX/err")"
  [ "$(cat "$SANDBOX/capped-env")" = "2000 1" ] || fail "caps loop: CAPPED_MB and CAPPED_SECS not exported: $(cat "$SANDBOX/capped-env")"
  # The memory cap is the sandbox's share: its cgroup's limit (v2, else v1) less 1024 MB for Claude, half a limit under
  # 2048 MB; remote.cap_mb only lowers it.
  mkdir -p "$SANDBOX/cgroup/memory"
  for c in "v2 4294967296 - 3072 4096" "v2 4294967296 1000 1000 4096" "v2 4294967296 9000 3072 4096" \
    "v2 1073741824 - 512 1024" "v1 4294967296 - 3072 4096" "v2 max - 2000 none" "v1 9223372036854771712 - 2000 none"; do
    read -r v limit cm want shown <<<"$c"
    rm -f "$SANDBOX/cgroup/memory.max" "$SANDBOX/cgroup/memory/memory.limit_in_bytes"
    if [ "$v" = v2 ]; then echo "$limit" >"$SANDBOX/cgroup/memory.max"; else echo "$limit" >"$SANDBOX/cgroup/memory/memory.limit_in_bytes"; fi
    code=0
    if [ "$cm" = - ]; then run 0 "" --name share || code=$?; else REMOTE_CAP_MB="$cm" run 0 "" --name share || code=$?; fi
    [ "$code" = 0 ] || fail "share $c: wanted exit 0, got $code: $(cat "$SANDBOX/out")"
    rm -f "$W/hello.txt"
    grep -q "^\[share\] == capped: shell prefix set (--mb $want --secs 300; sandbox limit: $shown MB)" "$SANDBOX/err" \
      || fail "share $c: $(grep 'prefix set' "$SANDBOX/err")"
    [ "$(cat "$SANDBOX/capped-env")" = "$want 300" ] || fail "share $c: CAPPED_MB not the cap: $(cat "$SANDBOX/capped-env")"
    result_is "share $c result" ".caps == {\"mb\": $want, \"secs\": 300}"
  done
  rm -rf "$SANDBOX/cgroup"
fi
# Caps that are not whole numbers never reach the backend; the sandbox script refuses them too.
code=0; REMOTE_CAP_SECS=abc run 0 "" || code=$?
if [ "$code" = 0 ] || [ -e "$SANDBOX/calls" ]; then fail "bad cap: not refused before the backend: $code $(cat "$SANDBOX/err")"; fi
code=0; HOME="$SANDBOX/badcaps" bash "$REPO/scripts/remote-agent-sandbox.sh" 2000 5s </dev/null >/dev/null 2>"$SANDBOX/err" || code=$?
[ "$code" = 1 ] || fail "sandbox bad caps: wanted exit 1, got $code"
grep -qx "== bad caps: 2000 MB, 5s s" "$SANDBOX/err" || fail "sandbox bad caps: not said: $(cat "$SANDBOX/err")"

printf 'capped: killed: memory npm test\n' >"$SANDBOX/killmsg"
code=0; run 0 "" --name caps-mem || code=$?
[ "$code" = 0 ] || fail "caps memory: wanted exit 0, got $code: $(cat "$SANDBOX/out")"
if ! { [ "$(grep -c "killed: " "$SANDBOX/err")" = 2 ] && grep -qx "\[caps-mem\] killed: memory npm test" "$SANDBOX/err"; }; then
  fail "caps memory: not one line [caps-mem] killed: memory npm test: $(cat "$SANDBOX/err")"
fi
result_is "caps memory result" '.killed == "memory" and .lost == false and .capped_kills == [{"kind": "memory", "cmd": "npm test"}]'
rm -f "$W/hello.txt"
printf 'capped: killed: timeout\n' >"$SANDBOX/killmsg"
run 0 "" --name caps-time || fail "caps timeout: exited non-zero: $(cat "$SANDBOX/out")"
grep -qx "\[caps-time\] killed: timeout" "$SANDBOX/err" || fail "caps timeout: no kill line: $(cat "$SANDBOX/err")"
result_is "caps timeout result" '.killed == "timeout" and .lost == false and .capped_kills == [{"kind": "timeout", "cmd": null}]'
rm -f "$W/hello.txt" "$SANDBOX/killmsg"
# Claude's text quoting a kill line, tagged or not, counts no kill: the sandbox indents every line of it.
printf '%s\n' '{"type":"assistant","message":{"content":[{"type":"text","text":"the log said:\ncapped: killed: memory npm test\n== capped: killed: timeout make"}]}}' >"$SANDBOX/quote"
run 0 "" --name quote || fail "quoted kill: exited non-zero: $(cat "$SANDBOX/out")"
grep -q "^\[quote\]   capped: killed: memory npm test$" "$SANDBOX/err" || fail "quoted kill: Claude's text not shown indented: $(cat "$SANDBOX/err")"
grep -q "^\[quote\] killed: " "$SANDBOX/err" && fail "quoted kill: counted as a kill: $(cat "$SANDBOX/err")"
result_is "quoted kill result" '.killed == null and .capped_kills == [] and .lost == false'
rm -f "$W/hello.txt"
# Nor does Claude's text quoting a container error make a failed run lost.
{ echo '{"type":"assistant","message":{"content":[{"type":"text","text":"Error from server: No such container: test-pool-abc"}]}}'
  echo '{"type":"result","is_error":true,"result":"gave up"}'; } >"$SANDBOX/quote"
code=0; run 0 "" --name quote || code=$?
[ "$code" = 1 ] || fail "quoted container error: wanted exit 1, got $code: $(cat "$SANDBOX/out")"
result_is "quoted container error result" '.status == "failed" and .lost == false'
rm -f "$W/hello.txt" "$SANDBOX/quote"

# --agent runs the model the agent's Claude wrapper sets, as a local run of it does; without it, Claude's default.
want="$(sed -n 's/^model: *//p' "$REPO/adapters/claude/agents/epic-implementer.md" | head -1)"
[ -n "$want" ] || fail "agent model: the epic-implementer wrapper sets no model"
run 0 "" --name model --agent epic-implementer || fail "agent model: exited non-zero: $(cat "$SANDBOX/out")"
if ! grep -qx -- "--model" "$SANDBOX/claude-args" || ! grep -qxF -- "$want" "$SANDBOX/claude-args"; then
  fail "agent model: claude not run with --model $want: $(tr '\n' ' ' <"$SANDBOX/claude-args")"
fi
grep -q "remote-agent-sandbox auto 300 $want\$" "$SANDBOX/calls" || fail "agent model: not the argument after the caps: $(grep remote-agent-sandbox "$SANDBOX/calls" | tail -c 200)"
grep -qx "\[model\] == claude ($want)" "$SANDBOX/err" || fail "agent model: not shown: $(cat "$SANDBOX/err")"
rm -f "$W/hello.txt"
run 0 "" --name nomodel || fail "no agent: exited non-zero: $(cat "$SANDBOX/out")"
grep -qx -- "--model" "$SANDBOX/claude-args" && fail "no agent: claude run with a model: $(tr '\n' ' ' <"$SANDBOX/claude-args")"
rm -f "$W/hello.txt"
# --model runs that model, and wins over --agent's; a bad name is refused before any backend call.
run 0 "" --name model2 --agent epic-implementer --model my-model.1 || fail "--model: exited non-zero: $(cat "$SANDBOX/out")"
grep -qxF -- "my-model.1" "$SANDBOX/claude-args" || fail "--model: claude not run with it: $(tr '\n' ' ' <"$SANDBOX/claude-args")"
grep -qxF -- "$want" "$SANDBOX/claude-args" && fail "--model: --agent's model used too: $(tr '\n' ' ' <"$SANDBOX/claude-args")"
grep -q "remote-agent-sandbox auto 300 my-model.1\$" "$SANDBOX/calls" || fail "--model: not the argument after the caps"
rm -f "$W/hello.txt"
for bad in "-x" "a b" "a/b" ""; do
  code=0; run 0 "" --model "$bad" || code=$?
  [ "$code" = 1 ] || fail "--model '$bad': wanted exit 1, got $code: $(cat "$SANDBOX/out")"
  grep -q "^remote-agent: --model needs" "$SANDBOX/err" || fail "--model '$bad': no reason: $(cat "$SANDBOX/err")"
  [ ! -e "$SANDBOX/calls" ] || fail "--model '$bad': kubectl was called: $(cat "$SANDBOX/calls")"
done
# --home ships that home's setup in place of $HOME's, with the token still only on stdin; a missing folder is refused first.
run 0 "" --name home --home "$H2" || fail "--home: exited non-zero: $(cat "$SANDBOX/out")"
[ -f "$POD/.claude/skills/only-here/SKILL.md" ] || fail "--home: the given home's skill not in the sandbox"
[ ! -e "$POD/.claude/skills/skill-a" ] || fail "--home: a file of \$HOME reached the sandbox"
[ "$(sed -n 1p "$SANDBOX/stdin")" = "sk-claude-secret" ] || fail "--home: stdin line 1 is not the Claude token"
grep -rq --exclude-dir=projects "sk-claude-secret" "$POD/.claude" "$POD/.agents" && fail "--home: the token was shipped in the setup"
grep -q "sk-claude-secret" "$SANDBOX/calls" && fail "--home: the token leaked into kubectl arguments"
rm -f "$W/hello.txt"
code=0; run 0 "" --home "$SANDBOX/no-such-home" || code=$?
[ "$code" = 1 ] || fail "--home missing: wanted exit 1, got $code: $(cat "$SANDBOX/out")"
grep -q "^remote-agent: --home needs" "$SANDBOX/err" || fail "--home missing: no reason: $(cat "$SANDBOX/err")"
[ ! -e "$SANDBOX/calls" ] || fail "--home missing: kubectl was called: $(cat "$SANDBOX/calls")"
# The appended rules state facts about the sandbox only: how the agent ends is its brief's to say, as in a local run.
rules="$(grep -A1 -x -- "--append-system-prompt" "$SANDBOX/claude-args" | tail -1)"
case "$rules" in
  "You run unattended"*) ;;
  *) fail "rules: not passed: $(tr '\n' ' ' <"$SANDBOX/claude-args")" ;;
esac
case "$rules" in *"End with"* | *summary*) fail "rules: tell the agent how to end: $rules" ;; esac
rm -f "$W/hello.txt"
code=0; HOME="$SANDBOX/badmodel" bash "$REPO/scripts/remote-agent-sandbox.sh" 2000 5 '--x' </dev/null >/dev/null 2>"$SANDBOX/err" || code=$?
if [ "$code" != 1 ] || ! grep -qx "== bad model: --x" "$SANDBOX/err"; then
  fail "sandbox bad model: wanted exit 1 and the reason, got $code: $(cat "$SANDBOX/err")"
fi

# Without capped, or without a python3 to run it, the run goes on uncapped and says so, naming only what is missing
# (on Linux a missing ps is not: capped reads /proc).
: >"$SANDBOX/nocapped"
run 0 "" || fail "caps no capped: exited non-zero: $(cat "$SANDBOX/out")"
grep -q "== capped: unavailable (missing: capped)" "$SANDBOX/err" || fail "caps no capped: not reported: $(cat "$SANDBOX/err")"
[ ! -s "$SANDBOX/prefix" ] || fail "caps no capped: a shell prefix set anyway: $(cat "$SANDBOX/prefix")"
[ "$(cat "$W/hello.txt" 2>/dev/null)" = "hello" ] || fail "caps no capped: patch not applied"
result_is "caps no capped result" '.killed == null and .lost == false and .capped_kills == [] and .capped_off == "capped"'
rm -f "$W/hello.txt" "$SANDBOX/nocapped"
printf '#!/bin/sh\nexit 127\n' >"$SANDBOX/podbin/python3"
chmod +x "$SANDBOX/podbin/python3"
run 0 "" || fail "caps no python3: exited non-zero: $(cat "$SANDBOX/out")"
grep -q "== capped: unavailable (missing: python3)" "$SANDBOX/err" || fail "caps no python3: not reported: $(cat "$SANDBOX/err")"
[ ! -s "$SANDBOX/prefix" ] || fail "caps no python3: a shell prefix set anyway: $(cat "$SANDBOX/prefix")"
[ "$(cat "$W/hello.txt" 2>/dev/null)" = "hello" ] || fail "caps no python3: patch not applied"
result_is "caps no python3 result" '.capped_off == "python3" and .capped_kills == []'
rm -f "$W/hello.txt" "$SANDBOX/podbin/python3" "$SANDBOX/real"

# A sandbox that went away mid-run with no kill line before it is lost; one killed first reads as the kill.
echo "Error from server: error executing command in container: No such container: test-pool-abc" >"$SANDBOX/vanish"
code=0; run 0 "$patch" || code=$?
[ "$code" = 1 ] || fail "lost: wanted exit 1, got $code: $(cat "$SANDBOX/out")"
grep -q "killed: " "$SANDBOX/err" && fail "lost: a kill line printed: $(cat "$SANDBOX/err")"
result_is "lost result" '.lost == true and .killed == null and .capped_kills == [] and .status == "failed"'
{ echo "== capped: killed: memory make test"; echo "Error from server: No such container: test-pool-abc"; } >"$SANDBOX/vanish"
code=0; run 0 "$patch" || code=$?
[ "$code" = 1 ] || fail "killed then lost: wanted exit 1, got $code: $(cat "$SANDBOX/out")"
grep -q "^\[agent-[^]]*\] killed: memory make test$" "$SANDBOX/err" || fail "killed then lost: no kill line: $(cat "$SANDBOX/err")"
result_is "killed then lost result" '.lost == false and .killed == "memory"'
rm -f "$SANDBOX/vanish"

# --name labels the claim and its pod with the task and tags the progress; it is made a valid label value. run.json
# keeps the name though --attach comes after it.
mkdir -p "$SANDBOX/att"
printf 'ticket\n' >"$SANDBOX/att/ticket.md"
run 0 "$patch" --name "07-Visible runs!" --attach "$SANDBOX/att/ticket.md" || fail "name: exited non-zero: $(cat "$SANDBOX/out")"
claim="$(sed -n 's/^  name: //p' "$SANDBOX/claim.yaml" | head -1)"
[ "$(pod_labels)" = "sandbox.users.io/remote-agent-run: $claim, sandbox.users.io/remote-agent-task: 07-visible-runs" ] || fail "name: pod labels are $(pod_labels)"
grep -qF "sandbox.users.io/remote-agent-task: 07-visible-runs" "$SANDBOX/claim.yaml" || fail "name: claim not labelled"
grep -qx "    name: test-pool" "$SANDBOX/claim.yaml" || fail "name: claim not on the pool Setting's warm pool: $(cat "$SANDBOX/claim.yaml")"
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
remote_config "$SANDBOX/config" REMOTE_BACKEND=cluster REMOTE_CONTEXT=test-ctx REMOTE_POOL=test-pool
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
remote_config "$SANDBOX/config" REMOTE_BACKEND=cluster REMOTE_CONTEXT=test-ctx REMOTE_POOL=test-pool
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
remote_config "$SANDBOX/config" REMOTE_BACKEND=cluster REMOTE_CONTEXT=test-ctx REMOTE_POOL=test-pool
rm "$SANDBOX/config/dotagents/remote.env"
ready && fail "ready: yes with no token"
grep -q "CLAUDE_CODE_OAUTH_TOKEN" "$SANDBOX/out" || fail "ready, no token: $(cat "$SANDBOX/out")"
remote_config "$SANDBOX/config" REMOTE_BACKEND=cluster REMOTE_CONTEXT=test-ctx REMOTE_POOL=test-pool
# The backend is probed, briefly: an unreachable cluster is a no, with kubectl's own error on one line.
rm -f "$SANDBOX/calls"
ready || fail "ready: no though the cluster answers: $(cat "$SANDBOX/out")"
grep -q -- "--context test-ctx --request-timeout=5s version" "$SANDBOX/calls" || fail "ready: the cluster not probed: $(cat "$SANDBOX/calls" 2>/dev/null)"
: >"$SANDBOX/unreachable"
code=0; ready || code=$?
[ "$code" = 1 ] || fail "ready, unreachable: wanted exit 1, got $code"
[ "$(cat "$SANDBOX/out")" = "cluster backend not reachable: context test-ctx: Unable to connect to the server: dial tcp 10.0.0.1:6443: connect: no route to host" ] \
  || fail "ready, unreachable: not one line naming the backend and the error: $(cat "$SANDBOX/out")"
rm -f "$SANDBOX/unreachable" "$SANDBOX/calls"

# The cluster backend has no default kube context or warm pool: unset, ready and a run say which one, and nothing
# reaches kubectl.
for missing in CONTEXT POOL; do
  if [ "$missing" = CONTEXT ]; then remote_config "$SANDBOX/config" REMOTE_BACKEND=cluster REMOTE_POOL=test-pool
  else remote_config "$SANDBOX/config" REMOTE_BACKEND=cluster REMOTE_CONTEXT=test-ctx; fi
  code=0; ready || code=$?
  [ "$code" = 1 ] || fail "ready, no $missing: wanted exit 1, got $code"
  grep -q "REMOTE_${missing} not set" "$SANDBOX/out" || fail "ready, no $missing: not named: $(cat "$SANDBOX/out")"
  code=0; run 0 "$patch" || code=$?
  [ "$code" = 1 ] || fail "run, no $missing: wanted exit 1, got $code"
  grep -q "REMOTE_${missing} not set" "$SANDBOX/out" || fail "run, no $missing: not named: $(cat "$SANDBOX/out")"
  [ ! -e "$SANDBOX/calls" ] || fail "no $missing: kubectl was called: $(cat "$SANDBOX/calls")"
done
remote_config "$SANDBOX/config" REMOTE_BACKEND=cluster REMOTE_CONTEXT=test-ctx REMOTE_POOL=test-pool

# --help prints the usage on stdout and exits 0; an unknown --option is refused; neither starts a run.
code=0; (cd "$W" && env -u CLAUDE_CODE_OAUTH_TOKEN HOME="$H" PATH="$SANDBOX/bin:$PATH" XDG_CONFIG_HOME="$SANDBOX/config" \
  "$REPO/scripts/remote-agent.sh" --help) >"$SANDBOX/report" 2>"$SANDBOX/err" || code=$?
[ "$code" = 0 ] || fail "--help: wanted exit 0, got $code"
grep -q "^usage: remote-agent" "$SANDBOX/report" || fail "--help: usage not on stdout: $(cat "$SANDBOX/report")"
[ ! -e "$SANDBOX/calls" ] || fail "--help: kubectl was called: $(cat "$SANDBOX/calls")"
code=0; (cd "$W" && env -u CLAUDE_CODE_OAUTH_TOKEN HOME="$H" PATH="$SANDBOX/bin:$PATH" XDG_CONFIG_HOME="$SANDBOX/config" \
  "$REPO/scripts/remote-agent.sh" -h) >"$SANDBOX/report" 2>&1 || code=$?
if [ "$code" != 0 ] || ! grep -q "^usage: remote-agent" "$SANDBOX/report"; then fail "-h: not the usage with exit 0: $code $(cat "$SANDBOX/report")"; fi
code=0; (cd "$W" && env -u CLAUDE_CODE_OAUTH_TOKEN HOME="$H" PATH="$SANDBOX/bin:$PATH" XDG_CONFIG_HOME="$SANDBOX/config" \
  "$REPO/scripts/remote-agent.sh" --bogus "a brief") >"$SANDBOX/report" 2>"$SANDBOX/err" || code=$?
[ "$code" = 1 ] || fail "--bogus: wanted exit 1, got $code"
if ! grep -q "unknown option: --bogus" "$SANDBOX/err" || ! grep -q "^usage: remote-agent" "$SANDBOX/err"; then
  fail "--bogus: not refused with the usage on stderr: $(cat "$SANDBOX/err")"
fi
[ ! -e "$SANDBOX/calls" ] || fail "--bogus: kubectl was called: $(cat "$SANDBOX/calls")"
# --agent names a subagent with a Claude wrapper, refused before any backend call otherwise.
for bad in no-such-agent ../epic-implementer ""; do
  code=0; run 0 "" --agent "$bad" || code=$?
  [ "$code" = 1 ] || fail "--agent '$bad': wanted exit 1, got $code: $(cat "$SANDBOX/out")"
  grep -q "^remote-agent: \(no agent\|--agent needs\)" "$SANDBOX/err" || fail "--agent '$bad': no reason: $(cat "$SANDBOX/err")"
  [ ! -e "$SANDBOX/calls" ] || fail "--agent '$bad': kubectl was called: $(cat "$SANDBOX/calls")"
done

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
# The kill reaches the backend at once, but a slow runner (Git Bash on Windows) can take a moment to reap it.
for _ in $(seq 50); do alive "$(cat "$SANDBOX/backend.pid" 2>/dev/null)" || break; sleep 0.1; done
if alive "$(cat "$SANDBOX/backend.pid" 2>/dev/null)"; then
  fail "TERM: the backend's kubectl exec outlived remote-agent by 5s"
  kill "$(cat "$SANDBOX/backend.pid")"
fi
jq -e '.state == "failed" and .exit_code == 143 and (.ended | type) == "number"' "$(kept)/run.json" >/dev/null 2>&1 \
  || fail "TERM: run.json not failed with exit 143: $(cat "$(kept)/run.json" 2>/dev/null)"
result_is "TERM result" '.exit_code == 143 and .status == "failed" and .complete == false and .total_cost_usd == null'
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

# Refused before anything reaches the cluster, each naming why: an empty brief, one starting with "-", two
# attachments with one name, a snapshot over REMOTE_MAX_MB.
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
refused "starts with" "$REPO/scripts/remote-agent.sh" -- "--help"
refused "starts with" "$REPO/scripts/remote-agent.sh" "-p"
printf -- '--help\n' >"$SANDBOX/dash-brief"
refused "starts with" "$REPO/scripts/remote-agent.sh" "@$SANDBOX/dash-brief"
# -h, like --help, prints the usage and exits 0, starting no run.
rm -f "$SANDBOX/calls"
code=0; (cd "$W" && HOME="$H" PATH="$SANDBOX/bin:$PATH" XDG_CONFIG_HOME="$SANDBOX/config" \
  "$REPO/scripts/remote-agent.sh" -h) >"$SANDBOX/out" 2>&1 || code=$?
[ "$code" = 0 ] || fail "-h: wanted exit 0, got $code: $(cat "$SANDBOX/out")"
grep -q "^usage: remote-agent" "$SANDBOX/out" || fail "-h: no usage: $(cat "$SANDBOX/out")"
[ ! -e "$SANDBOX/calls" ] || fail "-h: kubectl was called: $(cat "$SANDBOX/calls")"
mkdir -p "$SANDBOX/a/notes"
refused "two attachments named notes" "$REPO/scripts/remote-agent.sh" --attach "$SANDBOX/run/notes" --attach "$SANDBOX/a/notes" "x"
# 2 MB of random bytes against the smallest limit the Setting allows (1 MB; 0 is refused as a bad value).
head -c 2097152 /dev/urandom >"$W/big.bin"
refused "over REMOTE_MAX_MB=1" env REMOTE_MAX_MB=1 "$REPO/scripts/remote-agent.sh" "x"
rm -f "$W/big.bin"

# Records live in the common git dir: a run in a linked worktree keeps its record there, so `git worktree remove`
# leaves it, and `resume` places its session for any worktree of the repo afterwards.
rm -f "$W/hello.txt"
: >"$SANDBOX/real"
LW="$SANDBOX/linked"
git -C "$W" worktree add -q --detach "$LW"
main_w="$W"
W="$LW"
run 0 "" || fail "linked worktree: exited non-zero: $(cat "$SANDBOX/out")"
W="$main_w"
rm -f "$SANDBOX/real"
claim="$(sed -n 's/^  name: //p' "$SANDBOX/claim.yaml" | head -1)"
git -C "$W" worktree remove --force "$LW"
k="$W/.git/remote-agent/$claim"
for f in transcript.jsonl run.json progress.log changes.patch summary.md session.jsonl; do
  [ -s "$k/$f" ] || fail "linked worktree: $f not kept in the common git dir after the worktree went: $(find "$W/.git" -path '*remote-agent*' 2>/dev/null)"
done
jq -e --arg run "$claim" '.run == $run and .state == "done"' "$k/run.json" >/dev/null 2>&1 \
  || fail "linked worktree: run.json not whole: $(cat "$k/run.json" 2>/dev/null)"
LW2="$SANDBOX/linked2"
git -C "$W" worktree add -q --detach "$LW2"
real_lw2="$(cd "$LW2" && pwd -P)"
if command -v cygpath >/dev/null 2>&1; then real_lw2="$(cygpath -w "$real_lw2")"; fi
lw2_projects="$CC/projects/$(printf '%s' "$real_lw2" | sed 's/[^A-Za-z0-9]/-/g')"
rm -rf "$CC"
resume "$claim" "$LW2" || fail "resume after the worktree went: exited non-zero: $(cat "$SANDBOX/err")"
[ -f "$lw2_projects/0b1e5e55-1d00-4c1a-9a7e-5e551011d000.jsonl" ] \
  || fail "resume after the worktree went: session not placed for $LW2: $(find "$CC" -type f 2>/dev/null)"
# A run kept before records moved, in the linked worktree's own git dir, still resumes.
old="$(git -C "$LW2" rev-parse --absolute-git-dir)/remote-agent/agent-old"
mkdir -p "$old"
printf '%s\n' '{"sessionId":"01d5e55-0002","cwd":"/sandbox/work"}' >"$old/session.jsonl"
rm -rf "$CC"
resume agent-old "$LW2" || fail "resume from the old location: exited non-zero: $(cat "$SANDBOX/err")"
[ -f "$lw2_projects/01d5e55-0002.jsonl" ] || fail "resume from the old location: session not placed: $(find "$CC" -type f 2>/dev/null) $(cat "$SANDBOX/err")"
git -C "$W" worktree remove --force "$LW2"

# Two runs whose first ids clash (a stub date and od give the same id twice): the second makes a new id, and both
# records are whole.
mkdir -p "$SANDBOX/idbin"
real_date="$(command -v date)"
real_od="$(command -v od)"
cat >"$SANDBOX/idbin/date" <<STUB
#!/usr/bin/env bash
if [ "\$*" = "+%Y%m%d-%H%M%S" ]; then echo 20260101-000000; else exec "$real_date" "\$@"; fi
STUB
cat >"$SANDBOX/idbin/od" <<STUB
#!/usr/bin/env bash
case "\$*" in
  *"/dev/urandom"*)
    n=\$(( \$(cat "$SANDBOX/od-calls" 2>/dev/null || echo 0) + 1 ))
    echo "\$n" >"$SANDBOX/od-calls"
    if [ "\$n" -le 2 ]; then echo "    7"; else echo "    8"; fi ;;
  *) exec "$real_od" "\$@" ;;
esac
STUB
chmod +x "$SANDBOX/idbin/date" "$SANDBOX/idbin/od"
clash_path="$PATH"
PATH="$SANDBOX/idbin:$PATH"
for i in 1 2; do
  rm -f "$W/hello.txt"
  run 0 "$patch" || fail "id clash: run $i exited non-zero: $(cat "$SANDBOX/out")"
done
PATH="$clash_path"
rm -f "$W/hello.txt"
for id in agent-20260101-000000-7 agent-20260101-000000-8; do
  k="$W/.git/remote-agent/$id"
  if [ ! -s "$k/changes.patch" ] || [ ! -f "$k/transcript.jsonl" ] || [ ! -s "$k/progress.log" ]; then
    fail "id clash: record $id not whole: $(ls "$k" 2>/dev/null)"
  fi
  jq -e --arg run "$id" '.run == $run and .state == "done" and .exit_code == 0' "$k/run.json" >/dev/null 2>&1 \
    || fail "id clash: run.json of $id not its own: $(cat "$k/run.json" 2>/dev/null)"
done
[ "$(grep -c "claimed agent-20260101-000000-7" "$W/.git/remote-agent/agent-20260101-000000-7/progress.log" 2>/dev/null)" = 1 ] \
  || fail "id clash: the second run wrote into the first run's record: $(cat "$W/.git/remote-agent/agent-20260101-000000-7/progress.log" 2>/dev/null)"

# `remote-agent result <name|run>` prints a run's result.json, so a caller that redirected stderr, or started several
# runs at once, still reads each run's result and exit code. Two runs at once, one with stderr in a file and one
# through `2>&1 | cat`, each in its own stub pod: each keeps its own progress.log and result.
# Runs `remote-agent result` in $W with the arguments given; stdout to $SANDBOX/result, stderr to $SANDBOX/err.
result() {
  (cd "$W" && env HOME="$H" "$REPO/scripts/remote-agent.sh" result "$@") >"$SANDBOX/result" 2>"$SANDBOX/err"
}
# Starts remote-agent.sh in $W as task $1 with the arguments after it; the caller redirects its output.
start() {
  local task="$1"
  shift
  (cd "$W" && env -u CLAUDE_CODE_OAUTH_TOKEN HOME="$H" PATH="$SANDBOX/bin:$PATH" XDG_CONFIG_HOME="$SANDBOX/config" \
    CLAUDE_CONFIG_DIR="$CC" "$REPO/scripts/remote-agent.sh" --name "$task" "$@" "add a hello file")
}
rm -f "$W/hello.txt"
echo 0 >"$SANDBOX/code.08-alone"; : >"$SANDBOX/patch.08-alone"
echo 1 >"$SANDBOX/code.08-piped"; : >"$SANDBOX/patch.08-piped"
start 08-alone >/dev/null 2>"$SANDBOX/err-alone" &
alone=$!
start 08-piped 2>&1 | cat >"$SANDBOX/err-piped" &
piped=$!
wait "$alone" || true
wait "$piped" || true
for t in 08-alone:3 08-piped:1; do
  task="${t%:*}" rc="${t#*:}"
  result "$task" || fail "result $task: exited non-zero: $(cat "$SANDBOX/err")"
  jq -e --arg n "$task" --argjson rc "$rc" '.name == $n and .exit_code == $rc' "$SANDBOX/result" >/dev/null 2>&1 \
    || fail "result $task: not its result with exit $rc: $(cat "$SANDBOX/result")"
  k="$W/.git/remote-agent/$(jq -r .run "$SANDBOX/result" 2>/dev/null | tr -d '\r')"
  # Its progress.log is its own stderr's lines, untagged, however that stderr was sent.
  [ "$(tr -d '\r' <"$k/progress.log" 2>/dev/null)" = "$(tr -d '\r' <"$SANDBOX/${task/08-/err-}" | sed -n "s/^\[$task\] //p")" ] \
    || fail "result $task: progress.log is not its own stderr: $(cat "$k/progress.log" 2>/dev/null)"
  # By its run id too.
  run_id="$(jq -r .run "$SANDBOX/result" | tr -d '\r')"
  result "$run_id" || fail "result $run_id: exited non-zero: $(cat "$SANDBOX/err")"
  jq -e --arg n "$task" '.name == $n' "$SANDBOX/result" >/dev/null 2>&1 || fail "result $run_id: not $task's result: $(cat "$SANDBOX/result")"
done
rm -f "$SANDBOX"/code.08-* "$SANDBOX"/patch.08-*
# A retry: the same name twice, the newest run's result printed, whatever order the folders sort in.
run 1 "" --name 08-retry || true
first="$(jq -r .run "$(kept)/run.json" | tr -d '\r')"
run 0 "" --name 08-retry || true
result 08-retry || fail "result of a retry: exited non-zero: $(cat "$SANDBOX/err")"
jq -e '.exit_code == 3' "$SANDBOX/result" >/dev/null 2>&1 || fail "result of a retry: not the newest run's: $(cat "$SANDBOX/result")"
# The first run made newer: its result wins, so the newest by start is what counts, not the folder name.
jq -c '.started += 3600' "$W/.git/remote-agent/$first/run.json" >"$SANDBOX/run.json" && mv "$SANDBOX/run.json" "$W/.git/remote-agent/$first/run.json"
result 08-retry || fail "result of a retry, first newer: exited non-zero: $(cat "$SANDBOX/err")"
jq -e --arg r "$first" '.exit_code == 1 and .run == $r' "$SANDBOX/result" >/dev/null 2>&1 \
  || fail "result of a retry, first newer: not the newest by start: $(cat "$SANDBOX/result")"
# A name matches as --name writes it.
result 08-Retry || fail "result 08-Retry: the name not matched as --name writes it: $(cat "$SANDBOX/err")"
# From outside the repo, given its dir; a run kept before records moved, in a linked worktree's own git dir, is found
# there by name.
old="$W/.git/worktrees/wt/remote-agent/agent-oldres"
mkdir -p "$old"
printf '{"run":"agent-oldres","name":"08-old","started":1}\n' >"$old/run.json"
printf '{"run":"agent-oldres","exit_code":0}\n' >"$old/result.json"
code=0; (cd "$SANDBOX" && env HOME="$H" "$REPO/scripts/remote-agent.sh" result 08-old "$W") >"$SANDBOX/result" 2>"$SANDBOX/err" || code=$?
[ "$code" = 0 ] || fail "result with a dir: exited $code: $(cat "$SANDBOX/err")"
jq -e '.run == "agent-oldres"' "$SANDBOX/result" >/dev/null 2>&1 \
  || fail "result with a dir: not the old location's run: $(cat "$SANDBOX/result")"
rm -rf "$W/.git/worktrees/wt"
code=0; result 08-retry "$W" extra || code=$?
[ "$code" = 1 ] || fail "result with three arguments: exited $code, not 1"
grep -q "usage: remote-agent result" "$SANDBOX/err" || fail "result with three arguments: no usage: $(cat "$SANDBOX/err")"
# Unknown names, and names that are no run name, exit non-zero with one line.
for q in 08-nothing ../x ""; do
  code=0; result "$q" || code=$?
  [ "$code" != 0 ] || fail "result '$q': exited 0"
  [ ! -s "$SANDBOX/result" ] || fail "result '$q': printed on stdout: $(cat "$SANDBOX/result")"
  [ "$(grep -c . "$SANDBOX/err")" = 1 ] || fail "result '$q': not one line: $(cat "$SANDBOX/err")"
done
code=0; result || code=$?
[ "$code" != 0 ] || fail "result with no argument: exited 0"
# A run still going has no result.json yet: say so rather than print an older run's.
mkdir -p "$W/.git/remote-agent/agent-going"
printf '{"run":"agent-going","name":"08-retry","started":%s,"state":"running"}\n' "$(( $(date +%s) + 7200 ))" \
  >"$W/.git/remote-agent/agent-going/run.json"
code=0; result 08-retry || code=$?
[ "$code" != 0 ] || fail "result of a run still going: exited 0 with $(cat "$SANDBOX/result")"
grep -q "agent-going has no result yet" "$SANDBOX/err" || fail "result of a run still going: does not name it: $(cat "$SANDBOX/err")"
rm -rf "$W/.git/remote-agent/agent-going"
# Every worktree of the repo shares the records: a run made from the caller's worktree wins over a newer one made
# from another under the same name, which counts only when none was made here.
W2="$SANDBOX/w2"
git -C "$W" worktree add -q --detach "$W2"
# Makes a finished run $1 named 08-shared, from worktree $2, started at $3.
shared() {
  mkdir -p "$W/.git/remote-agent/$1"
  jq -cn --arg r "$1" --arg w "$2" --argjson s "$3" '{run: $r, name: "08-shared", worktree: $w, started: $s,
    state: "done"}' >"$W/.git/remote-agent/$1/run.json"
  jq -cn --arg r "$1" '{run: $r, name: "08-shared", exit_code: 0}' >"$W/.git/remote-agent/$1/result.json"
}
now="$(date +%s)"
shared agent-shared-here "$(git -C "$W" rev-parse --show-toplevel)" "$now"
shared agent-shared-there "$(git -C "$W2" rev-parse --show-toplevel)" "$((now + 60))"
result 08-shared || fail "result in a shared repo: exited non-zero: $(cat "$SANDBOX/err")"
jq -e '.run == "agent-shared-here"' "$SANDBOX/result" >/dev/null 2>&1 \
  || fail "result in a shared repo: not this worktree's run: $(cat "$SANDBOX/result")"
(cd "$W2" && env HOME="$H" "$REPO/scripts/remote-agent.sh" result 08-shared) >"$SANDBOX/result" 2>"$SANDBOX/err" \
  || fail "result from the other worktree: exited non-zero: $(cat "$SANDBOX/err")"
jq -e '.run == "agent-shared-there"' "$SANDBOX/result" >/dev/null 2>&1 \
  || fail "result from the other worktree: not its run: $(cat "$SANDBOX/result")"
result 08-shared "$W2" || fail "result for the other worktree's dir: exited non-zero: $(cat "$SANDBOX/err")"
jq -e '.run == "agent-shared-there"' "$SANDBOX/result" >/dev/null 2>&1 \
  || fail "result for the other worktree's dir: not its run: $(cat "$SANDBOX/result")"
rm -rf "$W/.git/remote-agent/agent-shared-here"
result 08-shared || fail "result made only from another worktree: exited non-zero: $(cat "$SANDBOX/err")"
jq -e '.run == "agent-shared-there"' "$SANDBOX/result" >/dev/null 2>&1 \
  || fail "result made only from another worktree: not found: $(cat "$SANDBOX/result")"
rm -rf "$W/.git/remote-agent/agent-shared-there"
git -C "$W" worktree remove --force "$W2"
# A run just begun is found by its name from its first write of run.json: never the previous run's result. A stub ps
# holds the run between that write and the next while $SANDBOX/ps-hold is there.
run 0 "" --name 08-started || true
# Then it runs the real ps, or fails as a missing one would.
real_ps="$(command -v ps || true)"
cat >"$SANDBOX/bin/ps" <<EOF
#!/bin/sh
if [ -e "$SANDBOX/ps-hold" ]; then
  : >"$SANDBOX/ps-held"
  while [ -e "$SANDBOX/ps-hold" ]; do sleep 0.1; done
fi
[ -z "$real_ps" ] || exec "$real_ps" "\$@"
exit 127
EOF
chmod +x "$SANDBOX/bin/ps"
: >"$SANDBOX/ps-hold"
start 08-started >/dev/null 2>&1 &
begun=$!
i=0
while [ ! -e "$SANDBOX/ps-held" ] && [ "$i" -lt 300 ]; do sleep 0.1; i=$((i + 1)); done
code=0; result 08-started || code=$?
[ "$code" != 0 ] || fail "result of a run just begun: exited 0 with the previous run's $(cat "$SANDBOX/result")"
grep -q "has no result yet" "$SANDBOX/err" || fail "result of a run just begun: does not say so: $(cat "$SANDBOX/err")"
rm -f "$SANDBOX/ps-hold"
wait "$begun" || true
rm -f "$SANDBOX/bin/ps" "$SANDBOX/ps-held"
(cd "$W" && "$REPO/scripts/remote-agent.sh" --help) | grep -q "result <name|run>" || fail "--help does not list result"
grep -q "remote-agent result <name|run>" "$REPO/scripts/remote-agent.sh" || fail "the header comment does not list result"

# `remote-agent prune --older-than <Nd|Nh> [--dry-run] [dir]` removes the records older than the age, by run.json's
# ended, else started, else the folder's mtime, in the common git dir and the old per-worktree locations; never a
# live run. Records made by hand in a repo of their own.
P="$SANDBOX/prune"
git init -q "$P"
# As prune prints it, from git: physical on macOS (mktemp gives /var/..., a link to /private/var/...), C:/... in Git Bash.
P="$(git -C "$P" rev-parse --path-format=absolute --show-toplevel)"
now="$(date +%s)"
# Makes record folder $1 (under $P/.git) with run.json $2 (none when empty), its mtime in 2020.
rec() {
  mkdir -p "$P/.git/$1"
  [ -z "$2" ] || printf '%s\n' "$2" >"$P/.git/$1/run.json"
  printf 'x\n' >"$P/.git/$1/progress.log"
  touch -t 202001010000 "$P/.git/$1"
}
# A pid surely not alive: a finished child's.
sh -c 'exit 0' &
dead=$!
wait "$dead" || true
# A ps (as `ps -o lstart= -p <pid>`) that says this test's shell started at $me_start, $dead is not alive, and any
# other pid started at another time; $SANDBOX/psbin-none holds one with no -o lstart (Git Bash's), where only
# `kill -0` tells a live pid.
me_start="Mon Jan  5 10:00:00 2026"
mkdir -p "$SANDBOX/psbin" "$SANDBOX/psbin-none"
# shellcheck disable=SC2016 # $4 is for the stub ps, not this shell
printf '#!/bin/sh\ncase "$4" in %s) echo " %s" ;; %s) exit 1 ;; *) echo " Sat Jan  3 09:00:00 2026" ;; esac\n' \
  "$$" "$me_start" "$dead" >"$SANDBOX/psbin/ps"
printf '#!/bin/sh\necho "ps: unknown option -- o" >&2\nexit 1\n' >"$SANDBOX/psbin-none/ps"
chmod +x "$SANDBOX/psbin/ps" "$SANDBOX/psbin-none/ps"
psbin="$SANDBOX/psbin"
# Runs `remote-agent prune` in $P with the arguments given; stdout to $SANDBOX/prune.out, stderr to $SANDBOX/prune.err.
prune() {
  (cd "$P" && env HOME="$H" PATH="$psbin:$PATH" "$REPO/scripts/remote-agent.sh" prune "$@") >"$SANDBOX/prune.out" 2>"$SANDBOX/prune.err"
}
old=$((now - 3 * 86400))
make_records() {
  rm -rf "$P/.git/remote-agent" "$P/.git/worktrees"
  rec remote-agent/agent-done "{\"state\":\"done\",\"started\":$((old - 60)),\"ended\":$old,\"pid\":$dead}"
  rec remote-agent/agent-stopped "{\"state\":\"running\",\"started\":$old,\"ended\":null,\"pid\":$dead,\"pid_start\":\"x\"}"
  rec remote-agent/agent-reused "{\"state\":\"running\",\"started\":$old,\"ended\":null,\"pid\":$$,\"pid_start\":\"Thu Jan  1 00:00:00 1970\"}"
  rec remote-agent/agent-young "{\"state\":\"done\",\"started\":$old,\"ended\":$((now - 3600)),\"pid\":$dead}"
  rec remote-agent/agent-live "{\"state\":\"running\",\"started\":$old,\"ended\":null,\"pid\":$$,\"pid_start\":\"$me_start\"}"
  rec remote-agent/agent-nojson ""
  rec worktrees/wt/remote-agent/agent-oldplace "{\"state\":\"failed\",\"started\":$old,\"ended\":$old}"
  # Not a run name, and a link out of the folder: neither is followed nor removed.
  rec remote-agent/-x "{\"state\":\"done\",\"started\":$old,\"ended\":$old}"
  mkdir -p "$SANDBOX/outside/agent-out"
  printf '{"state":"done","started":%s,"ended":%s}\n' "$old" "$old" >"$SANDBOX/outside/agent-out/run.json"
  # Git Bash copies on a plain `ln -s`: ask for a native link, and skip the link cases where there can be none.
  haslink=1
  MSYS="${MSYS:+$MSYS }winsymlinks:nativestrict" ln -s "$SANDBOX/outside/agent-out" "$P/.git/remote-agent/agent-link" 2>/dev/null || haslink=
  MSYS="${MSYS:+$MSYS }winsymlinks:nativestrict" ln -s "$SANDBOX/outside" "$P/.git/remote-agent/agent-done/inside-link" 2>/dev/null || haslink=
  touch -t 202001010000 "$P/.git/remote-agent/agent-done"
}
gone="agent-done agent-stopped agent-reused agent-nojson"
kept="agent-young agent-live -x"
make_records
prune --older-than 1d --dry-run || fail "prune --dry-run: exited non-zero: $(cat "$SANDBOX/prune.err")"
for r in $gone $kept agent-oldplace; do
  [ -d "$P/.git/remote-agent/$r" ] || [ -d "$P/.git/worktrees/wt/remote-agent/$r" ] || fail "prune --dry-run: removed $r"
done
[ "$(grep -c '^would remove .*/' "$SANDBOX/prune.out")" = 5 ] || fail "prune --dry-run: not one line per run that would go: $(cat "$SANDBOX/prune.out")"
tail -1 "$SANDBOX/prune.out" | grep -q '^would remove 5 runs$' || fail "prune --dry-run: no count last: $(cat "$SANDBOX/prune.out")"
prune --older-than 1d || fail "prune: exited non-zero: $(cat "$SANDBOX/prune.err")"
for r in $gone; do
  [ ! -e "$P/.git/remote-agent/$r" ] || fail "prune: kept old $r"
  grep -qx "removed $P/.git/remote-agent/$r" "$SANDBOX/prune.out" || fail "prune: no line for $r: $(cat "$SANDBOX/prune.out")"
done
[ ! -e "$P/.git/worktrees/wt/remote-agent/agent-oldplace" ] || fail "prune: kept an old run in the old per-worktree location"
for r in $kept; do [ -d "$P/.git/remote-agent/$r" ] || fail "prune: removed $r"; done
[ "$(tail -1 "$SANDBOX/prune.out")" = "removed 5 runs" ] || fail "prune: no count last: $(cat "$SANDBOX/prune.out")"
grep -q -- '-x' "$SANDBOX/prune.err" || fail "prune: a folder that is no run name not named on stderr: $(cat "$SANDBOX/prune.err")"
if [ -n "$haslink" ] && { [ ! -L "$P/.git/remote-agent/agent-link" ] || [ ! -f "$SANDBOX/outside/agent-out/run.json" ]; }; then
  fail "prune: followed or removed a link out of the folder"
fi
# Hours: a run that ended an hour ago stays under 2h, and goes once it ended 3 hours ago.
prune --older-than 2h || fail "prune --older-than 2h: exited non-zero: $(cat "$SANDBOX/prune.err")"
[ -d "$P/.git/remote-agent/agent-young" ] || fail "prune --older-than 2h: removed a run that ended an hour ago"
[ "$(cat "$SANDBOX/prune.out")" = "removed 0 runs" ] || fail "prune with nothing to remove: $(cat "$SANDBOX/prune.out")"
jq -c ".ended = $((now - 3 * 3600))" "$P/.git/remote-agent/agent-young/run.json" >"$SANDBOX/run.json" \
  && mv "$SANDBOX/run.json" "$P/.git/remote-agent/agent-young/run.json"
prune --older-than 2h || fail "prune --older-than 2h: exited non-zero: $(cat "$SANDBOX/prune.err")"
[ ! -e "$P/.git/remote-agent/agent-young" ] || fail "prune --older-than 2h: kept a run that ended 3 hours ago"
[ -d "$P/.git/remote-agent/agent-live" ] || fail "prune --older-than 2h: removed the live run"
# Bad ages and arguments are refused with the usage, and nothing goes.
make_records
for a in "--older-than x" "--older-than 1" "--older-than 1w" "--older-than -1d" "--older-than" "" "--older-than 1d --what"; do
  code=0
  # shellcheck disable=SC2086 # each case is its words
  prune $a || code=$?
  [ "$code" = 1 ] || fail "prune $a: exited $code, not 1"
  grep -q 'usage: remote-agent prune' "$SANDBOX/prune.err" || fail "prune $a: no usage: $(cat "$SANDBOX/prune.err")"
  [ -d "$P/.git/remote-agent/agent-done" ] || fail "prune $a: removed a run"
done
# A huge age removes nothing: the number is capped, never overflowing into a negative age.
for a in 9999999999999999d 99999999999999999999h; do
  prune --older-than "$a" || fail "prune --older-than $a: exited non-zero: $(cat "$SANDBOX/prune.err")"
  [ "$(cat "$SANDBOX/prune.out")" = "removed 0 runs" ] || fail "prune --older-than $a: $(cat "$SANDBOX/prune.out")"
done
# Without a ps that prints start times, a pid that is alive keeps its run.
psbin="$SANDBOX/psbin-none"
prune --older-than 1d || fail "prune, no ps: exited non-zero: $(cat "$SANDBOX/prune.err")"
[ -d "$P/.git/remote-agent/agent-live" ] || fail "prune, no ps: removed the live run"
[ ! -e "$P/.git/remote-agent/agent-stopped" ] || fail "prune, no ps: kept the stopped run"
psbin="$SANDBOX/psbin"
# From outside the repo, given its dir; two dirs are refused with the usage, and a dir outside any repo by name.
make_records
code=0; (cd "$SANDBOX" && env HOME="$H" PATH="$psbin:$PATH" "$REPO/scripts/remote-agent.sh" prune --older-than 1d "$P") \
  >"$SANDBOX/prune.out" 2>"$SANDBOX/prune.err" || code=$?
[ "$code" = 0 ] || fail "prune with a dir: exited $code: $(cat "$SANDBOX/prune.err")"
for r in $gone; do [ ! -e "$P/.git/remote-agent/$r" ] || fail "prune with a dir: kept old $r"; done
for r in $kept; do [ -d "$P/.git/remote-agent/$r" ] || fail "prune with a dir: removed $r"; done
make_records
code=0; prune --older-than 1d "$P" "$P" || code=$?
[ "$code" = 1 ] || fail "prune with two dirs: exited $code, not 1"
grep -q 'usage: remote-agent prune' "$SANDBOX/prune.err" || fail "prune with two dirs: no usage: $(cat "$SANDBOX/prune.err")"
[ -d "$P/.git/remote-agent/agent-done" ] || fail "prune with two dirs: removed a run"
mkdir -p "$SANDBOX/nogit"
code=0; prune --older-than 1d "$SANDBOX/nogit" || code=$?
[ "$code" = 1 ] || fail "prune outside a repo: exited $code, not 1"
grep -q "not in a git worktree: $SANDBOX/nogit" "$SANDBOX/prune.err" || fail "prune outside a repo: not said: $(cat "$SANDBOX/prune.err")"
(cd "$W" && "$REPO/scripts/remote-agent.sh" --help) | grep -q "prune --older-than" || fail "--help does not list prune"
grep -q "remote-agent prune --older-than" "$REPO/scripts/remote-agent.sh" || fail "the header comment does not list prune"

# Without a token nothing reaches the cluster.
rm "$SANDBOX/config/dotagents/remote.env"
code=0; run 0 "$patch" || code=$?
if [ "$code" = 0 ] || [ -e "$SANDBOX/calls" ]; then fail "no token: ran anyway or called kubectl"; fi

[ "$fails" -eq 0 ] || { echo "remote-agent.test.sh: $fails failure(s)"; exit 1; }
echo "remote-agent.test.sh: ok"
