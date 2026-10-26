#!/usr/bin/env bash
# Symlink this repo into each AI tool's config folders.
# Usage: ./install.sh [all|shared|claude|codex|cursor]   (default: all)
# Existing real files are moved to ~/.dotagents-backup/<timestamp>/ before linking.
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
  if ls "$REPO"/adapters/claude/hooks/*.json >/dev/null 2>&1; then
    echo "  hooks: merge adapters/claude/hooks/*.json into ~/.claude/settings.json by hand (see adapters/claude/README.md)"
  fi
}

install_codex() {
  [ -d "$HOME/.codex" ] || { echo "codex: ~/.codex not found, skipping"; return; }
  echo "codex"
  link "$REPO/global/AGENTS.md" "$HOME/.codex/AGENTS.md"
  for a in "$REPO"/adapters/codex/agents/*.toml; do
    [ -e "$a" ] && link "$a" "$HOME/.codex/agents/$(basename "$a")"
  done
}

install_cursor() {
  [ -d "$HOME/.cursor" ] || { echo "cursor: ~/.cursor not found, skipping"; return; }
  echo "cursor"
  for a in "$REPO"/adapters/cursor/agents/*.md; do
    [ -e "$a" ] && link "$a" "$HOME/.cursor/agents/$(basename "$a")"
  done
}

case "${1:-all}" in
  shared) install_shared ;;
  claude) install_shared; install_claude ;;
  codex)  install_shared; install_codex ;;
  cursor) install_shared; install_cursor ;;
  all)    install_shared; install_claude; install_codex; install_cursor ;;
  *) echo "usage: $0 [all|shared|claude|codex|cursor]"; exit 1 ;;
esac
