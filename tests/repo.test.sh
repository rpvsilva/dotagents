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
  grep -q "^| \`$s\` |" README.md || fail "README.md: no row for skill '$s'"
done

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

# Shell scripts are committed executable.
while read -r mode _ _ path; do
  [ "$mode" = "100755" ] || fail "$path is not executable (git update-index --chmod=+x $path)"
done < <(git ls-files -s '*.sh')

# CLAUDE.md is committed as a symlink to AGENTS.md. Reads the index, so Windows checkouts without symlinks agree.
mode="$(git ls-files -s CLAUDE.md | cut -d' ' -f1)"
if [ "$mode" != "120000" ] || [ "$(git cat-file blob :CLAUDE.md 2>/dev/null)" != "AGENTS.md" ]; then
  fail "CLAUDE.md is not committed as a symlink to AGENTS.md (git rm --cached CLAUDE.md && ln -sf AGENTS.md CLAUDE.md && git add CLAUDE.md)"
fi

[ "$fails" -eq 0 ] || { echo "repo.test.sh: $fails failure(s)"; exit 1; }
echo "repo.test.sh: ok"
