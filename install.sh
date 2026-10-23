#!/usr/bin/env bash
# Symlink this repo into each AI tool's config folders.
# Usage: ./install.sh [all|shared|claude|codex|cursor]   (default: all)
# Existing real files are moved to ~/.dotagents-backup/<timestamp>/ before linking.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP="$HOME/.dotagents-backup/$(date +%Y%m%d-%H%M%S)"
SHARED_SKILLS="$HOME/.agents/skills"

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

skills() { find "$REPO/skills" -mindepth 1 -maxdepth 1 -type d -exec basename {} \; ; }

# Tool-neutral skills live in ~/.agents/skills, which several agents read directly.
install_shared() {
  echo "shared"
  for s in $(skills); do link "$REPO/skills/$s" "$SHARED_SKILLS/$s"; done
}

install_claude() {
  echo "claude"
  for s in $(skills); do link "$SHARED_SKILLS/$s" "$HOME/.claude/skills/$s"; done
  link "$REPO/global/AGENTS.md" "$HOME/.claude/CLAUDE.md"
  for a in "$REPO"/adapters/claude/agents/*.md; do
    [ -e "$a" ] && link "$a" "$HOME/.claude/agents/$(basename "$a")"
  done
  if ls "$REPO"/adapters/claude/hooks/*.json >/dev/null 2>&1; then
    echo "  hooks: merge adapters/claude/hooks/*.json into ~/.claude/settings.json by hand (see adapters/claude/README.md)"
  fi
}

install_codex() {
  [ -d "$HOME/.codex" ] || { echo "codex: ~/.codex not found, skipping"; return; }
  echo "codex"
  link "$REPO/global/AGENTS.md" "$HOME/.codex/AGENTS.md"
}

install_cursor() {
  echo "cursor: not wired yet (see adapters/cursor/README.md)"
}

case "${1:-all}" in
  shared) install_shared ;;
  claude) install_shared; install_claude ;;
  codex)  install_shared; install_codex ;;
  cursor) install_shared; install_cursor ;;
  all)    install_shared; install_claude; install_codex; install_cursor ;;
  *) echo "usage: $0 [all|shared|claude|codex|cursor]"; exit 1 ;;
esac
