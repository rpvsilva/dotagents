#!/usr/bin/env bash
# Stages the parts of this machine's agent setup a remote sandbox needs into <dir>, laid out like $HOME.
# Usage: scripts/remote-agent-pack.sh <dir>
# Copies, following links: ~/.claude/{CLAUDE.md,skills,agents,commands,rules} and ~/.agents/{skills,hooks}.
# Writes a filtered ~/.claude/settings.json (hooks that call ~/.agents/hooks, permissions, model, enabled
# plugins and their GitHub-hosted marketplaces) and ~/.claude/remote-mcp.json (MCP servers with no env or
# headers, which could hold secrets). Never copies credentials, history, transcripts, caches or settings env.
# Prints what it left out to stderr.
set -euo pipefail

# shellcheck source=hooks/lib.sh disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/hooks/lib.sh"

out="${1:?usage: $0 <dir>}"
mkdir -p "$out/.claude" "$out/.agents"
note() { echo "remote-agent-pack: $*" >&2; }

for p in .claude/CLAUDE.md .claude/skills .claude/agents .claude/commands .claude/rules .agents/skills .agents/hooks; do
  [ -e "$HOME/$p" ] || continue
  cp -RL "$HOME/$p" "$out/$p" 2>/dev/null || note "some of ~/$p could not be copied (broken links?)"
done

# Secret-looking files never leave the machine, wherever they sit.
while IFS= read -r -d '' f; do
  if is_secret "${f#"$out"/}"; then rm -f "$f"; note "left out ${f#"$out"/} (looks like a secret)"; fi
done < <(find "$out" -type f -print0)
find "$out" -name .DS_Store -delete

settings="$HOME/.claude/settings.json"
if [ -f "$settings" ]; then
  jq '{
    hooks: ((.hooks // {})
      | map_values(map(.hooks |= map(select((.command // "") | test("\\.agents/hooks/")))) | map(select(.hooks | length > 0)))
      | with_entries(select(.value | length > 0))),
    permissions, model, enabledPlugins,
    extraKnownMarketplaces: ((.extraKnownMarketplaces // {}) | with_entries(select(.value.source.source == "github")))
  } | with_entries(select(.value != null))' "$settings" >"$out/.claude/settings.json"
  left="$(jq -r '[(.env // {} | keys[] | "env." + .), (if .statusLine then "statusLine" else empty end),
    (if .apiKeyHelper then "apiKeyHelper" else empty end),
    (.extraKnownMarketplaces // {} | to_entries[] | select(.value.source.source != "github") | "marketplace " + .key)] | join(", ")' "$settings")"
  [ -z "$left" ] || note "settings left out: $left"
  dropped="$(jq '[.hooks // {} | .[][] | .hooks[]? | select((.command // "") | test("\\.agents/hooks/") | not)] | length' "$settings")"
  [ "$dropped" = 0 ] || note "settings left out $dropped hook(s) that do not call ~/.agents/hooks"
fi

if [ -f "$HOME/.claude.json" ]; then
  jq '.mcpServers // {} | with_entries(select(((.value.env // {}) | length) == 0 and ((.value.headers // {}) | length) == 0))' \
    "$HOME/.claude.json" >"$out/.claude/remote-mcp.json"
  left="$(jq -r '.mcpServers // {} | to_entries[] | select(((.value.env // {}) | length) > 0 or ((.value.headers // {}) | length) > 0) | .key' \
    "$HOME/.claude.json" | paste -sd, -)"
  [ -z "$left" ] || note "MCP servers left out (they need env or headers): $left"
fi

note "setup staged: $(du -sh "$out" | cut -f1)"
