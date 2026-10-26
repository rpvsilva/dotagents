#!/usr/bin/env bash
# Merges a config snippet into a JSON file that other tools also write to (~/.claude/settings.json,
# ~/.codex/hooks.json, ~/.cursor/hooks.json), so install.sh never replaces it.
# Objects merge key by key, arrays gain only the items they lack, and existing scalars win.
# Running it twice changes nothing. Before a change, the old file is copied into <backup-dir>.
# Usage: scripts/merge-json.sh <snippet.json> <target.json> <backup-dir>
set -euo pipefail

snippet="${1:?usage: merge-json.sh <snippet.json> <target.json> <backup-dir>}"
target="${2:?usage: merge-json.sh <snippet.json> <target.json> <backup-dir>}"
backup="${3:?usage: merge-json.sh <snippet.json> <target.json> <backup-dir>}"

current="{}"
[ -s "$target" ] && current="$(cat "$target")"

merged="$(jq --slurpfile s "$snippet" '
  def merge($b):
    if type == "object" and ($b | type) == "object" then
      reduce ($b | keys_unsorted[]) as $k (.; .[$k] = (if has($k) then .[$k] | merge($b[$k]) else $b[$k] end))
    elif type == "array" and ($b | type) == "array" then
      . as $a | $a + [$b[] | select(. as $x | $a | any(. == $x) | not)]
    elif . == null then $b
    else . end;
  merge($s[0])' <<<"$current")"

if [ "$(jq -S . <<<"$current")" = "$(jq -S . <<<"$merged")" ]; then
  echo "  $target: already has $(basename "$snippet")"
  exit 0
fi

mkdir -p "$(dirname "$target")"
if [ -e "$target" ]; then
  # Keep the first copy: two snippets merged into one file in a run must back up the original.
  saved="$backup/$(echo "${target#"$HOME"/}" | tr '/' '_')"
  if [ ! -e "$saved" ]; then
    mkdir -p "$backup"
    cp "$target" "$saved"
    echo "  backed up $target"
  fi
fi
printf '%s\n' "$merged" >"$target"
echo "  $target: merged $(basename "$snippet")"
