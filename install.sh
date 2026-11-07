#!/usr/bin/env bash
# Symlink this repo into each AI tool's config folders.
# Usage: ./install.sh [all|shared|claude|codex|cursor] [<group>|<part>...]   (default: all tools, every part)
# Words come in any order: a tool word picks the tools, every other word a group or part (scripts/parts.sh list,
# groups.json). A selection installs those parts, core and every part they require, and nothing else; installs add up.
# Existing real files are moved to ~/.dotagents-backup/<timestamp>/ before linking.
# Config files other tools also write to get snippets merged in instead (scripts/merge-json.sh).
# MCP servers come from mcp/servers.json, secrets from ~/.config/dotagents/mcp.env (scripts/mcp-servers.sh).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# On a terminal, the plain output below is drawn as a live view (scripts/ui.sh); DOTAGENTS_PLAIN=1 keeps it plain.
if [ -t 1 ] && [ -z "${DOTAGENTS_PLAIN:-}" ] && [ "${TERM:-dumb}" != dumb ]; then
  DOTAGENTS_PLAIN=1 "$BASH" "${BASH_SOURCE[0]}" "$@" 2>&1 | "$REPO/scripts/ui.sh" install "$@"
  exit "${PIPESTATUS[0]}"
fi
BACKUP="$HOME/.dotagents-backup/$(date +%Y%m%d-%H%M%S)"
SHARED_SKILLS="$HOME/.agents/skills"
# Git Bash on Windows copies on `ln -s` unless told otherwise; real links need Developer Mode or admin.
export MSYS="${MSYS:+$MSYS }winsymlinks:nativestrict"

link() {
  local src="$1" dest="$2"
  mkdir -p "$(dirname "$dest")"
  if [ -L "$dest" ]; then
    rm "$dest"
  elif [ -e "$dest" ]; then
    mkdir -p "$BACKUP"
    mv "$dest" "$BACKUP/$(echo "${dest#"$HOME"/}" | tr '/' '_')"
    echo "  backed up $dest"
  fi
  ln -s "$src" "$dest"
  echo "  $dest -> $src"
}

# A failed merge or plugin install is reported at the end, so it does not stop the other tools.
failed=0

usage() { echo "usage: $0 [all|shared|claude|codex|cursor] [<group>|<part>...]"; exit 1; }

target="" words=()
for w in "$@"; do
  case "$w" in
    all | shared | claude | codex | cursor) target="$w" ;;
    *) words+=("$w") ;;
  esac
done
target="${target:-all}"
# The parts to install, one per line: the selection, core and their required needs (scripts/parts.sh closure).
# With no selection, every part.
selected=""
if [ ${#words[@]} -gt 0 ]; then
  selected="$(PARTS_REPO="$REPO" "$REPO/scripts/parts.sh" closure "${words[@]}")" || usage
fi

# True when part $1 is to be installed.
has() {
  [ -n "$selected" ] || return 0
  case $'\n'"$selected"$'\n' in *$'\n'"$1"$'\n'*) return 0 ;; esac
  return 1
}

# Merges every *.json snippet in folder $1 into JSON file $2; with --each, only the snippets of selected parts
# (adapters/<tool>/hooks/<hook>.json).
merge() {
  local f each=""
  if [ "$1" = --each ]; then each=1; shift; fi
  for f in "$1"/*.json; do
    [ -e "$f" ] || continue
    [ -z "$each" ] || { f="${f##*/}"; has "${f%.json}"; } || continue
    "$REPO/scripts/merge-json.sh" "$1/${f##*/}" "$2" "$BACKUP" || failed=1
  done
}

# Links each file matching $1 (a glob) into folder $2 when the part its name stands for is selected.
link_each() {
  local f n
  for f in $1; do
    [ -e "$f" ] || continue
    n="${f##*/}"
    if has "${n%.*}"; then link "$f" "$2/$n"; fi
  done
}

# Installs mcp/servers.json into tool $1's config.
mcp() { "$REPO/scripts/mcp-servers.sh" "$1" "$REPO/mcp/servers.json" "$BACKUP" || failed=1; }

skills() { find "$REPO/skills" -mindepth 1 -maxdepth 1 -type d -exec basename {} \; ; }

# Tool-neutral skills live in ~/.agents/skills, which several agents read directly.
# Hook scripts go to ~/.agents/hooks, the one path every adapter's hook config calls: a real folder of one link per
# file (lib.sh and secret-patterns.txt always, each selected hook), so the links show which hooks are installed.
# Commands skills run from any project go to ~/.agents/bin (remote-agent: subagents on a cluster, Docker or an SSH host, remote/README.md; settings: reads and writes Settings; capped: runs a command under a memory and time cap).
install_shared() {
  echo "shared"
  [ -z "$selected" ] || echo "  parts: $(paste -sd ' ' - <<<"$selected")"
  for s in $(skills); do if has "$s"; then link "$REPO/skills/$s" "$SHARED_SKILLS/$s"; fi; done
  # An older install linked the whole folder: linking into that link would write into the repo, so it goes first.
  [ ! -L "$HOME/.agents/hooks" ] || rm "$HOME/.agents/hooks"
  # The hooks library is core: lib.sh and secret-patterns.txt come with every selection.
  link "$REPO/scripts/hooks/lib.sh" "$HOME/.agents/hooks/lib.sh"
  link "$REPO/scripts/hooks/secret-patterns.txt" "$HOME/.agents/hooks/secret-patterns.txt"
  for f in "$REPO"/scripts/hooks/*.sh; do
    [ "${f##*/}" = lib.sh ] || link_each "$f" "$HOME/.agents/hooks"
  done
  if has remote; then link "$REPO/scripts/remote-agent.sh" "$HOME/.agents/bin/remote-agent"; fi
  link "$REPO/scripts/settings.sh" "$HOME/.agents/bin/settings"
  link "$REPO/scripts/capped" "$HOME/.agents/bin/capped"
}

install_claude() {
  echo "claude"
  for s in $(skills); do if has "$s"; then link "$SHARED_SKILLS/$s" "$HOME/.claude/skills/$s"; fi; done
  # A mod (a plugin folder with a hooks module) in ~/.claude/skills is loaded as a plugin and watched: a saved file reloads it.
  for m in "$REPO"/adapters/claude/mods/*/; do
    [ -f "$m.claude-plugin/plugin.json" ] || continue
    m="${m%/}"
    if has "${m##*/}"; then link "$m" "$HOME/.claude/skills/${m##*/}"; fi
  done
  if has global; then link "$REPO/global/AGENTS.md" "$HOME/.claude/CLAUDE.md"; fi
  link_each "$REPO/adapters/claude/agents/*.md" "$HOME/.claude/agents"
  merge --each "$REPO/adapters/claude/hooks" "$HOME/.claude/settings.json"
  if has global; then merge "$REPO/adapters/claude/settings" "$HOME/.claude/settings.json"; fi
  has global || has mcp || return 0
  # DOTAGENTS_SKIP_PLUGINS=1 skips plugins and MCP servers, both set through the claude CLI
  # (the tests set it, so they never install into a throwaway $HOME).
  if [ -n "${DOTAGENTS_SKIP_PLUGINS:-}" ]; then
    echo "  plugins and mcp: skipped (DOTAGENTS_SKIP_PLUGINS)"
  elif ! command -v claude >/dev/null; then
    echo "  plugins and mcp: claude not on PATH, skipped"
  else
    if has global; then "$REPO/scripts/claude-plugins.sh" "$REPO/adapters/claude/plugins.txt" || failed=1; fi
    if has mcp; then mcp claude; fi
  fi
}

install_codex() {
  [ -d "$HOME/.codex" ] || { echo "codex: ~/.codex not found, skipping"; return; }
  echo "codex"
  if has global; then
    link "$REPO/global/AGENTS.md" "$HOME/.codex/AGENTS.md"
    for r in "$REPO"/adapters/codex/rules/*.rules; do
      [ -e "$r" ] && link "$r" "$HOME/.codex/rules/$(basename "$r")"
    done
  fi
  link_each "$REPO/adapters/codex/agents/*.toml" "$HOME/.codex/agents"
  merge --each "$REPO/adapters/codex/hooks" "$HOME/.codex/hooks.json"
  if has mcp; then mcp codex; fi
  local h
  for h in "$REPO"/adapters/codex/hooks/*.json; do
    h="${h##*/}"
    if has "${h%.json}"; then echo "  codex: trust new or changed hooks with /hooks, or Codex skips them"; break; fi
  done
}

install_cursor() {
  [ -d "$HOME/.cursor" ] || { echo "cursor: ~/.cursor not found, skipping"; return; }
  echo "cursor"
  link_each "$REPO/adapters/cursor/agents/*.md" "$HOME/.cursor/agents"
  merge --each "$REPO/adapters/cursor/hooks" "$HOME/.cursor/hooks.json"
  if has mcp; then mcp cursor; fi
}

case "$target" in
  shared) install_shared ;;
  claude) install_shared; install_claude ;;
  codex)  install_shared; install_codex ;;
  cursor) install_shared; install_cursor ;;
  all)    install_shared; install_claude; install_codex; install_cursor ;;
esac

[ "$failed" -eq 0 ] || { echo "some config snippets, plugins or MCP servers were not installed, see above"; exit 1; }
