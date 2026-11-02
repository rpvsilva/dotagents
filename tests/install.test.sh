#!/usr/bin/env bash
# Runs install.sh against a throwaway $HOME and checks the links it leaves behind.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
fails=0

fail() { echo "FAIL: $*"; fails=$((fails + 1)); }

assert_link() {
  local dest="$1" want="$2" got
  if [ ! -L "$dest" ]; then fail "$dest is not a symlink"; return; fi
  got="$(readlink "$dest")"
  [ "$got" = "$want" ] || fail "$dest -> $got, want $want"
}

# Prints the target of every symlink under $1, so two runs can be compared: the sorted paths, then their targets in
# the same order, from one readlink for all (one each took seconds).
snapshot() {
  find "$1" -type l | sort >"$SANDBOX/links"
  cat "$SANDBOX/links"
  tr '\n' '\0' <"$SANDBOX/links" | xargs -0 readlink
}

run() { HOME="$1" DOTAGENTS_SKIP_PLUGINS=1 "$REPO/install.sh" "${@:2}"; }

skills=()
for d in "$REPO"/skills/*/; do skills+=("$(basename "$d")"); done

# all: a pre-existing real file is backed up, then everything is linked.
home="$SANDBOX/all"
mkdir -p "$home/.claude" "$home/.codex" "$home/.cursor"
echo "old instructions" >"$home/.claude/CLAUDE.md"
run "$home" all >/dev/null

for s in "${skills[@]}"; do
  assert_link "$home/.agents/skills/$s" "$REPO/skills/$s"
  assert_link "$home/.claude/skills/$s" "$home/.agents/skills/$s"
  [[ "$s" == _* ]] || [ -f "$home/.claude/skills/$s/SKILL.md" ] || fail "$s/SKILL.md not reachable through ~/.claude/skills"
done
# Claude mods (a folder with .claude-plugin/plugin.json) sit beside the skills: Claude Code loads each as a plugin.
mods=0
for m in "$REPO"/adapters/claude/mods/*/; do
  [ -f "$m.claude-plugin/plugin.json" ] || continue
  m="${m%/}"
  assert_link "$home/.claude/skills/${m##*/}" "$m"
  mods=$((mods + 1))
done
[ "$mods" -gt 0 ] || fail "no mod found under adapters/claude/mods"
assert_link "$home/.agents/hooks" "$REPO/scripts/hooks"
assert_link "$home/.agents/bin/remote-agent" "$REPO/scripts/remote-agent.sh"
assert_link "$home/.agents/bin/settings" "$REPO/scripts/settings.sh"
assert_link "$home/.claude/CLAUDE.md" "$REPO/global/AGENTS.md"
assert_link "$home/.codex/AGENTS.md" "$REPO/global/AGENTS.md"
for a in "$REPO"/adapters/claude/agents/*.md; do assert_link "$home/.claude/agents/$(basename "$a")" "$a"; done
for a in "$REPO"/adapters/codex/agents/*.toml; do assert_link "$home/.codex/agents/$(basename "$a")" "$a"; done
for r in "$REPO"/adapters/codex/rules/*.rules; do assert_link "$home/.codex/rules/$(basename "$r")" "$r"; done
for a in "$REPO"/adapters/cursor/agents/*.md; do assert_link "$home/.cursor/agents/$(basename "$a")" "$a"; done
grep -rqx "old instructions" "$home/.dotagents-backup" 2>/dev/null || fail "replaced CLAUDE.md was not backed up"

# Hook and settings snippets are merged into each tool's config, next to what is already there.
echo '{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "orca"}]}]}}' >"$home/.cursor/hooks.json"
run "$home" all >/dev/null
has_guard() {
  local g
  for g in guard-merge guard-secrets; do
    # Once per event: Cursor runs guard-secrets on two.
    jq -e --arg g "$g" '[.hooks[] | [.. | .command? | strings | select(test($g))] | length] | max == 1' "$1" >/dev/null || fail "$1: $g hook missing or doubled"
  done
}
has_guard "$home/.claude/settings.json"
has_guard "$home/.codex/hooks.json"
has_guard "$home/.cursor/hooks.json"
jq -e '.hooks.Stop[0].hooks[0].command == "orca"' "$home/.cursor/hooks.json" >/dev/null || fail "existing Cursor hook lost"
jq -e '.permissions.deny | index("Bash(gh auth token:*)") != null' "$home/.claude/settings.json" >/dev/null || fail "Claude deny rules not merged"

# MCP servers without variables reach Codex and Cursor even with no secrets file.
remote="$(jq -r '[.mcpServers | to_entries[] | select(tojson | test("\\$\\{") | not) | .key][0] // empty' "$REPO/mcp/servers.json")"
if [ -n "$remote" ]; then
  grep -qxF "[mcp_servers.\"$remote\"]" "$home/.codex/config.toml" || fail "MCP server $remote not in Codex config"
  jq -e --arg n "$remote" '.mcpServers[$n]' "$home/.cursor/mcp.json" >/dev/null || fail "MCP server $remote not in Cursor config"
fi

# Second run: same links, nothing backed up.
before="$(snapshot "$home")"
out="$(run "$home" all)"
[ "$before" = "$(snapshot "$home")" ] || fail "second run changed the links"
if grep -q "backed up" <<<"$out"; then fail "second run backed something up"; fi

# claude: no codex links; codex: skipped cleanly when ~/.codex is missing.
home="$SANDBOX/claude"
mkdir -p "$home"
run "$home" claude >/dev/null
assert_link "$home/.claude/CLAUDE.md" "$REPO/global/AGENTS.md"
assert_link "$home/.claude/skills/remote-runs" "$REPO/adapters/claude/mods/remote-runs"
[ ! -e "$home/.codex" ] || fail "install.sh claude touched ~/.codex"
run "$home" codex | grep -q "skipping" || fail "codex without ~/.codex did not skip"
run "$home" cursor | grep -q "skipping" || fail "cursor without ~/.cursor did not skip"

# Unknown target exits non-zero.
if run "$SANDBOX/bad" nope >/dev/null 2>&1; then fail "unknown target exited 0"; fi

[ "$fails" -eq 0 ] || { echo "install.test.sh: $fails failure(s)"; exit 1; }
echo "install.test.sh: ok"
