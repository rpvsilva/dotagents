#!/usr/bin/env bash
# Runs scripts/merge-json.sh on throwaway files and checks what it merges, keeps and backs up.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MERGE="$REPO/scripts/merge-json.sh"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
fails=0

fail() { echo "FAIL: $*"; fails=$((fails + 1)); }

# Passes when jq filter $2 is true on file $1.
check() { jq -e "$2" "$1" >/dev/null || fail "$3"; }

cat >"$SANDBOX/snippet.json" <<'EOF'
{ "version": 1, "hooks": { "PreToolUse": [{ "matcher": "Bash", "hooks": [{ "type": "command", "command": "guard" }] }] },
  "permissions": { "deny": ["Bash(gh pr review:*)"] } }
EOF

# Missing target: created from the snippet, nothing backed up.
out="$("$MERGE" "$SANDBOX/snippet.json" "$SANDBOX/new/settings.json" "$SANDBOX/backup1")"
check "$SANDBOX/new/settings.json" '.hooks.PreToolUse[0].hooks[0].command == "guard"' "missing target: snippet not written"
[ ! -e "$SANDBOX/backup1" ] || fail "missing target: something was backed up"
grep -q merged <<<"$out" || fail "missing target: did not say merged"

# Existing target: other keys, other hooks and existing scalars survive; the original is backed up.
cat >"$SANDBOX/settings.json" <<'EOF'
{ "theme": "dark", "version": 2,
  "hooks": { "Stop": [{ "hooks": [{ "type": "command", "command": "orca" }] }],
             "PreToolUse": [{ "matcher": "*", "hooks": [{ "type": "command", "command": "orca" }] }] },
  "permissions": { "deny": ["Bash(rm:*)"], "allow": ["Bash(ls:*)"] } }
EOF
cp "$SANDBOX/settings.json" "$SANDBOX/original.json"
"$MERGE" "$SANDBOX/snippet.json" "$SANDBOX/settings.json" "$SANDBOX/backup2" >/dev/null
s="$SANDBOX/settings.json"
check "$s" '.theme == "dark"' "existing: unrelated key lost"
check "$s" '.version == 2' "existing: scalar overwritten by the snippet"
check "$s" '.hooks.Stop[0].hooks[0].command == "orca"' "existing: other event lost"
check "$s" '[.hooks.PreToolUse[].hooks[0].command] == ["orca", "guard"]' "existing: hook not appended after the existing one"
check "$s" '.permissions.deny == ["Bash(rm:*)", "Bash(gh pr review:*)"]' "existing: deny list not merged"
check "$s" '.permissions.allow == ["Bash(ls:*)"]' "existing: allow list changed"
backup="$(find "$SANDBOX/backup2" -type f)"
if [ -z "$backup" ] || ! cmp -s "$backup" "$SANDBOX/original.json"; then fail "existing: original not backed up"; fi

# Second run: file untouched, nothing backed up.
cp "$s" "$SANDBOX/after-first.json"
out="$("$MERGE" "$SANDBOX/snippet.json" "$s" "$SANDBOX/backup3")"
cmp -s "$s" "$SANDBOX/after-first.json" || fail "second run changed the file"
[ ! -e "$SANDBOX/backup3" ] || fail "second run backed something up"
grep -q "already has" <<<"$out" || fail "second run did not say already has"

# Two snippets into one file in one run: the backup holds the original, not the in-between file.
echo '{ "permissions": { "deny": ["Bash(gh auth token:*)"] } }' >"$SANDBOX/second.json"
cp "$SANDBOX/original.json" "$s"
"$MERGE" "$SANDBOX/snippet.json" "$s" "$SANDBOX/backup4" >/dev/null
"$MERGE" "$SANDBOX/second.json" "$s" "$SANDBOX/backup4" >/dev/null
cmp -s "$(find "$SANDBOX/backup4" -type f)" "$SANDBOX/original.json" || fail "two snippets: backup is not the original"
check "$s" '.permissions.deny | index("Bash(gh auth token:*)") != null' "two snippets: second not merged"

# Blank target: treated as empty, so the snippet is merged.
printf '  \n' >"$SANDBOX/blank.json"
"$MERGE" "$SANDBOX/snippet.json" "$SANDBOX/blank.json" "$SANDBOX/backup5" >/dev/null
check "$SANDBOX/blank.json" '.hooks.PreToolUse[0].hooks[0].command == "guard"' "blank target: snippet not merged"

# Invalid target: fails naming the file, and leaves it alone.
echo '{bad' >"$SANDBOX/bad.json"
if err="$("$MERGE" "$SANDBOX/snippet.json" "$SANDBOX/bad.json" "$SANDBOX/backup6" 2>&1)"; then fail "invalid target: exited 0"; fi
grep -q "bad.json" <<<"$err" || fail "invalid target: error does not name the file"
[ "$(cat "$SANDBOX/bad.json")" = '{bad' ] || fail "invalid target: file changed"

# Edited snippet: the entry calling the same ~/.agents/hooks script is replaced, not doubled.
hook() { printf '{"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "~/.agents/hooks/g.sh"%s}]}]}}' "$1"; }
hook '' >"$SANDBOX/v1.json"
hook ', "timeout": 5' >"$SANDBOX/v2.json"
echo '{"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "orca"}]}]}}' >"$SANDBOX/edit.json"
"$MERGE" "$SANDBOX/v1.json" "$SANDBOX/edit.json" "$SANDBOX/backup7" >/dev/null
"$MERGE" "$SANDBOX/v2.json" "$SANDBOX/edit.json" "$SANDBOX/backup7" >/dev/null
check "$SANDBOX/edit.json" '.hooks.PreToolUse | length == 2' "edited snippet: entry doubled or orca entry lost"
check "$SANDBOX/edit.json" '.hooks.PreToolUse[1].hooks[0].timeout == 5' "edited snippet: entry not updated"

[ "$fails" -eq 0 ] || { echo "merge-json.test.sh: $fails failure(s)"; exit 1; }
echo "merge-json.test.sh: ok"
