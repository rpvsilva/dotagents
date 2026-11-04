#!/usr/bin/env bash
# Feeds scripts/ui.sh plain install.sh and uninstall.sh output and checks the live view it draws from it.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
fails=0

fail() { echo "FAIL: $*"; fails=$((fails + 1)); }

# Runs ui.sh with args $@ on stdin, as the final screen: no colours, each redrawn line kept only as last drawn.
view() { HOME=/home/me NO_COLOR=1 TERM=dumb "$REPO/scripts/ui.sh" "$@" | tr '\r' '\n' | sed 's/\x1b\[[0-9;?]*[a-zA-Z]//g'; }

out="$(view install all <<'PLAIN'
shared
  /home/me/.agents/skills/a -> /repo/skills/a
  /home/me/.agents/skills/b -> /repo/skills/b
claude
  backed up /home/me/.claude/CLAUDE.md
  /home/me/.claude/CLAUDE.md -> /repo/global/AGENTS.md
  /home/me/.claude/settings.json: merged guard.json
  /home/me/.claude/settings.json: already has other.json
  plugin x@y: install failed
✘ Failed to install plugin "x@y": boom
codex: ~/.codex not found, skipping
cursor: ~/.cursor not found, skipping
some config snippets, plugins or MCP servers were not installed, see above
PLAIN
)"
grep -q "✓ shared  2 linked" <<<"$out" || fail "shared section not summed up: $out"
grep -q "✗ claude  1 linked · 1 updated · 1 backed up · 1 unchanged · 1 failed" <<<"$out" || fail "claude section not summed up: $out"
grep -q "↺ backed up ~/.claude/CLAUDE.md" <<<"$out" || fail "backup not shown with ~: $out"
grep -q "✗ plugin x@y: install failed" <<<"$out" || fail "failure not shown: $out"
grep -q "↳ Failed to install plugin \"x@y\": boom" <<<"$out" || fail "CLI error not kept under its tool: $out"
grep -q "○ codex   not found, skipped" <<<"$out" || fail "skipped tool not shown: $out"
grep -q "Finished with problems" <<<"$out" || fail "failure not in summary: $out"
grep -q "some config snippets" <<<"$out" || fail "closing line dropped: $out"

out="$(view uninstall --dry-run all <<'PLAIN'
claude
  would remove /home/me/.claude/CLAUDE.md
PLAIN
)"
grep -q "→ remove ~/.claude/CLAUDE.md" <<<"$out" || fail "dry run plan not shown: $out"
grep -q "Dry run, nothing changed" <<<"$out" || fail "dry run summary missing: $out"

out="$(view install bogus <<<"usage: ./install.sh [all|shared|claude|codex|cursor]")"
grep -q "Stopped" <<<"$out" || fail "usage error not shown as stopped: $out"
grep -q "usage:" <<<"$out" || fail "usage line dropped: $out"

# Off a terminal both scripts print the plain lines the other tests read.
out="$(HOME="$SANDBOX" DOTAGENTS_SKIP_PLUGINS=1 "$REPO/install.sh" shared)"
grep -qx shared <<<"$out" || fail "install.sh off a terminal did not print plain lines: $out"

[ "$fails" -eq 0 ] || exit 1
echo "ui: ok"
