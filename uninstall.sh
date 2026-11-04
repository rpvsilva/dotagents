#!/usr/bin/env bash
# Undoes install.sh: removes what it linked or merged, and touches nothing else.
# Usage: ./uninstall.sh [--dry-run] [--purge] [all|claude|codex|cursor]   (default: all)
#   - Links: removes every link in the folders install.sh fills that points into this repo (or, for Claude skills, into
#     ~/.agents/skills), stale ones included. Links elsewhere and real files stay. A file install.sh moved aside is
#     copied back from its newest copy in ~/.dotagents-backup/.
#   - Merged configs (~/.claude/settings.json, ~/.codex/hooks.json, ~/.cursor/hooks.json): removes the snippets'
#     entries and any hook calling ~/.agents/hooks/; everything else stays.
#   - MCP servers in mcp/servers.json and Claude plugins in adapters/claude/plugins.txt are removed (their
#     marketplaces stay). DOTAGENTS_SKIP_PLUGINS=1 skips both for Claude, as in install.sh.
#   - all also removes the shared ~/.agents links; a single tool leaves them for the others.
#   - --purge also deletes dotagents' own data: ~/.config/dotagents (secrets, Settings), ~/.agents/telemetry and
#     ~/.agents/runs. ~/.dotagents-backup is always kept: it holds your original files.
#   - --dry-run prints what would change and changes nothing.
# Every config file it edits is copied to ~/.dotagents-backup/<timestamp>-uninstall/ first. Running it twice is safe.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# On a terminal, the plain output below is drawn as a live view (scripts/ui.sh); DOTAGENTS_PLAIN=1 keeps it plain.
if [ -t 1 ] && [ -z "${DOTAGENTS_PLAIN:-}" ] && [ "${TERM:-dumb}" != dumb ]; then
  DOTAGENTS_PLAIN=1 "$BASH" "${BASH_SOURCE[0]}" "$@" 2>&1 | "$REPO/scripts/ui.sh" uninstall "$@"
  exit "${PIPESTATUS[0]}"
fi
BACKUPS="$HOME/.dotagents-backup"
BACKUP="$BACKUPS/$(date +%Y%m%d-%H%M%S)-uninstall"
SHARED_SKILLS="$HOME/.agents/skills"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/dotagents"

dry=0 purge=0 target=all
for arg in "$@"; do
  case "$arg" in
    --dry-run) dry=1 ;;
    --purge) purge=1 ;;
    all | claude | codex | cursor) target="$arg" ;;
    *) echo "usage: $0 [--dry-run] [--purge] [all|claude|codex|cursor]"; exit 1 ;;
  esac
done
command -v jq >/dev/null || { echo "uninstall.sh needs jq"; exit 1; }

# A failed step is reported at the end, so it does not stop the others.
failed=0
would() { [ "$dry" -eq 1 ] && echo "  would $*"; }

# The name install.sh gives $1's copy in a backup folder.
backup_name() { echo "${1#"$HOME"/}" | tr '/' '_'; }

# Is $1 a link install.sh made: one into this repo, or a Claude skill linked into ~/.agents/skills?
ours() {
  [ -L "$1" ] || return 1
  case "$(readlink "$1")" in "$REPO"/* | "$SHARED_SKILLS"/*) return 0 ;; esac
  return 1
}

# Removes link $1 when it is ours, then copies back the newest file install.sh moved aside for it.
unlink_ours() {
  local dest="$1" b saved=""
  ours "$dest" || return 0
  for b in "$BACKUPS"/*/"$(backup_name "$dest")"; do [ -e "$b" ] && saved="$b"; done # timestamps sort oldest first
  if would "remove $dest"; then
    [ -z "$saved" ] || would "restore $dest from $saved"
    return 0
  fi
  rm "$dest"
  echo "  removed $dest"
  [ -n "$saved" ] || return 0
  cp -Rp "$saved" "$dest"
  echo "  restored $dest from $saved"
}

unlink_in() {
  local f
  for f in "$1"/* "$1"/.[!.]*; do unlink_ours "$f"; done
}

# Writes $2 to file $1 when it differs, copying the old file to the backup folder first. Empty content deletes the file.
# A JSON file differs only in content, not in layout.
write() {
  local file="$1" content="$2"
  [ "$(cat "$file")" != "$content" ] || return 0
  case "$file" in *.json) [ "$(jq -S . "$file")" != "$(jq -S . <<<"$content")" ] || return 0 ;; esac
  would "edit $file" && return 0
  mkdir -p "$BACKUP"
  cp "$file" "$BACKUP/$(backup_name "$file")"
  if [ -z "$content" ]; then
    rm "$file"
    echo "  removed $file (nothing else was in it)"
  else
    printf '%s\n' "$content" >"$file"
    echo "  $file: dotagents entries removed"
  fi
}

# Removes every *.json snippet in folder $1 from JSON file $2: the reverse of install.sh's merge. An array loses the
# snippet's items and every item calling a ~/.agents/hooks/ script, and an object or array left empty by that goes too.
# Scalars stay: the user may share them (Cursor's "version": 1), and a stray one changes nothing.
unmerge() {
  local dir="$1" file="$2" snippets
  [ -s "$file" ] || return 0
  jq -e 'type == "object"' "$file" >/dev/null 2>&1 || { echo "  $file: not a JSON object, left as is"; failed=1; return 0; }
  snippets="$(jq -s . "$dir"/*.json)"
  write "$file" "$(jq --argjson s "$snippets" '
    def hookscripts: [.. | strings | select(test("\\.agents/hooks/"))];
    # [] when nothing is left, else [what is left].
    def strip($b):
      if type == "object" and ($b | type) == "object" then
        (reduce ($b | keys_unsorted[]) as $k (.;
          if has($k) then (.[$k] | strip($b[$k])) as $r | if $r == [] then del(.[$k]) else .[$k] = $r[0] end
          else . end)) as $n
        | if $n == {} and . != {} then [] else [$n] end
      elif type == "array" and ($b | type) == "array" then
        map(select(. as $x | (any($b[]; . == $x) or ($x | hookscripts | length) > 0) | not)) as $n
        | if $n == [] and . != [] then [] else [$n] end
      else [.] end;
    reduce $s[] as $b (.; strip($b)[0] // {})' "$file")"
}

servers() { jq -r '.mcpServers | keys[]' "$REPO/mcp/servers.json"; }

uninstall_claude_cli() {
  local id rest name
  if [ -n "${DOTAGENTS_SKIP_PLUGINS:-}" ]; then
    echo "  plugins and mcp: skipped (DOTAGENTS_SKIP_PLUGINS)"
    return
  fi
  command -v claude >/dev/null || { echo "  plugins and mcp: claude not on PATH, skipped"; return; }
  while read -r id rest; do
    case "$id" in '' | '#'*) continue ;; esac
    # Only user scope: install.sh installs there, and a project's own copy is not ours to remove.
    claude plugin list --json | jq -e --arg id "$id" 'any(.[]; .id == $id and (.scope // "user") == "user")' >/dev/null || continue
    would "uninstall plugin $id" && continue
    if claude plugin uninstall "$id" --scope user >/dev/null; then echo "  plugin $id: uninstalled"
    else echo "  plugin $id: uninstall failed"; failed=1; fi
  done < <(tr -d '\r' <"$REPO/adapters/claude/plugins.txt")
  for name in $(servers); do
    jq -e --arg n "$name" '.mcpServers[$n]' "$HOME/.claude.json" >/dev/null 2>&1 || continue
    would "remove mcp $name" && continue
    if claude mcp remove "$name" --scope user >/dev/null; then echo "  mcp $name: removed"
    else echo "  mcp $name: remove failed"; failed=1; fi
  done
}

uninstall_claude() {
  echo "claude"
  unlink_in "$HOME/.claude/skills"
  unlink_ours "$HOME/.claude/CLAUDE.md"
  unlink_in "$HOME/.claude/agents"
  unmerge "$REPO/adapters/claude/hooks" "$HOME/.claude/settings.json"
  unmerge "$REPO/adapters/claude/settings" "$HOME/.claude/settings.json"
  uninstall_claude_cli
}

uninstall_codex() {
  [ -d "$HOME/.codex" ] || { echo "codex: ~/.codex not found, skipping"; return; }
  echo "codex"
  unlink_ours "$HOME/.codex/AGENTS.md"
  unlink_in "$HOME/.codex/agents"
  unlink_in "$HOME/.codex/rules"
  unmerge "$REPO/adapters/codex/hooks" "$HOME/.codex/hooks.json"
  # The marked MCP block scripts/mcp-servers.sh appends, and the blank lines before it.
  local file="$HOME/.codex/config.toml"
  [ -e "$file" ] || return 0
  write "$file" "$(awk '
    $0 ~ /^# >>> dotagents mcp/ {skip=1}
    !skip {print}
    $0 ~ /^# <<< dotagents mcp/ {skip=0}' "$file")"
}

uninstall_cursor() {
  [ -d "$HOME/.cursor" ] || { echo "cursor: ~/.cursor not found, skipping"; return; }
  echo "cursor"
  unlink_in "$HOME/.cursor/agents"
  unmerge "$REPO/adapters/cursor/hooks" "$HOME/.cursor/hooks.json"
  local file="$HOME/.cursor/mcp.json"
  [ -s "$file" ] || return 0
  jq -e 'type == "object"' "$file" >/dev/null 2>&1 || { echo "  $file: not a JSON object, left as is"; failed=1; return 0; }
  write "$file" "$(jq --argjson n "$(servers | jq -R . | jq -s .)" \
    'reduce $n[] as $k (.; del(.mcpServers[$k])) | if .mcpServers == {} then del(.mcpServers) else . end' "$file")"
}

uninstall_shared() {
  echo "shared"
  unlink_in "$SHARED_SKILLS"
  unlink_ours "$HOME/.agents/hooks"
  unlink_in "$HOME/.agents/bin"
  if [ "$purge" -eq 1 ]; then
    local d
    for d in "$CONFIG" "$HOME/.agents/telemetry" "$HOME/.agents/runs"; do
      [ -e "$d" ] || continue
      would "delete $d" && continue
      rm -rf "$d"
      echo "  deleted $d"
    done
  fi
  # Folders install.sh created, once nothing else is in them.
  [ "$dry" -eq 1 ] || rmdir "$SHARED_SKILLS" "$HOME/.agents/bin" "$HOME/.agents" 2>/dev/null || true
}

case "$target" in
  claude) uninstall_claude ;;
  codex) uninstall_codex ;;
  cursor) uninstall_cursor ;;
  all) uninstall_claude; uninstall_codex; uninstall_cursor; uninstall_shared ;;
esac

[ "$target" = all ] || [ "$purge" -eq 0 ] || echo "--purge applies to all only: dotagents data kept"
[ ! -d "$BACKUPS" ] || echo "your original files are still in $BACKUPS; delete it once you no longer need them"
[ "$failed" -eq 0 ] || { echo "some steps failed, see above"; exit 1; }
