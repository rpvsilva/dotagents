#!/usr/bin/env bash
# The part of scripts/remote-agent.sh that runs inside the sandbox; remote-agent.sh passes it to `bash -c`.
# Expects the setup unpacked under $HOME (scripts/remote-agent-pack.sh) and the worktree snapshot in ~/work.
# Stdin: the Claude token (one line), then the brief.
# Installs the setup's plugins and MCP servers, commits the snapshot as a base, runs Claude on the brief with
# progress on stderr and its raw stream-json events on stdout, and leaves ~/out/changes.patch (everything changed
# since the base), ~/out/summary.md, ~/out/stream.jsonl and ~/out/session.jsonl (Claude's session, the token redacted).
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

echo "== claude" >&2
# Progress: Claude's text and each tool call, one line each; the full stream is kept for the summary, and goes out
# raw, as it comes, on the run's stdout (fd 3: see the end), which remote-agent.sh keeps as the live transcript.
# Each text is held until the next assistant or user event: the last one before a successful result is the final
# report, which remote-agent.sh prints on stdout from summary.md, so it is dropped here rather than shown twice on its
# stderr. Before an error result, or with no result at all, it is shown.
claude -p "$brief" --dangerously-skip-permissions --append-system-prompt "$rules" --output-format stream-json --verbose \
  | tee "$HOME/out/stream.jsonl" /dev/fd/3 \
  | jq -rn --unbuffered 'foreach ((inputs | objects), {type: "end"}) as $e ({held: null, out: []};
      if $e.type == "assistant" then
        reduce ($e.message.content[]? | objects) as $c (.out = [];
          if $c.type == "text" then .out += [.held | strings] | .held = $c.text
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
