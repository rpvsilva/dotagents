#!/usr/bin/env bash
# Prints a remote-agent run's result.json on stdout: what run folder $1 says the run came to, given its exit code $2.
# Usage: remote-agent-result.sh <run folder> <exit code>   (remote-agent.sh calls it on every exit, redacts the token
# and writes the file through a temporary one and `mv`; it reads only files already redacted, and the patch's names)
# Fields: run, name (from run.json); exit_code; status (done on exit 0 or 3, else failed, as run.json's state);
# report_status (the report's `status:` line in summary.md, null without one); changed (the files changes.patch
# touches; [] when it is empty, as on exit 3; null when no patch came back); follow_up (the report's `follow-up:`
# lines, [] when none); report_path (summary.md, null when none came back); duration_ms, num_turns, total_cost_usd,
# usage (from the last result event of transcript.jsonl; duration_ms from run.json's started and ended without one);
# model (the init event's, else the first of the result's modelUsage); complete (true only on exit 0, 3 or 4 with a
# result event that is not an error). Anything unknown is null, never 0.
set -euo pipefail

keep="$1"
rc="$2"
[ -f "$keep/run.json" ] || exit 1

# The files a patch touches, one per line, from its `diff --git a/<path> b/<path>` headers.
patch_files() {
  awk 'index($0, "diff --git a/") == 1 {
    rest = substr($0, 14)
    n = (length(rest) - 3) / 2
    if (n == int(n) && substr(rest, n + 1, 3) == " b/") print substr(rest, n + 4)
  }' "$1"
}
if [ -s "$keep/changes.patch" ]; then
  changed="$(patch_files "$keep/changes.patch" | jq -Rn '[inputs]' | tr -d '\r')"
elif [ -e "$keep/changes.patch" ] || [ "$rc" = 3 ]; then
  changed='[]'
else
  changed=null
fi

report=null
summary=""
if [ -f "$keep/summary.md" ]; then
  report="$(printf '%s' "$keep/summary.md" | jq -Rs . | tr -d '\r')"
  summary="$(cat "$keep/summary.md")"
fi

# The transcript's last result event and init model; a partial last line (a killed run) is skipped.
events='{}'
if [ -f "$keep/transcript.jsonl" ]; then
  events="$(jq -cRn '[inputs | fromjson? | objects] as $e
    | {result: ([$e[] | select(.type == "result")] | last),
       model: ([$e[] | select(.type == "system" and .subtype == "init") | .model | strings] | last)}' \
    "$keep/transcript.jsonl" 2>/dev/null | tr -d '\r')" || events='{}'
  [ -n "$events" ] || events='{}'
fi

# shellcheck disable=SC2016 # jq's variables
jq -c --argjson rc "$rc" --argjson changed "$changed" --argjson report "$report" --arg summary "$summary" \
  --argjson ev "$events" '
  ($ev.result // null) as $r
  | ($summary | split("\n") | map(sub("\r$"; "") | sub("^[\\s>*`-]+"; ""))) as $lines
  | {run: .run, name: .name, exit_code: $rc,
     status: (if $rc == 0 or $rc == 3 then "done" else "failed" end),
     report_status: ([$lines[] | capture("^status:\\s*(?<s>\\S+)").s] | first // null),
     changed: $changed,
     follow_up: [$lines[] | capture("^follow-up:\\s*(?<f>.*)$").f | sub("\\s+$"; "") | select(. != "")],
     report_path: $report,
     duration_ms: ($r.duration_ms // (if (.started | type) == "number" and (.ended | type) == "number"
       then (.ended - .started) * 1000 else null end)),
     num_turns: ($r.num_turns // null), total_cost_usd: ($r.total_cost_usd // null), usage: ($r.usage // null),
     model: ($ev.model // ($r.modelUsage // {} | keys_unsorted | first) // null),
     complete: ($r != null and $r.is_error != true and ($rc == 0 or $rc == 3 or $rc == 4))}' "$keep/run.json" \
  | tr -d '\r'
