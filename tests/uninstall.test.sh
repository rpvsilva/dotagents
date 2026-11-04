#!/usr/bin/env bash
# Runs install.sh then uninstall.sh against a throwaway $HOME and checks only dotagents' own changes are undone.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
fails=0

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
[ "$(cat "$home/.claude/CLAUDE.md")" = "old instructions" ] || fail "original CLAUDE.md not restored"
[ "$(readlink "$home/.agents/skills/foreign")" = "$SANDBOX/other" ] || fail "foreign skill link removed"
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
[ -L "$home/.agents/hooks" ] || fail "uninstall claude removed the shared links"

# Unknown target exits non-zero.
if uninstall "$home" nope >/dev/null 2>&1; then fail "unknown target exited 0"; fi

[ "$fails" -eq 0 ] || { echo "uninstall.test.sh: $fails failure(s)"; exit 1; }
echo "uninstall.test.sh: ok"
