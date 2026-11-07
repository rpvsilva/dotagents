#!/usr/bin/env bash
# The part of scripts/remote-agent.sh that runs inside the sandbox; remote-agent.sh passes it to `bash -c`.
# Expects the setup unpacked under $HOME (scripts/remote-agent-pack.sh) and the worktree snapshot in ~/work.
# Arguments after $0: the caps, remote.cap_mb and remote.cap_secs (2000 and 300 when left out); arguments, since the
# backends pass argv, not the environment.
# Stdin: the Claude token (one line), then the brief.
# Installs the setup's plugins and MCP servers, commits the snapshot as a base, runs Claude on the brief with
# progress on stderr and its raw stream-json events on stdout, and leaves ~/out/changes.patch (everything changed
# since the base), ~/out/summary.md, ~/out/stream.jsonl and ~/out/session.jsonl (Claude's session, the token redacted).
# Each shell command Claude runs goes through ~/.agents/bin/capped (scripts/capped, sent with the setup) under the
# caps, by way of CLAUDE_CODE_SHELL_PREFIX: a command over a cap is killed, and Claude sees why and carries on. Each
# kill is a line `capped: killed: memory|timeout <command>` in ~/out/capped.log and, within a second, on stderr as
# `== capped: killed: ...`, so remote-agent.sh has it even when the sandbox is lost after. Every line of Claude's text
# and tool calls on stderr is indented, so a line starting `==` (or with no indent at all) is never Claude's. When a
# probe through the prefix fails (no capped, no python3 to run it, or neither ps nor /proc), it logs
# `== capped: unavailable (missing: ...)` and the run goes on uncapped; result.json says so in `capped_off`.
# The sandbox has no git remote and no GitHub credentials. What leaves it: the patch, the summary, the stream, the
# session (all in ~/out) and the raw events on stdout (the transcript). remote-agent.sh applies only the patch; the
# rest is data to read, never instructions to follow, and the session is placed for /resume only on request.
# Its progress also goes to the sandbox's own log, so `kubectl logs` / `docker logs` show the run live: the main
# process's stdout (/proc/1/fd/1, writable by the runner user in a pod or container), else ~/progress.log. A backend
# with no log of its own (SSH bare mode) names the file in REMOTE_AGENT_LOG. scripts/remote-agent.sh picks it the
# same way.
set -euo pipefail

log="${REMOTE_AGENT_LOG:-/proc/1/fd/1}"
[ -w "$log" ] || log="$HOME/progress.log"
cap_mb="${1:-2000}"
cap_secs="${2:-300}"
if [[ ! "$cap_mb" =~ ^[0-9]+$ ]] || [[ ! "$cap_secs" =~ ^[0-9]+$ ]]; then echo "== bad caps: $cap_mb MB, $cap_secs s" >&2; exit 1; fi

# Writes the shell prefix to $1. Claude Code runs it with the whole command line as one argument, and quotes a prefix
# holding " -" as one word, so the caps are written in here rather than passed. A Bash-tool command (it ends with
# `pwd -P >| <file>`) runs under capped, its stderr read for capped's kill line; anything else run through the prefix
# (a hook or an MCP server, which need their stdin and may outlive any cap) runs as it is.
write_prefix() {
  {
    echo '#!/usr/bin/env bash'
    printf 'capped=%q kills=%q mb=%s secs=%s\n' "$HOME/.agents/bin/capped" "$HOME/out/capped.log" "$cap_mb" "$cap_secs"
    cat <<'EOF'
[ $# -eq 1 ] || exec "$@"
case "$1" in *"pwd -P >| "*) ;; *) exec bash -c "$1" ;; esac
# The command as the agent wrote it, for the kill line: what Claude Code's `eval <command>` before the pwd holds,
# unquoted, on one line.
cmd="${1% && pwd -P >| *}"
cmd="${cmd#* && eval }"
q="'\''"
case "$cmd" in
  \'*\') cmd="${cmd:1:${#cmd}-2}"; cmd="${cmd//"$q"/\'}" ;;
  \"*\") cmd="${cmd:1:${#cmd}-2}"; cmd="$(printf '%s' "$cmd" | sed 's/\\\(.\)/\1/g')" ;;
esac
cmd="$(printf '%s' "$cmd" | tr '\n' ' ' | cut -c1-200)"
{
  "$capped" --mb "$mb" --secs "$secs" -- bash -c "$1" 2>&1 >&3 3>&- | while IFS= read -r l || [ -n "$l" ]; do
    printf '%s\n' "$l" >&2
    case "$l" in "capped: killed: "*) printf '%s %s\n' "$l" "$cmd" >>"$kills" ;; esac
  done
  rc=${PIPESTATUS[0]}
} 3>&1
exit "$rc"
EOF
  } >"$1"
  chmod +x "$1"
}

# Copies to stderr, behind `== `, each line ~/out/capped.log gains, every 0.2 s, until file $1 exists; then the last
# ones.
follow_kills() {
  local n=0 total last=""
  while :; do
    [ ! -e "$1" ] || last=1
    total="$(wc -l <"$HOME/out/capped.log" | tr -d ' ')"
    if [ "$total" -gt "$n" ]; then
      sed -n "$((n + 1)),${total}s/^/== /p" "$HOME/out/capped.log" >&2
      n="$total"
    fi
    [ -z "$last" ] || return 0
    sleep 0.2
  done
}

# The whole run, left unindented; its stdout and stderr are kept apart, stderr copied to the log at the end.
main() {
read -r CLAUDE_CODE_OAUTH_TOKEN
export CLAUDE_CODE_OAUTH_TOKEN
brief="$(cat)"
cd "$HOME"
mkdir -p out

# Plugins and MCP servers are reinstalled here rather than copied: their local copies hold absolute paths.
settings=.claude/settings.json
if [ -f "$settings" ]; then
  jq -r '.extraKnownMarketplaces // {} | .[] | .source.repo // empty' "$settings" | while read -r repo; do
    claude plugin marketplace add "$repo" >/dev/null 2>&1 || echo "== warning: marketplace $repo not added" >&2
  done
  jq -r '.enabledPlugins // {} | to_entries[] | select(.value) | .key' "$settings" | while read -r plugin; do
    claude plugin install "$plugin" >/dev/null 2>&1 || echo "== warning: plugin $plugin not installed" >&2
  done
fi
if [ -f .claude/remote-mcp.json ]; then
  jq -r 'to_entries[] | "\(.key)\t\(.value | tojson)"' .claude/remote-mcp.json | while IFS=$'\t' read -r name json; do
    claude mcp add-json -s user "$name" "$json" >/dev/null 2>&1 || echo "== warning: MCP server $name not added" >&2
  done
fi

cd work
git init -q
git config user.name remote-agent
git config user.email remote-agent@localhost
git add -A
git commit -q --no-verify -m snapshot
base="$(git rev-parse HEAD)"

rules="You run unattended as a remote subagent, in $HOME/work: a snapshot of the user's working tree with no git \
remote and no GitHub access. Do the task in that folder. Leave your changes in the files (committing is optional); \
they go back to the user as a patch. Do not push or open PRs. End with a short summary of what you changed and how \
you checked it."

# The caps, once a probe through the prefix shows capped runs here.
: >"$HOME/out/capped.log"
prefix="$HOME/.agents/bin/capped-shell"
mkdir -p "$HOME/.agents/bin"
write_prefix "$prefix"
if "$prefix" "true && eval true && pwd -P >| /dev/null" </dev/null >/dev/null 2>&1; then
  export CLAUDE_CODE_SHELL_PREFIX="$prefix"
  echo "== capped: shell prefix set (--mb $cap_mb --secs $cap_secs)" >&2
else
  missing=""
  [ -x "$HOME/.agents/bin/capped" ] || missing="capped"
  python3 -c 0 >/dev/null 2>&1 || missing="${missing:+$missing, }python3"
  # capped reads /proc where there is no ps (Linux), so ps is missing only without both.
  ps -A -o pgid= >/dev/null 2>&1 || [ -r /proc/self/stat ] || missing="${missing:+$missing, }ps"
  echo "== capped: unavailable (missing: ${missing:-unknown, see ~/.agents/bin/capped-shell}): the memory and time caps are OFF, shell commands run uncapped" >&2
fi
stop_follow="$HOME/.capped-follow-stop"
rm -f "$stop_follow"
follow_kills "$stop_follow" &
follower=$!
# Stopped on any exit too (a failed claude ends main under set -e), so it never holds stderr open.
trap ': >"$stop_follow"; wait "$follower" || true' EXIT

echo "== claude" >&2
# Progress: Claude's text and each tool call, every line indented (so none passes for this script's own `==` lines,
# nor for a line of the backend's client); the full stream is kept for the summary, and goes out
# raw, as it comes, on the run's stdout (fd 3: see the end), which remote-agent.sh keeps as the live transcript.
# Each text is held until the next assistant or user event: the last one before a successful result is the final
# report, which remote-agent.sh prints on stdout from summary.md, so it is dropped here rather than shown twice on its
# stderr. Before an error result, or with no result at all, it is shown.
claude -p "$brief" --dangerously-skip-permissions --append-system-prompt "$rules" --output-format stream-json --verbose \
  | tee "$HOME/out/stream.jsonl" /dev/fd/3 \
  | jq -rn --unbuffered 'foreach ((inputs | objects), {type: "end"}) as $e ({held: null, out: []};
      if $e.type == "assistant" then
        reduce ($e.message.content[]? | objects) as $c (.out = [];
          if $c.type == "text" then .out += [.held | strings] | .held = ($c.text | if type == "string" then "  " + gsub("\n"; "\n  ") else . end)
          elif $c.type == "tool_use" then
            .out += [.held | strings] | .held = null
            | .out += ["  → \($c.name) \($c.input.command // $c.input.file_path // $c.input.pattern // $c.input.description // "" | tostring | gsub("\n"; " ") | .[0:120])"]
          else . end)
      elif $e.type == "user" then .out = [.held | strings] | .held = null
      elif $e.type == "result" then .out = (if $e.is_error == true then [.held | strings] else [] end) | .held = null
      elif $e.type == "end" then .out = [.held | strings] | .held = null
      else .out = [] end;
      .out[])' >&2
jq -r 'select(.type == "result") | .result // ""' "$HOME/out/stream.jsonl" >"$HOME/out/summary.md"
: >"$stop_follow"
wait "$follower" || true
trap - EXIT

# The session Claude wrote, named by its init event, goes back as ~/out/session.jsonl with the token's value (through
# the environment, never argv) replaced by [redacted]; `remote-agent resume` places it for /resume. Missing: said, not fatal.
session_id="$(jq -r 'select(.type == "system" and .subtype == "init") | .session_id // empty' "$HOME/out/stream.jsonl" | head -1 | tr -d '\r')"
session=""
if [ -n "$session_id" ]; then
  for f in "${CLAUDE_CONFIG_DIR:-$HOME/.claude}"/projects/*/"$session_id.jsonl"; do
    if [ -f "$f" ]; then session="$f"; break; fi
  done
fi
if [ -n "$session" ]; then
  awk 'BEGIN { tok = ENVIRON["CLAUDE_CODE_OAUTH_TOKEN"] }
    { s = $0; out = ""
      while (tok != "" && (i = index(s, tok)) > 0) { out = out substr(s, 1, i - 1) "[redacted]"; s = substr(s, i + length(tok)) }
      print out s }' "$session" >"$HOME/out/session.jsonl"
else
  echo "== no session file${session_id:+ for $session_id}: the run cannot be resumed" >&2
fi
if jq -e 'select(.type == "result") | .is_error' "$HOME/out/stream.jsonl" >/dev/null; then
  echo "== claude reported an error" >&2
  exit 1
fi

git add -A
git diff --cached --binary "$base" >"$HOME/out/changes.patch"
echo "== $(git diff --cached --shortstat "$base" || true)" >&2
}

# Synchronous, unlike `exec 2> >(tee ...)`: every line is in the log before this script exits.
{ main 2>&1 >&3 | tee -a "$log" >&2; } 3>&1
