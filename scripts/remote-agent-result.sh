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
# result event that is not an error); capped_off (null, or what the sandbox lacked to run the caps: they were off and commands ran uncapped); capped_kills (each command the sandbox's caps killed, [{kind, cmd}], kind memory
# or timeout and cmd null when the line names none: from progress.log's `capped: killed: <kind> <cmd>` lines, which
# came live, else from the capped.log that came back); killed (the last kill's kind, else null); lost (true when the
# run failed with an exit other than 3, 4 or 5, no kill line, and a progress line saying its container or pod was gone).
# Anything unknown is null, never 0.
set -euo pipefail

keep="$1"
rc="$2"
[ -f "$keep/run.json" ] || exit 1

# A temporary folder for what jq reads from files: the summary and the last result event can each be larger than
# one argument may be (128 KiB on Linux, ~32K characters for the whole command line on Git Bash).
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# The files a patch touches, each as a JSON string on its own line, in the patch's order and once each: a rename's or
# copy's two paths, from its `rename from`/`rename to` (`copy`) lines, and both sides' paths from the `---`/`+++`
# lines, git's C-quoted names ("a/caf\303\251.txt") unquoted; for a section with neither (a mode change, a binary
# file, an empty new file), the name in the `diff --git` header. Only header lines count: a hunk's content never does.
# In the C locale, so awk handles bytes: an octal escape is one byte of the name's UTF-8.
patch_files() {
  LC_ALL=C awk '
    BEGIN {
      for (i = 1; i < 32; i++) ctl[sprintf("%c", i)] = sprintf("\\u%04x", i)
      esc["a"] = sprintf("%c", 7); esc["b"] = sprintf("%c", 8); esc["t"] = "\t"; esc["n"] = "\n"
      esc["v"] = sprintf("%c", 11); esc["f"] = sprintf("%c", 12); esc["r"] = "\r"
    }
    # The path at the start of s: a quoted one unquoted, else s up to a tab. Sets used to the characters it took.
    function path(s,   out, i, c, n, k) {
      if (substr(s, 1, 1) != "\"") { i = index(s, "\t"); if (i) s = substr(s, 1, i - 1); used = length(s); return s }
      out = ""
      for (i = 2; i <= length(s); i++) {
        c = substr(s, i, 1)
        if (c == "\"") break
        if (c == "\\") {
          c = substr(s, ++i, 1)
          if (c ~ /[0-7]/) {
            n = c + 0
            for (k = 1; k < 3 && substr(s, i + 1, 1) ~ /[0-7]/; k++) n = n * 8 + substr(s, ++i, 1)
            c = sprintf("%c", n)
          } else if (c in esc) c = esc[c]
        }
        out = out c
      }
      used = i
      return out
    }
    function side(p) { return (p ~ /^[ab]\//) ? substr(p, 3) : p }
    function add(p,   out, i, c) {
      if (p == "" || p == "/dev/null" || p in seen) return
      seen[p] = 1
      got = 1
      out = ""
      for (i = 1; i <= length(p); i++) {
        c = substr(p, i, 1)
        if (c == "\\" || c == "\"") out = out "\\" c
        else if (c in ctl) out = out ctl[c]
        else out = out c
      }
      print "\"" out "\""
    }
    # A section that named no path: its header names it twice, the same on both sides.
    function flush(   rest, n) {
      if (head == "" || got) return
      if (substr(head, 1, 1) == "\"") {
        path(head)
        rest = substr(head, used + 2)
        add(side(path(rest)))
      } else {
        n = (length(head) - 1) / 2
        if (n == int(n) && substr(head, 1, 2) == "a/" && substr(head, n + 1, 3) == " b/") add(substr(head, n + 4))
      }
    }
    { sub(/\r$/, "") }
    index($0, "diff --git ") == 1 { flush(); head = substr($0, 12); got = 0; hdr = 1; next }
    !hdr { next }
    /^@@ / || /^GIT binary patch/ { hdr = 0; next }
    /^(---|\+\+\+) / { p = path(substr($0, 5)); if (p != "/dev/null") add(side(p)); next }
    /^(rename|copy) from / { add(path(substr($0, length($1) + 7))); next }
    /^(rename|copy) to / { add(path(substr($0, length($1) + 5))); next }
    END { flush() }
  ' "$1"
}
if [ -s "$keep/changes.patch" ]; then
  patch_files "$keep/changes.patch" | jq -cn '[inputs]' | tr -d '\r' >"$tmp/changed"
elif [ -e "$keep/changes.patch" ] || [ "$rc" = 3 ]; then
  echo '[]' >"$tmp/changed"
else
  echo null >"$tmp/changed"
fi

report=null
: >"$tmp/summary"
if [ -f "$keep/summary.md" ]; then
  report="$(printf '%s' "$keep/summary.md" | jq -Rs . | tr -d '\r')"
  cp "$keep/summary.md" "$tmp/summary"
fi

# The caps' kills, and whether the sandbox was lost: from the progress lines, CRs dropped.
: >"$tmp/progress"
[ ! -f "$keep/progress.log" ] || tr -d '\r' <"$keep/progress.log" >"$tmp/progress"
: >"$tmp/kills"
grep -E '^capped: killed: (memory|timeout)( |$)' "$tmp/progress" >"$tmp/kills" 2>/dev/null \
  || { [ ! -f "$keep/capped.log" ] || tr -d '\r' <"$keep/capped.log" | grep -E '^capped: killed: (memory|timeout)( |$)' >"$tmp/kills"; } \
  || true
# The sandbox's own line when the caps could not run there: what was missing, else null.
capoff="$(sed -nE 's/^== capped: unavailable \(missing: ([^)]*)\).*/\1/p' "$tmp/progress" | head -1)"
gone=false
if grep -qE 'No such container|container not found|pods? "[^"]*" not found' "$tmp/progress"; then gone=true; fi

# The transcript's last result event and init model, read one event at a time; a partial last line (a killed run) is
# skipped.
echo '{}' >"$tmp/events"
if [ -f "$keep/transcript.jsonl" ]; then
  jq -cRn 'reduce (inputs | fromjson? | objects) as $e ({};
      if $e.type == "result" then .result = $e
      elif $e.type == "system" and $e.subtype == "init" and ($e.model | type) == "string" then .model = $e.model
      else . end)' "$keep/transcript.jsonl" 2>/dev/null | tr -d '\r' >"$tmp/events.new" \
    && [ -s "$tmp/events.new" ] && mv "$tmp/events.new" "$tmp/events"
fi

# shellcheck disable=SC2016 # jq's variables
jq -c --argjson rc "$rc" --argjson gone "$gone" --arg capoff "$capoff" --rawfile kills "$tmp/kills" --slurpfile changed "$tmp/changed" --argjson report "$report" \
  --rawfile summary "$tmp/summary" --slurpfile ev "$tmp/events" '
  ($ev[0].result // null) as $r
  | [$kills | split("\n")[] | capture("^capped: killed: (?<kind>memory|timeout)( (?<cmd>.+))?$")
      | {kind, cmd: (.cmd // null)}] as $k
  | ($summary | split("\n") | map(sub("\r$"; "") | sub("^[\\s>*`-]+"; ""))) as $lines
  | {run: .run, name: .name, exit_code: $rc,
     status: (if $rc == 0 or $rc == 3 then "done" else "failed" end),
     report_status: ([$lines[] | capture("^status:\\s*(?<s>\\S+)").s] | first // null),
     changed: $changed[0],
     follow_up: [$lines[] | capture("^follow-up:\\s*(?<f>.*)$").f | sub("\\s+$"; "") | select(. != "")],
     report_path: $report,
     duration_ms: ($r.duration_ms // (if (.started | type) == "number" and (.ended | type) == "number"
       then (.ended - .started) * 1000 else null end)),
     num_turns: ($r.num_turns // null), total_cost_usd: ($r.total_cost_usd // null), usage: ($r.usage // null),
     model: ($ev[0].model // ($r.modelUsage // {} | keys_unsorted | first) // null),
     complete: ($r != null and $r.is_error != true and ($rc == 0 or $rc == 3 or $rc == 4)),
     capped_off: (if $capoff == "" then null else $capoff end), capped_kills: $k, killed: ($k | last | .kind // null),
     lost: ($gone and ($k | length) == 0 and ([0, 3, 4, 5] | index($rc)) == null)}' "$keep/run.json" \
  | tr -d '\r'
