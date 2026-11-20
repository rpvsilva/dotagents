#!/usr/bin/env bash
# Checks the repo's own rules from AGENTS.md: skill layout, README rows, links, MCP secrets, script modes.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
fails=0

fail() { echo "FAIL: $*"; fails=$((fails + 1)); }

# Prints the value of frontmatter key $2 in file $1.
frontmatter() { awk -v k="$2" 'NR==1 && $0!="---" {exit} NR>1 && $0=="---" {exit} NR>1 && index($0, k": ")==1 {print substr($0, length(k)+3); exit}' "$1"; }

# Prints `<path>: frontmatter is not valid YAML: <message>` for each file among $@ whose frontmatter (between the first
# two `---` lines) a strict YAML parse rejects, or is not a Hash with String `name` and `description`. One ruby call.
yaml_frontmatter_fails() {
  command -v ruby >/dev/null 2>&1 || { echo "ruby is missing: the frontmatter YAML check needs it (stdlib psych)"; return; }
  ruby -ryaml -e '
    ARGV.each do |path|
      begin
        lines = File.read(path, encoding: "UTF-8").lines.map(&:chomp)
        raise "no frontmatter" unless lines.first == "---"
        stop = (1...lines.size).find { |i| lines[i] == "---" } or raise "no closing ---"
        doc = YAML.safe_load(lines[1...stop].join("\n"))
        raise "not a mapping" unless doc.is_a?(Hash)
        %w[name description].each { |k| raise "#{k} is not a String" unless doc[k].is_a?(String) }
      rescue StandardError, ScriptError => e
        puts "#{path}: frontmatter is not valid YAML: #{e.message.lines.first.to_s.strip}"
      end
    end' "$@"
}
yaml_files=()
for f in skills/*/SKILL.md adapters/claude/agents/*.md adapters/cursor/agents/*.md; do [ -f "$f" ] && yaml_files+=("$f"); done
while IFS= read -r bad; do [ -z "$bad" ] || fail "$bad"; done < <(yaml_frontmatter_fails "${yaml_files[@]}")
# Negative case: an unquoted ": " in a description must be reported, by path.
bad_yaml="$(mktemp -d)/bad.md"
printf -- '---\nname: bad\ndescription: a: b\n---\n' >"$bad_yaml"
case "$(yaml_frontmatter_fails "$bad_yaml")" in
  "$bad_yaml: frontmatter is not valid YAML: "*) ;;
  *) fail "frontmatter check: an unquoted ': ' in a description is not reported by path" ;;
esac
rm -rf "$(dirname "$bad_yaml")"

# Skills: SKILL.md with a matching name, a description, and a README row.
# Folders starting with _ hold shared files skills link to, not skills.
for d in skills/*/; do
  d="${d%/}"
  s="$(basename "$d")"
  case "$s" in _*) continue ;; esac
  f="$d/SKILL.md"
  if [ ! -f "$f" ]; then fail "$f missing"; continue; fi
  [ "$(frontmatter "$f" name)" = "$s" ] || fail "$f: frontmatter name is not '$s'"
  [ -n "$(frontmatter "$f" description)" ] || fail "$f: frontmatter description missing"
  grep -qF "| [\`$s\`]($f) |" README.md || fail "README.md: no row for skill '$s' linking to $f"
done

# README inventory: every hook, subagent, MCP server, Claude plugin, mod and group is named in README.md as `name`,
# and every docs/ page is linked from it, so the README never goes stale.
readme_names() { # $1: what it is, then the names
  local what="$1" n
  shift
  for n in "$@"; do grep -qF "\`$n\`" README.md || fail "README.md: $what '$n' is not listed"; done
}
for h in scripts/hooks/*.sh; do
  h="${h##*/}"
  [ "$h" = lib.sh ] || readme_names hook "${h%.sh}"
done
for a in adapters/*/agents/*; do
  [ -f "$a" ] || continue
  a="${a##*/}"
  readme_names subagent "${a%.*}"
done
while read -r srv; do readme_names "MCP server" "$srv"; done < <(jq -r '.mcpServers // {} | keys[]' mcp/servers.json 2>/dev/null | tr -d '\r')
while read -r p _; do
  case "$p" in '#'* | '') continue ;; esac
  readme_names "Claude plugin" "${p%%@*}"
done < <(tr -d '\r' <adapters/claude/plugins.txt)
for d in adapters/claude/mods/*/; do
  [ -d "$d" ] || continue
  d="${d%/}"
  readme_names mod "${d##*/}"
done
while read -r g; do readme_names group "$g"; done < <(jq -r 'keys[]' groups.json 2>/dev/null | tr -d '\r')
# The README's at-a-glance counts ("13 skills · 9 subagents · 4 hooks · 2 MCP servers") match what exists.
readme_count() { # $1: the label, $2: the real count
  grep -qE "(^|[^0-9])$2 $1( |\$)" README.md || fail "README.md: the at-a-glance line does not say '$2 $1'"
}
readme_count skills "$(find skills -mindepth 1 -maxdepth 1 -type d ! -name '_*' | wc -l | tr -d ' ')"
readme_count subagents "$(find adapters/claude/agents -name '*.md' | wc -l | tr -d ' ')"
readme_count hooks "$(find scripts/hooks -name '*.sh' ! -name lib.sh | wc -l | tr -d ' ')"
readme_count "MCP servers" "$(jq -r '.mcpServers // {} | length' mcp/servers.json)"
for p in docs/*.md; do
  [ -f "$p" ] || continue
  grep -qF "]($p)" README.md || fail "README.md: does not link to $p"
done

# Settings Declarations: valid JSON, each key with a known kind, a meaning, a default of its kind (null for unset),
# choices for an enum, known Levels. A skill reads a shared key (skills/_shared/settings.json) by opting in with
# {"shared": true}, never by redeclaring it, and only opts in to a key that exists.
for f in skills/*/settings.json remote/settings.json; do
  [ -f "$f" ] || continue
  jq -e 'type == "object"' "$f" >/dev/null 2>&1 || { fail "$f: not a JSON object"; continue; }
  while read -r bad; do fail "$f: $bad"; done < <(jq -r 'to_entries[] | select(.value != {"shared": true}) | .key as $k | .value as $d
    | if ($d | type) != "object" then "\($k): not an object"
      elif ([$d.kind] | inside(["int", "bool", "string", "enum", "list"]) | not) then "\($k): unknown kind \($d.kind)"
      elif ($d.meaning | type) != "string" or $d.meaning == "" then "\($k): no meaning"
      elif $d.kind == "enum" and ((($d.choices | type) != "array") or ($d.choices | length) == 0) then "\($k): an enum without choices"
      elif ($d | has("default") | not) then "\($k): no default (null for unset)"
      elif $d.default != null and (($d.kind == "int" and ($d.default | type) != "number")
        or ($d.kind == "bool" and ($d.default | type) != "boolean")
        or (($d.kind == "string" or $d.kind == "enum") and ($d.default | type) != "string")
        or ($d.kind == "enum" and ($d.choices | index([$d.default])) == null)
        or ($d.kind == "list" and ($d.default | type) != "array")) then "\($k): default \($d.default | tojson) is not of kind \($d.kind)"
      elif $d.levels != null and (($d.levels | type) != "array" or ($d.levels | length) == 0 or ($d.levels | inside(["machine", "repo"]) | not)) then "\($k): levels must be machine and/or repo"
      else empty end' "$f")
  case "$f" in
    skills/_shared/*) ;;
    *) while read -r bad; do fail "$f: $bad"; done < <(jq -rn --slurpfile a "$f" --slurpfile s skills/_shared/settings.json \
      '$a[0] | to_entries[] | .key as $k | ($s[0] | has($k)) as $shared
       | if .value == {"shared": true} and ($shared | not) then "\($k): opts in to a shared Setting that does not exist"
         elif .value != {"shared": true} and $shared then "\($k): redeclares a shared Setting; opt in with {\"shared\": true}"
         else empty end') ;;
  esac
done

# Needs declarations (scripts/parts.sh): each a JSON object with a group from groups.json, needs naming a part as
# required or optional (never a core part: _shared or one of group core), agents as a list of names, and remote as
# the list of those agents that may run remotely (skills/remote-subagents/SKILL.md). Every skill, mod and hook has
# one; skills/_shared is core and has none. Every subagent wrapper's stem is owned by exactly one skill, the one holding the brief it names, and every
# owned agent has a wrapper. No part or group takes a tool word install.sh reserves.
parts_list="$(scripts/parts.sh list 2>&1 | tr -d '\r')" || { fail "scripts/parts.sh list: $parts_list"; parts_list=""; }
part_names="$(cut -f1 <<<"$parts_list")"
decls=()
for f in skills/*/needs.json adapters/claude/mods/*/needs.json scripts/hooks/*.needs.json remote/needs.json \
  mcp/needs.json global/needs.json; do
  [ -f "$f" ] || continue
  case "$f" in skills/_*) fail "$f: skills/_* is core, it has no needs.json"; continue ;; esac
  decls+=("$f")
done
# One jq reads every declaration; only when that fails, each is read alone to name the bad ones.
if [ ${#decls[@]} -gt 0 ] && ! jq -en '[inputs | type == "object"] | all' "${decls[@]}" >/dev/null 2>&1; then
  good=()
  for f in "${decls[@]}"; do
    if jq -e 'type == "object"' "$f" >/dev/null 2>&1; then good+=("$f"); else fail "$f: not a JSON object"; fi
  done
  decls=(${good[@]+"${good[@]}"})
fi
if [ ${#decls[@]} -gt 0 ]; then
  core_names="$(awk -F'\t' '$3 == "core" {print $1}' <<<"$parts_list")"
  while read -r bad; do fail "$bad"; done < <(jq -r --slurpfile g groups.json --arg parts "$part_names" --arg core "_shared
$core_names" '
    ($parts | split("\n")) as $names | ($core | split("\n")) as $core | input_filename as $f
    | [(if (.group | type) != "string" then "no group"
        elif (.group as $grp | $g[0] | has($grp) | not) then "group \(.group) is not in groups.json"
        else empty end),
      (if (.needs | type) != "object" then "needs is not an object"
       else .needs | to_entries[]
         | .key as $k | if any($core[]; . == $k) then "need \(.key) is core, which no part declares"
           elif $parts != "" and (any($names[]; . == $k) | not) then "need \(.key) is not a part (scripts/parts.sh list)"
           elif .value != "required" and .value != "optional" then "need \(.key): \(.value | tojson) is not required or optional"
           else empty end end),
      (if has("agents") and ((.agents | type) != "array" or any(.agents[]; type != "string")) then "agents is not a list of names"
       else empty end),
      (if has("remote") | not then empty
       elif (.remote | type) != "array" or any(.remote[]; type != "string") then "remote is not a list of names"
       else (if (.agents | type) == "array" then .agents else [] end) as $a | .remote[] | select(. as $r | any($a[]; . == $r) | not)
         | "remote \(.) is not in this file'"'"'s agents" end),
      (keys - ["group", "needs", "agents", "remote"] | .[] | "unknown key \(.)")][] | "\($f): \(.)"' "${decls[@]}" | tr -d '\r')
fi
for d in skills/*/ adapters/claude/mods/*/; do
  [ -d "$d" ] || continue
  case "$d" in skills/_*) continue ;; esac
  [ -f "${d}needs.json" ] || fail "${d}needs.json missing: declare the part's group and needs"
done
for h in scripts/hooks/*.sh; do
  [ "$h" = scripts/hooks/lib.sh ] || [ -f "${h%.sh}.needs.json" ] || fail "${h%.sh}.needs.json missing: declare the hook's group and needs"
done
for f in remote/needs.json mcp/needs.json global/needs.json; do [ -f "$f" ] || fail "$f missing"; done
# owners: `agent TAB skill` for each agent a skill lists.
owners=""
[ ${#decls[@]} -eq 0 ] || owners="$(jq -r 'input_filename as $f | select($f | startswith("skills/"))
  | (.agents // [])[]? | strings | "\(.)\t\($f | split("/")[1])"' "${decls[@]}" | tr -d '\r')"
while read -r bad; do fail "$bad"; done < <({
  [ -z "$owners" ] || awk '{print "O\t" $0}' <<<"$owners"
  for a in adapters/*/agents/*; do [ ! -f "$a" ] || printf 'W\t%s\n' "$a"; done
  grep -oHE '[~]/\.(agents|claude)/skills/[^`" ]+' adapters/*/agents/* 2>/dev/null | sed 's/^/R\t/' || true
} | awk -F'\t' '
  $1 == "O" { if ($2 in own) own[$2] = own[$2] " " $3; else own[$2] = $3; next }
  $1 == "W" { s = $2; sub(/.*\//, "", s); sub(/\.[^.]*$/, "", s); stem[$2] = s; wrapped[s] = 1; next }
  $1 == "R" { i = index($2, ":"); f = substr($2, 1, i - 1); r = substr($2, i + 1); sub(/^~\/\.(agents|claude)\/skills\//, "", r); refs[f] = refs[f] " " r }
  END {
    for (f in stem) {
      s = stem[f]; o = own[s]
      if (o == "") { print f ": no skill lists " s " in its needs.json agents"; continue }
      if (o ~ / /) { print f ": " s " is listed in agents by more than one skill: " o; continue }
      c = split(refs[f], rs, " ")
      for (j = 1; j <= c; j++) if (substr(rs[j], 1, length(o) + 1) != o "/") print f ": follows skills/" rs[j] ", but " s " is owned by " o
    }
    for (s in own) if (!(s in wrapped)) {
      c = split(own[s], os, " ")
      for (j = 1; j <= c; j++) print "skills/" os[j] "/needs.json: agent " s " has no wrapper in adapters/*/agents/"
    }
  }' | sort)
for n in $part_names $(jq -r 'keys[]' groups.json | tr -d '\r'); do
  case "$n" in all | shared | claude | codex | cursor) fail "$n: a part or group named as an install.sh tool word" ;; esac
done

# Skills read Settings through ~/.agents/bin/settings, never from a notes file: a skill or brief line naming a
# declared key next to a notes file must call it a Setting.
keys="$(jq -rs 'map(keys[]) | unique[]' skills/*/settings.json remote/settings.json)"
while IFS= read -r line; do
  for k in $keys; do
    case "$line" in *"\`$k\`"*) [[ "$line" == *Setting* ]] || fail "${line%%:*}: names \`$k\` next to a notes file, not as a Setting" ;; esac
  done
done < <(grep -n -i 'notes' skills/*/*.md | sed 's/^\([^:]*\):\([0-9]*\):/\1:\2: /')

# Relative markdown links resolve.
while read -r md; do
  dir="$(dirname "$md")"
  while read -r target; do
    target="${target%%#*}"
    [ -z "$target" ] && continue
    [ -e "$dir/$target" ] || fail "$md: broken link to $target"
  done < <(grep -oE '\]\([^)[:space:]]+\)' "$md" | sed -E 's/^\]\((.*)\)$/\1/' | grep -vE '^(https?:|mailto:|#)' || true)
done < <(git ls-files '*.md')

# Adapter agents point at a brief that exists in skills/, under the tool's own skills folder.
for a in adapters/*/agents/*; do
  [ -f "$a" ] || continue
  refs="$(grep -oE '[~]/\.(agents|claude)/skills/[^`" ]+' "$a" || true)"
  [ -n "$refs" ] || fail "$a: no ~/.agents/skills/... or ~/.claude/skills/... brief referenced"
  for r in $refs; do
    [ -f "skills/${r#\~/.agents/skills/}" ] || [ -f "skills/${r#\~/.claude/skills/}" ] || fail "$a: $r does not exist in skills/"
  done
done

# Couplings: a part's files (scripts/parts.sh files, minus tests and fixtures) refer to another part only as a declared
# need. References: a markdown link into skills/<x>/ or to a file or folder another part ships (scripts/parts.sh files,
# core's through _shared), ~/.agents/skills/<x> or $HOME/.agents/skills/<x>,
# ~/.agents/bin/remote-agent (part remote), .agents/hooks/<x>, and a subagent's name in backticks in a skill's *.md.
# Never needs: the part itself, a skill's own agents, an agent's own skill, and core (_shared, configure,
# ~/.agents/bin/settings, the hooks library).
agent_names="$(awk -F'\t' '$2 == "agent" {print $1}' <<<"$parts_list" | paste -sd'|' -)"
# shellcheck disable=SC2016 # a regex: $HOME is matched, not expanded
refs_re='\]\([^)[:space:]]+\)|`\.\./[A-Za-z0-9_-]+/|(~|\$HOME)/\.agents/(skills|bin)/[A-Za-z0-9_-]+|\.agents/hooks/[A-Za-z0-9_.-]+'
[ -z "$agent_names" ] || refs_re="$refs_re|\`($agent_names)\`"
# One awk reads tagged lines: P (part, kind, declaration), O (agent, owning skill), N (declaration, its needs and
# agents), S (part, a file it ships), F (part, a file of it scanned), then R (file:reference, from one grep over the
# scanned files).
part_files=""
files=()
while IFS=$'\t' read -r p _; do
  [ -n "$p" ] || continue
  while read -r f; do
    part_files="$part_files"$'S\t'"$p"$'\t'"$f"$'\n'
    [ "$p" != _shared ] || continue
    case "/$f" in */fixtures/* | */test_* | *.test.*) continue ;; esac
    part_files="$part_files"$'F\t'"$p"$'\t'"$f"$'\n'
    files+=("$f")
  done < <(scripts/parts.sh files "$p" | tr -d '\r')
done < <(printf '%s\n_shared\n' "$parts_list")
# Each finding carries the matched reference text m; a backtick match also prints agent_names once, control characters
# visible, so a platform-only failure is diagnosable from the CI log.
coupling_fails="$({
  [ -z "$parts_list" ] || awk '{print "P\t" $0}' <<<"$parts_list"
  [ -z "$owners" ] || awk '{print "O\t" $0}' <<<"$owners"
  [ ${#decls[@]} -eq 0 ] || jq -r 'input_filename as $f | [$f, (.needs // {} | keys[]?), (.agents // [])[]?] | "N\t" + join(" ")' \
    "${decls[@]}" 2>/dev/null | tr -d '\r'
  printf '%s' "$part_files"
  [ ${#files[@]} -eq 0 ] || grep -oHE "$refs_re" "${files[@]}" | sed 's/^/R\t/'
} | awk -F'\t' '
  $1 == "P" { kind[$2] = $3; decl[$2] = $5; next }
  $1 == "O" { owner[$2] = $3; next }
  $1 == "N" { k = index($2, " "); needs[k ? substr($2, 1, k - 1) : $2] = k ? substr($2, k) : ""; next }
  # ships: the part shipping a file, or every file under a folder; "*" when several parts share the folder.
  $1 == "S" {
    ships[$3] = $2; d = $3
    while (sub(/\/[^\/]*$/, "", d)) { if (!(d in ships)) ships[d] = $2; else if (ships[d] != $2) ships[d] = "*" }
    next
  }
  $1 == "F" { parts[$3] = parts[$3] " " $2; next }
  {
    r = substr($0, 3); i = index(r, ":"); f = substr(r, 1, i - 1); m = substr(r, i + 1); n = ""
    if (m ~ /^\]\(/) {
      if (f !~ /\.md$/) next
      t = substr(m, 3, length(m) - 3); sub(/#.*/, "", t)
      if (t == "" || t ~ /^[a-z]+:/ || t ~ /^\//) next
      d = f; sub(/\/[^\/]*$/, "", d); if (d == f) d = "."
      k = split(d "/" t, seg, "/"); top = 0
      for (j = 1; j <= k; j++) {
        if (seg[j] == "" || seg[j] == ".") continue
        if (seg[j] == "..") { if (top > 0) top--; continue }
        out[++top] = seg[j]
      }
      if (top >= 2 && out[1] == "skills") n = out[2]
      else if (top >= 1) {
        t = out[1]; for (j = 2; j <= top; j++) t = t "/" out[j]
        if (t in ships && ships[t] != "*") n = ships[t]
      }
    } else if (m ~ /^`\.\.\//) { n = substr(m, 5, length(m) - 5)
    } else if (m ~ /agents\/skills\//) { n = m; sub(/.*agents\/skills\//, "", n) }
    else if (m ~ /agents\/bin\//) { n = m; sub(/.*agents\/bin\//, "", n); n = (n == "remote-agent") ? "remote" : "settings" }
    else if (m ~ /agents\/hooks\//) { n = m; sub(/.*agents\/hooks\//, "", n); sub(/\.[a-z]+$/, "", n) }
    else if (m ~ /^`/) { if (f !~ /^skills\/.*\.md$/) next; n = substr(m, 2, length(m) - 2) }
    if (n == "") next
    c = split(parts[f], ps, " ")
    for (j = 1; j <= c; j++) {
      p = ps[j]
      ok = " " p " _shared configure settings lib secret-patterns " (kind[p] == "agent" ? owner[p] : needs[decl[p]]) " "
      if (index(ok, " " n " ") == 0) print f "\t" n "\t" p "\t" decl[p] "\t" m
    }
  }' | sort | awk -F'\t' '!seen[$1 FS $2 FS $3 FS $4]++')"
if [ -n "$coupling_fails" ] && cut -f5 <<<"$coupling_fails" | grep -q '^`'; then
  echo "coupling scan: a backtick match; agent_names as built:"
  printf '%s\n' "$agent_names" | sed -n l
fi
while IFS=$'\t' read -r f n p decl m; do
  [ -n "$f" ] || continue
  fail "$f: refers to $n, not a need of $p in $decl (matched: $m)"
done <<<"$coupling_fails"

# Hook snippets are valid JSON and call a hook script that exists.
for h in adapters/*/hooks/*.json adapters/*/settings/*.json; do
  [ -f "$h" ] || continue
  jq -e . "$h" >/dev/null 2>&1 || { fail "$h: invalid JSON"; continue; }
  for s in $(jq -r '.. | .command? | strings' "$h" | grep -oE '\.agents/hooks/[^"'"'"' ]+' || true); do
    script="scripts/hooks/${s#.agents/hooks/}"
    [ -f "$script" ] || fail "$h: calls $s but $script does not exist"
  done
done
for h in adapters/*/hooks/*.json; do
  [ -f "$h" ] || continue
  jq -e '[.. | .command? | strings | select(test("\\.agents/hooks/"))] | length > 0' "$h" >/dev/null 2>&1 || fail "$h: calls no script in ~/.agents/hooks/"
done

# Claude mods: install.sh links each folder of adapters/claude/mods/ into ~/.claude/skills/ beside the skills, so a
# mod is named in its plugin.json as its folder, and no mod shares a name with a skill.
for d in adapters/claude/mods/*/; do
  [ -d "$d" ] || continue
  d="${d%/}"
  m="${d##*/}"
  [ "$(jq -r .name "$d/.claude-plugin/plugin.json" 2>/dev/null | tr -d '\r')" = "$m" ] || fail "$d/.claude-plugin/plugin.json: missing or not named $m"
  [ ! -e "skills/$m" ] || fail "$d: a skill has the same name, both would be ~/.claude/skills/$m"
done

# MCP: valid JSON, and every env/header value is a ${VAR} reference.
if ! jq -e '.mcpServers | type == "object"' mcp/servers.json >/dev/null; then
  fail "mcp/servers.json: invalid JSON or no mcpServers object"
else
  while read -r bad; do
    fail "mcp/servers.json: $bad is a literal, use a \${ENV_VAR} reference"
  done < <(jq -r '.mcpServers | to_entries[] | .key as $srv | .value | (.env // {}), (.headers // {}) | to_entries[]
    | select(.value | test("\\$\\{[A-Za-z_][A-Za-z0-9_]*\\}") | not) | "\($srv).\(.key)"' mcp/servers.json)
fi

# The high tier in .review-gate.yml and the paths in CODEOWNERS list the same paths.
high="$(awk '/^  high:/ {on=1; next} on && /^    - / {print $2; next} on {exit}' .review-gate.yml | sort)"
owned="$(awk '!/^#/ && NF {print $1}' CODEOWNERS | sort)"
[ -n "$high" ] || fail ".review-gate.yml: no tiers.high list found"
while read -r p; do
  fail "$p is in only one of .review-gate.yml (high tier) and CODEOWNERS"
done < <(comm -3 <(echo "$high") <(echo "$owned") | tr -d '\t' | grep . || true)

# The remote-subagents skill says to give the human the transcript path, to leave remote-agent's stderr unredirected,
# to read the outcome from result.json through remote-agent result, that the remote list in needs.json declares
# which agents are remote-capable, and which uses of an agent that entry covers. The shared conventions only link to it.
rs=skills/remote-subagents/SKILL.md
if [ -f "$rs" ]; then
  remote="$(cat "$rs")"
  grep -q 'transcript' <<<"$remote" || fail "$rs: does not mention the transcript"
  grep -qi 'not redirect' <<<"$remote" || fail "$rs: does not say to leave stderr unredirected"
  grep -q 'result\.json' <<<"$remote" || fail "$rs: does not name result.json"
  grep -q 'remote-agent result' <<<"$remote" || fail "$rs: does not name remote-agent result"
  # shellcheck disable=SC2016 # literal backticks: `remote` is matched, not run
  grep -q 'needs\.json.*under `remote`' <<<"$remote" || fail "$rs: does not say an agent is remote-capable when its needs.json lists it under remote"
  grep -q 'The entry covers \*\*every use\*\*' <<<"$remote" || fail "$rs: does not say which uses of an agent its remote entry covers"
else
  fail "$rs is missing"
fi
# No other skill restates which agents are remote-capable: each links the declaration through remote-subagents.
while IFS= read -r hit; do
  fail "$hit: says remote-capable; only skills/remote-subagents/SKILL.md does, the others link it"
done < <(grep -rli 'remote-capable' skills --exclude-dir=remote-subagents || true)
if grep -q '^## Remote subagents' skills/_shared/conventions.md; then
  fail "skills/_shared/conventions.md: restates Remote subagents; link ../remote-subagents/SKILL.md instead"
fi

# Public groups name no one: the files a part outside group personal ships (and core's, through _shared) never name
# Rui, his accounts, the company org or his cluster. The image default is the one documented Setting value, so rpvsilva
# may appear in remote/settings.json and remote/README.md. Tests and fixtures are sample data, skipped on purpose.
personal_files=()
while IFS=$'\t' read -r p _ g _; do
  [ -n "$p" ] && [ "$g" != personal ] || continue
  while read -r f; do
    case "/$f" in */fixtures/* | */test_* | *.test.*) continue ;; esac
    [ -f "$f" ] && personal_files+=("$f")
  done < <(scripts/parts.sh files "$p" | tr -d '\r')
done < <(printf '%s\n_shared\t\tcore\n' "$parts_list")
if [ ${#personal_files[@]} -gt 0 ]; then
  while IFS=: read -r f t; do
    case "$t:$f" in rpvsilva:remote/settings.json | rpvsilva:remote/README.md) continue ;; esac
    fail "$f: names $t"
  done < <({
    grep -oHE 'rpvsilva|0xgui' "${personal_files[@]}" || true
    grep -oHw 'Rui' "${personal_files[@]}" || true
  } | sort -u)
fi

# Eval cases: each evals/<skill>/<case>/ follows the case format (evals/_runner/cases.py, docs/evals.md). Evals are dev
# tooling, never shipped: no part, core included, lists a file under evals/, and no needs.json makes one a part (a
# case's fixture/ and input/ are sample data, skipped).
if ! cases_out="$(python3 -I evals/_runner/cases.py check 2>&1)"; then
  while IFS= read -r line; do fail "evals: $line"; done <<<"$cases_out"
fi
while IFS= read -r p; do
  [ -n "$p" ] || continue
  while read -r f; do
    case "$f" in evals/*) fail "part '$p' ships $f; nothing under evals/ is a part" ;; esac
  done < <(scripts/parts.sh files "$p" | tr -d '\r')
done < <(printf '_shared\n%s\n' "$part_names")
while IFS= read -r f; do
  case "$f" in evals/*/*/fixture/* | evals/*/*/input/*) continue ;; esac
  fail "$f: evals are dev tooling, not a part; no needs.json under evals/"
done < <(find evals \( -name needs.json -o -name '*.needs.json' \) -print 2>/dev/null | sort)

# CI never runs evals: they bill a model per case, so they run only when asked (EVALS=1 tests/run.sh) on this machine.
while IFS=: read -r f n _; do
  fail "$f:$n: CI never runs evals; no workflow names evals/ or EVALS"
done < <(grep -rnE 'evals/|EVALS' .github/workflows/ 2>/dev/null)

# Shell scripts are committed executable.
while read -r mode _ _ path; do
  [ "$mode" = "100755" ] || fail "$path is not executable (git update-index --chmod=+x $path)"
done < <(git ls-files -s '*.sh')

# The shellcheck image is the pinned version, pinned by digest.
sc_version="$(tr -d '\r\n' <scripts/shellcheck-version)"
case "$(tr -d '\r\n' <scripts/shellcheck-image)" in
  "koalaman/shellcheck:v$sc_version@sha256:"*) ;;
  *) fail "scripts/shellcheck-image is not koalaman/shellcheck:v$sc_version@sha256:<digest> (scripts/shellcheck-version)" ;;
esac

# Every suite has a line in tests/suites.txt, or tests/run.sh and CI would never run it.
for t in tests/*.test.sh; do
  awk -v s="${t##*/}" '$1 == s { found = 1 } END { exit !found }' tests/suites.txt \
    || fail "tests/suites.txt: no line for ${t##*/}"
done

# Contributor files exist, and no doc claims GitHub enforces nothing.
for f in CONTRIBUTING.md .github/ISSUE_TEMPLATE/bug_report.yml .github/ISSUE_TEMPLATE/feature_request.yml .github/ISSUE_TEMPLATE/config.yml; do
  [ -f "$f" ] || fail "$f is missing"
done
if grep -rnE 'enforces nothing|enforces no branch protection|GitHub Free' AGENTS.md docs/*.md scripts >/dev/null 2>&1; then
  fail "AGENTS.md, docs/ or scripts/ hold 'enforces nothing', 'enforces no branch protection' or 'GitHub Free': say what GitHub enforces (the main ruleset) and what merge-pr.sh and guard-merge add"
fi

# CLAUDE.md is committed as a symlink to AGENTS.md. Reads the index, so Windows checkouts without symlinks agree.
mode="$(git ls-files -s CLAUDE.md | cut -d' ' -f1)"
if [ "$mode" != "120000" ] || [ "$(git cat-file blob :CLAUDE.md 2>/dev/null)" != "AGENTS.md" ]; then
  fail "CLAUDE.md is not committed as a symlink to AGENTS.md (git rm --cached CLAUDE.md && ln -sf AGENTS.md CLAUDE.md && git add CLAUDE.md)"
fi

[ "$fails" -eq 0 ] || { echo "repo.test.sh: $fails failure(s)"; exit 1; }
echo "repo.test.sh: ok"
