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

# ~/.agents/hooks is a real folder of per-file links: lib.sh, secret-patterns.txt and each hook script.
assert_hooks() {
  local h="$1" f
  [ -d "$h/.agents/hooks" ] && [ ! -L "$h/.agents/hooks" ] || { fail "$h/.agents/hooks is not a real folder"; return; }
  for f in "$REPO"/scripts/hooks/*.sh "$REPO/scripts/hooks/secret-patterns.txt"; do
    assert_link "$h/.agents/hooks/${f##*/}" "$f"
  done
}

# The single-tool targets get homes of their own, installed in the background while the all target runs; the
# assertions on them come after `wait`. Each step's output lands in $SANDBOX/<step>.out.
(
  home="$SANDBOX/claude"
  mkdir -p "$home"
  run "$home" claude >"$SANDBOX/claude.out"
  [ ! -e "$home/.codex" ] || touch "$SANDBOX/claude-touched-codex"
  run "$home" codex >"$SANDBOX/codex.out"
  run "$home" cursor >"$SANDBOX/cursor.out"
  home="$SANDBOX/shared"
  mkdir -p "$home"
  run "$home" shared >"$SANDBOX/shared.out"
  # A home from before per-file hook links: ~/.agents/hooks is one link to the repo folder.
  home="$SANDBOX/migrate"
  mkdir -p "$home/.agents"
  ln -s "$REPO/scripts/hooks" "$home/.agents/hooks"
  run "$home" shared >"$SANDBOX/migrate.out"
  if run "$SANDBOX/bad" nope >/dev/null 2>&1; then touch "$SANDBOX/bad-exited-0"; fi
) &
single=$!

# all: a pre-existing real file is backed up, then everything is linked. Cursor's hooks.json already holds another
# tool's hook, which the merge keeps.
home="$SANDBOX/all"
mkdir -p "$home/.claude" "$home/.codex" "$home/.cursor"
echo "old instructions" >"$home/.claude/CLAUDE.md"
echo '{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "orca"}]}]}}' >"$home/.cursor/hooks.json"
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
assert_hooks "$home"
assert_link "$home/.agents/bin/remote-agent" "$REPO/scripts/remote-agent.sh"
assert_link "$home/.agents/bin/settings" "$REPO/scripts/settings.sh"
assert_link "$home/.agents/bin/capped" "$REPO/scripts/capped"
assert_link "$home/.claude/CLAUDE.md" "$REPO/global/AGENTS.md"
assert_link "$home/.codex/AGENTS.md" "$REPO/global/AGENTS.md"
for a in "$REPO"/adapters/claude/agents/*.md; do assert_link "$home/.claude/agents/$(basename "$a")" "$a"; done
for a in "$REPO"/adapters/codex/agents/*.toml; do assert_link "$home/.codex/agents/$(basename "$a")" "$a"; done
for r in "$REPO"/adapters/codex/rules/*.rules; do assert_link "$home/.codex/rules/$(basename "$r")" "$r"; done
for a in "$REPO"/adapters/cursor/agents/*.md; do assert_link "$home/.cursor/agents/$(basename "$a")" "$a"; done
# The guards source lib.sh (and through it secret-patterns.txt) beside themselves: the per-file links carry them.
code=0
"$home/.agents/hooks/guard-secrets.sh" 'cat .env' 2>/dev/null || code=$?
[ "$code" -eq 2 ] || fail "guard-secrets through ~/.agents/hooks: wanted exit 2 on cat .env, got $code"
"$home/.agents/hooks/guard-secrets.sh" 'cat src/app.ts' || fail "guard-secrets through ~/.agents/hooks blocked a plain file"
git init -q "$SANDBOX/gated" && touch "$SANDBOX/gated/.review-gate.yml"
code=0
(cd "$SANDBOX/gated" && "$home/.agents/hooks/guard-merge.sh" 'gh pr merge 1' 2>/dev/null) || code=$?
[ "$code" -eq 2 ] || fail "guard-merge through ~/.agents/hooks: wanted exit 2 on gh pr merge, got $code"
grep -rqx "old instructions" "$home/.dotagents-backup" 2>/dev/null || fail "replaced CLAUDE.md was not backed up"

# Second run, with no target (the default, all): same links, nothing backed up, every tool installed again.
before="$(snapshot "$home")"
out="$(run "$home")"
[ "$before" = "$(snapshot "$home")" ] || fail "second run changed the links"
if grep -q "backed up" <<<"$out"; then fail "second run backed something up"; fi
for t in shared claude codex cursor; do grep -qx "$t" <<<"$out" || fail "install.sh with no target did not install $t"; done

# Hook and settings snippets are merged into each tool's config, next to what is already there, and the second run
# doubles none of them.
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
jq -e '[.hooks.SessionEnd[]? | .. | .command? | strings | select(test("telemetry-save"))] | length == 1' "$home/.claude/settings.json" >/dev/null || fail "Claude SessionEnd telemetry-save hook missing or doubled"
jq -e '.hooks.Stop[0].hooks[0].command == "orca"' "$home/.cursor/hooks.json" >/dev/null || fail "existing Cursor hook lost"
jq -e '.permissions.deny | index("Bash(gh auth token:*)") != null' "$home/.claude/settings.json" >/dev/null || fail "Claude deny rules not merged"

# MCP servers without variables reach Codex and Cursor even with no secrets file.
remote="$(jq -r '[.mcpServers | to_entries[] | select(tojson | test("\\$\\{") | not) | .key][0] // empty' "$REPO/mcp/servers.json")"
if [ -n "$remote" ]; then
  grep -qxF "[mcp_servers.\"$remote\"]" "$home/.codex/config.toml" || fail "MCP server $remote not in Codex config"
  jq -e --arg n "$remote" '.mcpServers[$n]' "$home/.cursor/mcp.json" >/dev/null || fail "MCP server $remote not in Cursor config"
fi

wait "$single" || fail "a single-tool install failed"

# claude: no codex links; codex and cursor: skipped cleanly when their folder is missing.
home="$SANDBOX/claude"
assert_link "$home/.claude/CLAUDE.md" "$REPO/global/AGENTS.md"
assert_link "$home/.claude/skills/remote-runs" "$REPO/adapters/claude/mods/remote-runs"
[ ! -e "$SANDBOX/claude-touched-codex" ] || fail "install.sh claude touched ~/.codex"
grep -q "skipping" "$SANDBOX/codex.out" || fail "codex without ~/.codex did not skip"
grep -q "skipping" "$SANDBOX/cursor.out" || fail "cursor without ~/.cursor did not skip"

# shared: ~/.agents only, no tool's folder.
home="$SANDBOX/shared"
assert_link "$home/.agents/skills/${skills[0]}" "$REPO/skills/${skills[0]}"
assert_hooks "$home"
for d in .claude .codex .cursor; do [ ! -e "$home/$d" ] || fail "install.sh shared created ~/$d"; done

# migrate: the old folder link is replaced by per-file links, and nothing was linked or moved into the repo through it.
home="$SANDBOX/migrate"
assert_hooks "$home"
[ -z "$(find "$REPO/scripts/hooks" -type l)" ] || fail "install.sh linked into the repo through the old ~/.agents/hooks link"
if grep -q "backed up" "$SANDBOX/migrate.out"; then fail "replacing the old ~/.agents/hooks link backed something up"; fi

# Unknown target exits non-zero.
[ ! -e "$SANDBOX/bad-exited-0" ] || fail "unknown target exited 0"

[ "$fails" -eq 0 ] || { echo "install.test.sh: $fails failure(s)"; exit 1; }
echo "install.test.sh: ok"
