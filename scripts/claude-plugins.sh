#!/usr/bin/env bash
# Installs the Claude Code plugins listed in a plugins file, adding their marketplaces first.
# Only installs what is missing: a plugin already installed, even disabled, is left as it is.
# A marketplace source starting with ./ is a folder of this repo, passed to claude as its absolute path;
# any other source (owner/repo, a URL, an absolute path) is passed as written.
# Usage: scripts/claude-plugins.sh <plugins.txt>
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
list="${1:?usage: claude-plugins.sh <plugins.txt>}"
marketplaces="$(claude plugin marketplace list --json | jq -r '.[].name')"
installed="$(claude plugin list --json | jq -r '.[].id')"
failed=0

while read -r id source; do
  case "$id" in '' | '#'*) continue ;; esac
  case "$source" in ./*) source="$REPO/${source#./}" ;; esac
  market="${id#*@}"
  if ! grep -qxF "$market" <<<"$marketplaces"; then
    claude plugin marketplace add "$source" >/dev/null || { echo "  plugin marketplace $source: add failed"; failed=1; continue; }
    marketplaces+=$'\n'"$market"
    echo "  plugin marketplace $market: added"
  fi
  if grep -qxF "$id" <<<"$installed"; then
    echo "  plugin $id: already installed"
  elif claude plugin install "$id" --scope user >/dev/null; then
    echo "  plugin $id: installed"
  else
    echo "  plugin $id: install failed"
    failed=1
  fi
done <"$list"

exit "$failed"
