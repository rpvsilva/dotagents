#!/usr/bin/env bash
# Undoes install.sh: removes what it linked or merged, and touches nothing else.
# Usage: ./uninstall.sh [--dry-run] [--purge] [all|claude|codex|cursor] [<group>|<part>...]   (default: all tools, every part)
# Words come in any order, as in install.sh: a tool word picks the tools, every other word a group or part
# (scripts/parts.sh list, groups.json).
#   - Links: removes every link in the folders install.sh fills that points into this repo (or, for Claude skills, into
#     ~/.agents/skills), stale ones included. Links elsewhere and real files stay. A file install.sh moved aside is
#     copied back from its newest copy in ~/.dotagents-backup/.
#   - Merged configs (~/.claude/settings.json, ~/.codex/hooks.json, ~/.cursor/hooks.json): removes the snippets'
#     entries and any hook calling ~/.agents/hooks/; everything else stays.
#   - MCP servers in mcp/servers.json and Claude plugins in adapters/claude/plugins.txt are removed (their
#     marketplaces stay). DOTAGENTS_SKIP_PLUGINS=1 skips both for Claude, as in install.sh.
#   - all also removes the shared ~/.agents links (the per-file links in ~/.agents/hooks, or an older install's one
#     link to the folder); a single tool leaves them for the others.
#   - A selection removes only the selected parts no installed part outside it still requires (scripts/parts.sh
#     closure of the installed rest), never core. Installed parts are read from the links in place. It removes those
#     parts' links one by one, and from the merged configs only their snippets and the hooks calling their scripts.
#   - --purge also deletes dotagents' own data: ~/.config/dotagents (secrets, Settings), ~/.agents/telemetry and
#     ~/.agents/runs. ~/.dotagents-backup is always kept: it holds your original files.
#   - --dry-run prints what would change and changes nothing.
# Every config file it edits is copied to ~/.dotagents-backup/<timestamp>-uninstall/ first. Running it twice is safe.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() { echo "usage: $0 [--dry-run] [--purge] [all|claude|codex|cursor] [<group>|<part>...]"; exit 1; }

# One tool word at most: a second would silently win over the first.
dry=0 purge=0 target="" words=()
for arg in "$@"; do
  case "$arg" in
    --dry-run) dry=1 ;;
    --purge) purge=1 ;;
    all | claude | codex | cursor) [ -z "$target" ] || usage; target="$arg" ;;
    -*) usage ;;
    *) words+=("$arg") ;;
  esac
done
target="${target:-all}"

# On a terminal, the plain output below is drawn as a live view (scripts/ui.sh); DOTAGENTS_PLAIN=1 keeps it plain.
if [ -t 1 ] && [ -z "${DOTAGENTS_PLAIN:-}" ] && [ "${TERM:-dumb}" != dumb ]; then
  DOTAGENTS_PLAIN=1 "$BASH" "${BASH_SOURCE[0]}" "$@" 2>&1 | "$REPO/scripts/ui.sh" uninstall "$@"
  exit "${PIPESTATUS[0]}"
fi
BACKUPS="$HOME/.dotagents-backup"
BACKUP="$BACKUPS/$(date +%Y%m%d-%H%M%S)-uninstall"
SHARED_SKILLS="$HOME/.agents/skills"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/dotagents"

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

parts() { PARTS_REPO="$REPO" "$REPO/scripts/parts.sh" "$@"; }

# Is part $1 of kind $2 installed: does one of its links stand in this $HOME? An mcp part has no link: never. Under an
# older install's one link to the hooks folder, every hook script reachable through it is installed.
installed() {
  local f
  case "$2" in
    skill) ours "$SHARED_SKILLS/$1" || ours "$HOME/.claude/skills/$1" ;;
    mod) ours "$HOME/.claude/skills/$1" ;;
    agent) ours "$HOME/.claude/agents/$1.md" || ours "$HOME/.codex/agents/$1.toml" || ours "$HOME/.cursor/agents/$1.md" ;;
    hook) ours "$HOME/.agents/hooks/$1.sh" || { ours "$HOME/.agents/hooks" && [ -f "$HOME/.agents/hooks/$1.sh" ]; } ;;
    remote) ours "$HOME/.agents/bin/remote-agent" ;;
    global)
      ours "$HOME/.claude/CLAUDE.md" || ours "$HOME/.codex/AGENTS.md" && return 0
      for f in "$HOME"/.codex/rules/*; do ours "$f" && return 0; done
      return 1
      ;;
    *) return 1 ;;
  esac
}

# The parts to remove, one per line, and their kinds as `name TAB kind` lines. With no selection, every part goes
# and the folders are emptied whole (unlink_in) instead.
removed="" kinds=""
if [ ${#words[@]} -gt 0 ]; then
  parts closure "${words[@]}" >/dev/null || usage
  list="$(parts list)"
  # The selection: each named part, each part of a named group, and the subagents of a selected skill.
  chosen="$(awk -F '\t' -v w=" ${words[*]} " '
    NR == FNR { if (index(w, " " $1 " ") || index(w, " " $3 " ")) { print $1; if ($2 == "skill") skill[$4] = 1 }; next }
    $2 == "agent" && $4 in skill { print $1 }' <(printf '%s\n' "$list") <(printf '%s\n' "$list") | sort -u)"
  # What the installed parts outside the selection require stays; core is always in that closure.
  others=()
  while IFS=$'\t' read -r name kind _; do
    if ! grep -qxF "$name" <<<"$chosen" && installed "$name" "$kind"; then others+=("$name"); fi
  done <<<"$list"
  keep="$(parts closure _shared ${others[@]+"${others[@]}"})"
  removed="$(grep -vxF -f <(printf '%s\n' "$keep") <<<"$chosen" || true)"
  for name in $chosen; do
    grep -qxF "$name" <<<"$removed" || ! installed "$name" "$(awk -F '\t' -v n="$name" '$1 == n { print $2 }' <<<"$list")" ||
      echo "kept $name: core, or another installed part requires it"
  done
  kinds="$(awk -F '\t' 'NR == FNR { r[$1] = 1; next } $1 in r { print $1 "\t" $2 }' <(printf '%s\n' "$removed") - <<<"$list")"
fi

# Is part $1 to be removed? With no selection, every part is.
gone() {
  [ ${#words[@]} -gt 0 ] || return 0
  grep -qxF "$1" <<<"$removed"
}

# The removed parts of kind $1, one per line.
gone_of() { awk -F '\t' -v k="$1" '$2 == k { print $1 }' <<<"$kinds"; }

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

# Removes snippets $3... from JSON file $1: the reverse of install.sh's merge. An array the snippets fill loses their
# items and every item holding a string that contains one of the JSON array $2's strings (hook script paths), and an
# object or array left empty by that goes too. Scalars stay: the user may share them (Cursor's "version": 1), and a stray one
# changes nothing.
unmerge() {
  local file="$1" scripts="$2" snippets
  shift 2
  [ -s "$file" ] && [ $# -gt 0 ] || return 0
  jq -e 'type == "object"' "$file" >/dev/null 2>&1 || { echo "  $file: not a JSON object, left as is"; failed=1; return 0; }
  snippets="$(jq -s . "$@")"
  write "$file" "$(jq --argjson s "$snippets" --argjson h "$scripts" '
    def hookscripts: [.. | strings | select(. as $x | any($h[]; . as $p | $x | contains($p)))];
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

# Removes from folder $1 the link <part>$2 of each removed part of the kinds $3...; with no selection, every link of
# ours in $1, stale ones included.
unlink_parts() {
  local dir="$1" ext="$2" p
  shift 2
  if [ ${#words[@]} -eq 0 ]; then unlink_in "$dir"; return; fi
  for p in $(for k in "$@"; do gone_of "$k"; done); do unlink_ours "$dir/$p$ext"; done
}

# The hook scripts a merged file loses items for, as unmerge's JSON array: with no selection, any under ~/.agents/hooks/.
hook_scripts() {
  if [ ${#words[@]} -eq 0 ]; then echo '[".agents/hooks/"]'; return; fi
  gone_of hook | jq -R '".agents/hooks/\(.).sh"' | jq -cs .
}

# Unmerges from JSON file $2 the removed hooks' snippets in folder $1 (all of them, with no selection).
unmerge_hooks() {
  local dir="$1" file="$2" h snippets=()
  for h in "$dir"/*.json; do
    h="${h##*/}"
    if [ -e "$dir/$h" ] && gone "${h%.json}"; then snippets+=("$dir/$h"); fi
  done
  unmerge "$file" "$(hook_scripts)" ${snippets[@]+"${snippets[@]}"}
}

uninstall_claude_cli() {
  local id rest name
  gone global || gone mcp || return 0
  if [ -n "${DOTAGENTS_SKIP_PLUGINS:-}" ]; then
    echo "  plugins and mcp: skipped (DOTAGENTS_SKIP_PLUGINS)"
    return
  fi
  command -v claude >/dev/null || { echo "  plugins and mcp: claude not on PATH, skipped"; return; }
  if gone global; then
    while read -r id rest; do
      case "$id" in '' | '#'*) continue ;; esac
      # Only user scope: install.sh installs there, and a project's own copy is not ours to remove.
      claude plugin list --json | jq -e --arg id "$id" 'any(.[]; .id == $id and (.scope // "user") == "user")' >/dev/null || continue
      would "uninstall plugin $id" && continue
      if claude plugin uninstall "$id" --scope user >/dev/null; then echo "  plugin $id: uninstalled"
      else echo "  plugin $id: uninstall failed"; failed=1; fi
    done < <(tr -d '\r' <"$REPO/adapters/claude/plugins.txt")
  fi
  gone mcp || return 0
  for name in $(servers); do
    jq -e --arg n "$name" '.mcpServers[$n]' "$HOME/.claude.json" >/dev/null 2>&1 || continue
    would "remove mcp $name" && continue
    if claude mcp remove "$name" --scope user >/dev/null; then echo "  mcp $name: removed"
    else echo "  mcp $name: remove failed"; failed=1; fi
  done
}

# Claude's skill links point at the shared ones, so they go first (the claude section runs before shared).
uninstall_claude() {
  echo "claude"
  unlink_parts "$HOME/.claude/skills" "" skill mod
  # A link to a mod this checkout no longer has (remote-runs, replaced by agent-flow) is ours and stale: removed.
  local m
  for m in "$HOME"/.claude/skills/*; do
    if [ -L "$m" ] && [ ! -e "$m" ] && [[ "$(readlink "$m")" == "$REPO"/adapters/claude/mods/* ]]; then unlink_ours "$m"; fi
  done
  if gone global; then unlink_ours "$HOME/.claude/CLAUDE.md"; fi
  unlink_parts "$HOME/.claude/agents" .md agent
  if gone build-epic; then unlink_ours "$HOME/.agents/bin/build-epic"; fi
  unmerge_hooks "$REPO/adapters/claude/hooks" "$HOME/.claude/settings.json"
  if gone global; then unmerge "$HOME/.claude/settings.json" "$(hook_scripts)" "$REPO"/adapters/claude/settings/*.json; fi
  uninstall_claude_cli
}

uninstall_codex() {
  [ -d "$HOME/.codex" ] || { echo "codex: ~/.codex not found, skipping"; return; }
  echo "codex"
  local r
  if gone global; then
    unlink_ours "$HOME/.codex/AGENTS.md"
    if [ ${#words[@]} -eq 0 ]; then unlink_in "$HOME/.codex/rules"
    else for r in "$REPO"/adapters/codex/rules/*.rules; do unlink_ours "$HOME/.codex/rules/${r##*/}"; done; fi
  fi
  unlink_parts "$HOME/.codex/agents" .toml agent
  unmerge_hooks "$REPO/adapters/codex/hooks" "$HOME/.codex/hooks.json"
  gone mcp || return 0
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
  unlink_parts "$HOME/.cursor/agents" .md agent
  unmerge_hooks "$REPO/adapters/cursor/hooks" "$HOME/.cursor/hooks.json"
  gone mcp || return 0
  local file="$HOME/.cursor/mcp.json"
  [ -s "$file" ] || return 0
  jq -e 'type == "object"' "$file" >/dev/null 2>&1 || { echo "  $file: not a JSON object, left as is"; failed=1; return 0; }
  write "$file" "$(jq --argjson n "$(servers | jq -R . | jq -s .)" \
    'reduce $n[] as $k (.; del(.mcpServers[$k])) | if .mcpServers == {} then del(.mcpServers) else . end' "$file")"
}

uninstall_shared() {
  echo "shared"
  unlink_parts "$SHARED_SKILLS" "" skill
  if [ ${#words[@]} -gt 0 ]; then
    # Core (the hooks library, settings, capped) stays: only the removed parts' links go.
    unlink_parts "$HOME/.agents/hooks" .sh hook
    if gone remote; then unlink_ours "$HOME/.agents/bin/remote-agent"; fi
    return 0
  fi
  # ~/.agents/hooks holds one link per file; older installs made it one link to the repo folder.
  if [ -L "$HOME/.agents/hooks" ]; then unlink_ours "$HOME/.agents/hooks"; else unlink_in "$HOME/.agents/hooks"; fi
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
  [ "$dry" -eq 1 ] || rmdir "$SHARED_SKILLS" "$HOME/.agents/hooks" "$HOME/.agents/bin" "$HOME/.agents" 2>/dev/null || true
}

case "$target" in
  claude) uninstall_claude ;;
  codex) uninstall_codex ;;
  cursor) uninstall_cursor ;;
  all) uninstall_claude; uninstall_codex; uninstall_cursor; uninstall_shared ;;
esac

[ "$purge" -eq 0 ] || { [ "$target" = all ] && [ ${#words[@]} -eq 0 ]; } ||
  echo "--purge applies to a full uninstall only: dotagents data kept"
[ ! -d "$BACKUPS" ] || echo "your original files are still in $BACKUPS; delete it once you no longer need them"
[ "$failed" -eq 0 ] || { echo "some steps failed, see above"; exit 1; }
