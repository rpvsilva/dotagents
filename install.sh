#!/usr/bin/env bash
# Symlink this repo into each AI tool's config folders.
# Usage: ./install.sh [all|shared|claude|codex|cursor]   (default: all)
# Existing real files are moved to ~/.dotagents-backup/<timestamp>/ before linking.
# Config files other tools also write to get snippets merged in instead (scripts/merge-json.sh).
# MCP servers come from mcp/servers.json, secrets from ~/.config/dotagents/mcp.env (scripts/mcp-servers.sh).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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

# Merges every *.json snippet in folder $1 into JSON file $2.
merge() {
  local f
  for f in "$1"/*.json; do
    [ -e "$f" ] || continue
    "$REPO/scripts/merge-json.sh" "$f" "$2" "$BACKUP" || failed=1
  done
}

# Installs mcp/servers.json into tool $1's config.
mcp() { "$REPO/scripts/mcp-servers.sh" "$1" "$REPO/mcp/servers.json" "$BACKUP" || failed=1; }

skills() { find "$REPO/skills" -mindepth 1 -maxdepth 1 -type d -exec basename {} \; ; }

# Tool-neutral skills live in ~/.agents/skills, which several agents read directly.
# Hook scripts go to ~/.agents/hooks, the one path every adapter's hook config calls.
install_shared() {
  echo "shared"
  for s in $(skills); do link "$REPO/skills/$s" "$SHARED_SKILLS/$s"; done
  link "$REPO/scripts/hooks" "$HOME/.agents/hooks"
}

install_claude() {
  echo "claude"
  for s in $(skills); do link "$SHARED_SKILLS/$s" "$HOME/.claude/skills/$s"; done
  link "$REPO/global/AGENTS.md" "$HOME/.claude/CLAUDE.md"
  for a in "$REPO"/adapters/claude/agents/*.md; do
    [ -e "$a" ] && link "$a" "$HOME/.claude/agents/$(basename "$a")"
  done
  merge "$REPO/adapters/claude/hooks" "$HOME/.claude/settings.json"
  merge "$REPO/adapters/claude/settings" "$HOME/.claude/settings.json"
  # DOTAGENTS_SKIP_PLUGINS=1 skips plugins and MCP servers, both set through the claude CLI
  # (the tests set it, so they never install into a throwaway $HOME).
  if [ -n "${DOTAGENTS_SKIP_PLUGINS:-}" ]; then
    echo "  plugins and mcp: skipped (DOTAGENTS_SKIP_PLUGINS)"
  elif ! command -v claude >/dev/null; then
    echo "  plugins and mcp: claude not on PATH, skipped"
  else
    "$REPO/scripts/claude-plugins.sh" "$REPO/adapters/claude/plugins.txt" || failed=1
    mcp claude
  fi
}

install_codex() {
  [ -d "$HOME/.codex" ] || { echo "codex: ~/.codex not found, skipping"; return; }
  echo "codex"
  link "$REPO/global/AGENTS.md" "$HOME/.codex/AGENTS.md"
  for a in "$REPO"/adapters/codex/agents/*.toml; do
    [ -e "$a" ] && link "$a" "$HOME/.codex/agents/$(basename "$a")"
  done
  for r in "$REPO"/adapters/codex/rules/*.rules; do
    [ -e "$r" ] && link "$r" "$HOME/.codex/rules/$(basename "$r")"
  done
  merge "$REPO/adapters/codex/hooks" "$HOME/.codex/hooks.json"
  mcp codex
  echo "  codex: trust new or changed hooks with /hooks, or Codex skips them"
}

install_cursor() {
  [ -d "$HOME/.cursor" ] || { echo "cursor: ~/.cursor not found, skipping"; return; }
  echo "cursor"
  for a in "$REPO"/adapters/cursor/agents/*.md; do
    [ -e "$a" ] && link "$a" "$HOME/.cursor/agents/$(basename "$a")"
  done
  merge "$REPO/adapters/cursor/hooks" "$HOME/.cursor/hooks.json"
  mcp cursor
}

case "${1:-all}" in
  shared) install_shared ;;
  claude) install_shared; install_claude ;;
  codex)  install_shared; install_codex ;;
  cursor) install_shared; install_cursor ;;
  all)    install_shared; install_claude; install_codex; install_cursor ;;
  *) echo "usage: $0 [all|shared|claude|codex|cursor]"; exit 1 ;;
esac

[ "$failed" -eq 0 ] || { echo "some config snippets, plugins or MCP servers were not installed, see above"; exit 1; }
