#!/usr/bin/env bash
# Installs the Claude Code plugins listed in a plugins file, adding their marketplaces first.
# Only installs what is missing. A plugin already installed but disabled is enabled at user scope when the user's
# settings hold no entry for it (it was never turned on, e.g. installed at another scope); one set to false there was
# turned off on purpose and stays off.
# Usage: scripts/claude-plugins.sh <plugins.txt>
set -euo pipefail

list="${1:?usage: claude-plugins.sh <plugins.txt>}"
marketplaces="$(claude plugin marketplace list --json | jq -r '.[].name' | tr -d '\r')"
plugins="$(claude plugin list --json)"
installed="$(jq -r '.[].id' <<<"$plugins" | tr -d '\r')"
disabled="$(jq -r '.[] | select(.enabled == false) | .id' <<<"$plugins" | tr -d '\r')"
settings="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json"
failed=0

while read -r id source; do
  case "$id" in '' | '#'*) continue ;; esac
  market="${id#*@}"
  if ! grep -qxF "$market" <<<"$marketplaces"; then
    claude plugin marketplace add "$source" >/dev/null || { echo "  plugin marketplace $source: add failed"; failed=1; continue; }
    marketplaces+=$'\n'"$market"
    echo "  plugin marketplace $market: added"
  fi
  if grep -qxF "$id" <<<"$disabled"; then
    if [ "$(jq -r --arg id "$id" '.enabledPlugins[$id]' "$settings" 2>/dev/null)" = false ]; then
      echo "  plugin $id: turned off in $settings, left off"
    elif claude plugin enable "$id" --scope user >/dev/null; then
      echo "  plugin $id: enabled"
    else
      echo "  plugin $id: enable failed"
      failed=1
    fi
  elif grep -qxF "$id" <<<"$installed"; then
    echo "  plugin $id: already installed"
  elif claude plugin install "$id" --scope user >/dev/null; then
    echo "  plugin $id: installed"
  else
    echo "  plugin $id: install failed"
    failed=1
  fi
done < <(tr -d '\r' <"$list") # a CRLF checkout (Windows) must not leave \r on a source

exit "$failed"
