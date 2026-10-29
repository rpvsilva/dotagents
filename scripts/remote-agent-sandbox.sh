#!/usr/bin/env bash
# The part of scripts/remote-agent.sh that runs inside the sandbox; remote-agent.sh passes it to `bash -c`.
# Expects the setup unpacked under $HOME (scripts/remote-agent-pack.sh) and the worktree snapshot in ~/work.
# Stdin: the Claude token (one line), then the brief.
# Installs the setup's plugins and MCP servers, commits the snapshot as a base, runs Claude on the brief with
# progress on stderr, and leaves ~/out/changes.patch (everything changed since the base) and ~/out/summary.md.
# The sandbox has no git remote and no GitHub credentials: results only leave as that patch.
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
# Progress: Claude's text and each tool call, one line each; the full stream is kept for the summary.
claude -p "$brief" --dangerously-skip-permissions --append-system-prompt "$rules" --output-format stream-json --verbose \
  | tee "$HOME/out/stream.jsonl" \
  | jq -r --unbuffered 'select(.type == "assistant") | .message.content[]
      | if .type == "text" then .text
        elif .type == "tool_use" then "  → \(.name) \(.input.command // .input.file_path // .input.pattern // .input.description // "" | tostring | gsub("\n"; " ") | .[0:120])"
        else empty end' >&2
jq -r 'select(.type == "result") | .result // ""' "$HOME/out/stream.jsonl" >"$HOME/out/summary.md"
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
