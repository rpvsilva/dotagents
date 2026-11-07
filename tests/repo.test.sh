#!/usr/bin/env bash
# Checks the repo's own rules from AGENTS.md: skill layout, README rows, links, MCP secrets, script modes.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
fails=0

fail() { echo "FAIL: $*"; fails=$((fails + 1)); }

# Prints the value of frontmatter key $2 in file $1.
frontmatter() { awk -v k="$2" 'NR==1 && $0!="---" {exit} NR>1 && $0=="---" {exit} NR>1 && index($0, k": ")==1 {print substr($0, length(k)+3); exit}' "$1"; }

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

# README inventory: every hook, subagent, MCP server, Claude plugin and mod is named in README.md as `name`,
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

# Adapter agents point at a brief that exists in skills/.
for a in adapters/*/agents/*; do
  [ -f "$a" ] || continue
  refs="$(grep -oE '[~]/\.agents/skills/[^`" ]+' "$a" || true)"
  [ -n "$refs" ] || fail "$a: no ~/.agents/skills/... brief referenced"
  for r in $refs; do
    [ -f "skills/${r#\~/.agents/skills/}" ] || fail "$a: $r does not exist in skills/"
  done
done

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
# and to read the outcome from result.json through remote-agent result. The shared conventions only link to it.
rs=skills/remote-subagents/SKILL.md
if [ -f "$rs" ]; then
  remote="$(cat "$rs")"
  grep -q 'transcript' <<<"$remote" || fail "$rs: does not mention the transcript"
  grep -qi 'not redirect' <<<"$remote" || fail "$rs: does not say to leave stderr unredirected"
  grep -q 'result\.json' <<<"$remote" || fail "$rs: does not name result.json"
  grep -q 'remote-agent result' <<<"$remote" || fail "$rs: does not name remote-agent result"
else
  fail "$rs is missing"
fi
if grep -q '^## Remote subagents' skills/_shared/conventions.md; then
  fail "skills/_shared/conventions.md: restates Remote subagents; link ../remote-subagents/SKILL.md instead"
fi

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

# CLAUDE.md is committed as a symlink to AGENTS.md. Reads the index, so Windows checkouts without symlinks agree.
mode="$(git ls-files -s CLAUDE.md | cut -d' ' -f1)"
if [ "$mode" != "120000" ] || [ "$(git cat-file blob :CLAUDE.md 2>/dev/null)" != "AGENTS.md" ]; then
  fail "CLAUDE.md is not committed as a symlink to AGENTS.md (git rm --cached CLAUDE.md && ln -sf AGENTS.md CLAUDE.md && git add CLAUDE.md)"
fi

[ "$fails" -eq 0 ] || { echo "repo.test.sh: $fails failure(s)"; exit 1; }
echo "repo.test.sh: ok"
