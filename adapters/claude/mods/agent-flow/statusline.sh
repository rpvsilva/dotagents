#!/usr/bin/env bash
# Prints agent-flow's band for a status line: this turn's running agents, who needs you, failed and remote runs, and a
# bar of how many ended, coloured, with spinners that step once a second. Nothing when no agent runs, waits or failed.
#
# Usage, from a Claude Code status line command that has read its JSON payload into $input:
#   ~/.claude/skills/agent-flow/statusline.sh "$(jq -r '.scratchpad_dir // ""' <<<"$input")"
# and `"refreshInterval": 1` under `statusLine` in settings, so it is re-run every second.
#
# The mod writes <scratchpad>/agent-flow/band.txt: one line per spinner frame; this prints the line of this second.
set -u

dir="${1:-}"
[ -n "$dir" ] || exit 0
file="$dir/agent-flow/band.txt"
[ -s "$file" ] || exit 0
n="$(wc -l <"$file" | tr -d ' ')"
[ "$n" -gt 0 ] || exit 0
sed -n "$(( $(date +%s) % n + 1 ))p" "$file"
