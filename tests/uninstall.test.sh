#!/usr/bin/env bash
# Runs install.sh then uninstall.sh against a throwaway $HOME and checks only dotagents' own changes are undone.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
fails=0
# Git Bash on Windows copies on `ln -s` unless told otherwise, as install.sh knows; the foreign link must be a link.
export MSYS="${MSYS:+$MSYS }winsymlinks:nativestrict"

fail() { echo "FAIL: $*"; fails=$((fails + 1)); }

install() { HOME="$1" DOTAGENTS_SKIP_PLUGINS=1 XDG_CONFIG_HOME="" "$REPO/install.sh" "${@:2}" >/dev/null; }
uninstall() { HOME="$1" DOTAGENTS_SKIP_PLUGINS=1 XDG_CONFIG_HOME="" "$REPO/uninstall.sh" "${@:2}"; }

# Every symlink under $1 that points into the repo or at a dotagents path.
ours() { find "$1" -type l -exec sh -c 'for l; do case "$(readlink "$l")" in "$0"/*|*/.agents/*) echo "$l";; esac; done' "$REPO" {} +; }

# Prints every file under $1 with its content or link target, so two states can be compared.
state() {
  local f
  find "$1" \( -type f -o -type l \) -not -path "*/.dotagents-backup/*" | sort | while read -r f; do
    if [ -L "$f" ]; then echo "$f -> $(readlink "$f")"; else echo "$f:"; cat "$f"; fi
  done
}

# A home with things that are not dotagents', next to which install.sh puts its own.
home="$SANDBOX/all"
mkdir -p "$home/.claude" "$home/.codex" "$home/.cursor" "$home/.agents/skills" "$home/.config/dotagents" "$SANDBOX/other"
echo "old instructions" >"$home/.claude/CLAUDE.md"
echo '{"model": "opus", "permissions": {"deny": ["Bash(rm:*)"]}}' >"$home/.claude/settings.json"
echo '{"version": 1, "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "orca"}]}]}}' >"$home/.cursor/hooks.json"
echo '{"mcpServers": {"mine": {"url": "https://example.com"}}}' >"$home/.cursor/mcp.json"
printf 'model = "o3"\n' >"$home/.codex/config.toml"
ln -s "$SANDBOX/other" "$home/.agents/skills/foreign"
echo "KEY=value" >"$home/.config/dotagents/mcp.env"
install "$home" all

# Dry run: prints the plan, changes nothing.
before="$(state "$home")"
out="$(uninstall "$home" --dry-run)"
[ "$before" = "$(state "$home")" ] || fail "--dry-run changed files"
grep -q "would remove $home/.claude/CLAUDE.md" <<<"$out" || fail "--dry-run did not list CLAUDE.md"

uninstall "$home" all >"$SANDBOX/all.out" || fail "uninstall all exited non-zero: $(cat "$SANDBOX/all.out")"

left="$(ours "$home")"
[ -z "$left" ] || fail "links left behind: $left"
[ ! -e "$home/.agents/hooks" ] || fail "$home/.agents/hooks left behind"
[ "$(cat "$home/.claude/CLAUDE.md")" = "old instructions" ] || fail "original CLAUDE.md not restored"
[ -L "$home/.agents/skills/foreign" ] || fail "foreign skill link removed"
jq -e '. == {"model": "opus", "permissions": {"deny": ["Bash(rm:*)"]}}' "$home/.claude/settings.json" >/dev/null ||
  fail "Claude settings not back to the user's own: $(cat "$home/.claude/settings.json")"
jq -e '. == {"version": 1, "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "orca"}]}]}}' "$home/.cursor/hooks.json" >/dev/null ||
  fail "Cursor hooks not back to the user's own: $(cat "$home/.cursor/hooks.json")"
jq -e '. == {"mcpServers": {"mine": {"url": "https://example.com"}}}' "$home/.cursor/mcp.json" >/dev/null ||
  fail "Cursor MCP servers not back to the user's own: $(cat "$home/.cursor/mcp.json")"
[ "$(cat "$home/.codex/config.toml")" = 'model = "o3"' ] || fail "Codex config not back to the user's own: $(cat "$home/.codex/config.toml")"
[ ! -e "$home/.codex/hooks.json" ] || ! jq -e '.. | strings | select(test("\\.agents/hooks/"))' "$home/.codex/hooks.json" >/dev/null ||
  fail "Codex hooks still call dotagents"
[ -e "$home/.config/dotagents/mcp.env" ] || fail "data deleted without --purge"
[ -d "$home/.dotagents-backup" ] || fail "backups deleted"

# Second run: nothing to do.
out="$(uninstall "$home" all)"
if grep -qE "removed|restored|deleted" <<<"$out"; then fail "second run changed something: $out"; fi

# --purge deletes dotagents' data but keeps the backups.
mkdir -p "$home/.agents/telemetry"
uninstall "$home" --purge >/dev/null
[ ! -e "$home/.config/dotagents" ] || fail "--purge kept ~/.config/dotagents"
[ ! -e "$home/.agents/telemetry" ] || fail "--purge kept ~/.agents/telemetry"
[ -d "$home/.dotagents-backup" ] || fail "--purge deleted backups"

# One tool: its links go, the shared ones stay for the others.
home="$SANDBOX/claude"
mkdir -p "$home/.claude"
install "$home" claude
uninstall "$home" claude >/dev/null
[ -z "$(ours "$home/.claude")" ] || fail "uninstall claude left Claude links"
[ -L "$home/.agents/hooks/lib.sh" ] || fail "uninstall claude removed the shared links"

# A home from before per-file hook links: its ~/.agents/hooks folder link is removed too, and nothing in the repo.
home="$SANDBOX/old"
mkdir -p "$home/.agents"
ln -s "$REPO/scripts/hooks" "$home/.agents/hooks"
uninstall "$home" all >/dev/null
[ ! -e "$home/.agents/hooks" ] && [ ! -L "$home/.agents/hooks" ] || fail "old ~/.agents/hooks folder link left behind"
[ -f "$REPO/scripts/hooks/lib.sh" ] || fail "uninstall removed a file in the repo through the old folder link"

# Unknown target exits non-zero.
if uninstall "$home" nope >/dev/null 2>&1; then fail "unknown target exited 0"; fi
out="$(uninstall "$home" nope 2>&1)" && rc=0 || rc=$?
{ [ "$rc" -eq 1 ] && grep -q "^usage:" <<<"$out"; } || fail "unknown word: want usage and exit 1, got $rc: $out"
out="$(uninstall "$home" --nope 2>&1)" && rc=0 || rc=$?
{ [ "$rc" -eq 1 ] && grep -q "^usage:" <<<"$out"; } || fail "unknown option: want usage and exit 1, got $rc: $out"

# Every link under $1 whose target is gone.
dangling() { find "$1" -type l ! -exec test -e {} \; -print; }

# Removing a group keeps the parts another installed group still requires.
home="$SANDBOX/pr-loop"
mkdir -p "$home/.claude" "$home/.codex" "$home/.cursor"
install "$home" pr-loop epics
uninstall "$home" pr-loop >"$SANDBOX/pr-loop.out" || fail "uninstall pr-loop exited non-zero: $(cat "$SANDBOX/pr-loop.out")"
for s in review-pr ship-pr fix-pr build-epic; do
  [ -L "$home/.agents/skills/$s" ] && [ -L "$home/.claude/skills/$s" ] || fail "uninstall pr-loop removed $s, which epics requires"
done
for a in pr-fixer pr-reviewer pr-reviewer-light; do
  [ -L "$home/.claude/agents/$a.md" ] && [ -L "$home/.codex/agents/$a.toml" ] && [ -L "$home/.cursor/agents/$a.md" ] ||
    fail "uninstall pr-loop removed agent $a, which epics requires"
done
for s in address-review renovate-prs; do
  [ ! -e "$home/.agents/skills/$s" ] && [ ! -L "$home/.claude/skills/$s" ] || fail "uninstall pr-loop kept $s"
done
[ -L "$home/.agents/skills/_shared" ] && [ -L "$home/.agents/hooks/lib.sh" ] || fail "uninstall pr-loop removed core"
[ -z "$(dangling "$home")" ] || fail "uninstall pr-loop left dangling links: $(dangling "$home")"
# A skill named alone takes its subagents with it.
uninstall "$home" to-epic >/dev/null
[ ! -L "$home/.agents/skills/to-epic" ] && [ ! -L "$home/.claude/agents/source-reader.md" ] && [ ! -L "$home/.codex/agents/source-reader.toml" ] ||
  fail "uninstall to-epic kept the skill or its source-reader agent"

# Removing the hooks group drops its links and config entries, and keeps remote, the other hooks and a foreign hook.
home="$SANDBOX/hooks"
mkdir -p "$home/.claude" "$home/.codex" "$home/.cursor"
echo '{"version": 1, "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "orca"}]}]}}' >"$home/.cursor/hooks.json"
echo '{"model": "opus"}' >"$home/.claude/settings.json"
install "$home" remote safety-hooks small-tools
before="$(state "$home")"
out="$(uninstall "$home" --dry-run safety-hooks)"
[ "$before" = "$(state "$home")" ] || fail "--dry-run safety-hooks changed files"
grep -q "would remove $home/.agents/hooks/guard-merge.sh" <<<"$out" || fail "--dry-run safety-hooks did not list guard-merge.sh: $out"
grep -q "would edit $home/.claude/settings.json" <<<"$out" || fail "--dry-run safety-hooks did not list settings.json: $out"
# One hook alone: the hooks beside it in the same arrays stay.
uninstall "$home" guard-merge >/dev/null
for f in "$home/.claude/settings.json" "$home/.codex/hooks.json" "$home/.cursor/hooks.json"; do
  { ! grep -q "guard-merge.sh" "$f" && grep -q "guard-secrets.sh" "$f"; } || fail "uninstall guard-merge: want guard-secrets kept and guard-merge gone in $f"
done
grep -q "allow-task-cleanup.sh" "$home/.claude/settings.json" || fail "uninstall guard-merge dropped allow-task-cleanup"
uninstall "$home" safety-hooks >"$SANDBOX/hooks.out" || fail "uninstall safety-hooks exited non-zero: $(cat "$SANDBOX/hooks.out")"
for h in guard-merge guard-secrets allow-task-cleanup; do
  [ ! -L "$home/.agents/hooks/$h.sh" ] || fail "uninstall safety-hooks kept the $h.sh link"
  for f in "$home/.claude/settings.json" "$home/.codex/hooks.json" "$home/.cursor/hooks.json"; do
    [ ! -e "$f" ] || ! grep -q "$h.sh" "$f" || fail "uninstall safety-hooks kept $h in $f"
  done
done
for l in .agents/bin/remote-agent .agents/skills/remote-subagents .claude/skills/remote-subagents .claude/skills/remote-runs \
  .agents/hooks/telemetry-save.sh .agents/hooks/lib.sh; do
  [ -L "$home/$l" ] || fail "uninstall safety-hooks removed $l"
done
jq -e '.model == "opus" and any(.hooks.SessionEnd[].hooks[]; .command | test("telemetry-save"))' "$home/.claude/settings.json" >/dev/null ||
  fail "uninstall safety-hooks dropped another hook or a setting: $(cat "$home/.claude/settings.json")"
jq -e '. == {"version": 1, "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "orca"}]}]}}' "$home/.cursor/hooks.json" >/dev/null ||
  fail "uninstall safety-hooks did not leave Cursor's foreign hook alone: $(cat "$home/.cursor/hooks.json")"
[ -z "$(dangling "$home")" ] || fail "uninstall safety-hooks left dangling links: $(dangling "$home")"

# A part another installed part requires stays, even when named.
uninstall "$home" telemetry >/dev/null
[ -L "$home/.agents/skills/telemetry" ] || fail "uninstall telemetry removed it while telemetry-save requires it"
# Removing remote leaves nothing dangling.
uninstall "$home" remote >/dev/null
[ ! -L "$home/.agents/bin/remote-agent" ] && [ ! -L "$home/.claude/skills/remote-runs" ] || fail "uninstall remote kept its links"
[ -z "$(dangling "$home")" ] || fail "uninstall remote left dangling links: $(dangling "$home")"

[ "$fails" -eq 0 ] || { echo "uninstall.test.sh: $fails failure(s)"; exit 1; }
echo "uninstall.test.sh: ok"
