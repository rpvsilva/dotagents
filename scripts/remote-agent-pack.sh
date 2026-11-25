#!/usr/bin/env bash
# Stages the parts of this machine's agent setup a remote sandbox needs into <dir>, laid out like $HOME.
# Usage: scripts/remote-agent-pack.sh <dir> [home]   (home: where the setup is read from, default $HOME)
# Copies, following links: ~/.claude/{CLAUDE.md,skills,agents,commands,rules}, ~/.codex/{AGENTS.md,agents,prompts}
# (Codex's own pieces, for a run on the codex harness; never its config.toml or auth.json),
# ~/.config/opencode/{AGENTS.md,agents,commands} (OpenCode's, for the opencode harness; never its opencode.json(c),
# which may hold keys), ~/.pi/agent/{AGENTS.md,skills,prompts} (Pi's, for the pi harness; never its auth.json,
# models.json, settings.json or extensions) and ~/.agents/{skills,hooks}, and
# this repo's scripts/capped as ~/.agents/bin/capped, which the sandbox runs the agent's shell commands under.
# A ~/.claude/skills/<name> that is the same folder as ~/.agents/skills/<name> is staged as a relative link to that
# copy; ~/.claude/skills/synced (the account's claude.ai skills) and Python caches (__pycache__, *.pyc) are left out:
# together they were most of the setup's 25 MB, sent to every run (#564).
# Writes a filtered ~/.claude/settings.json (hooks that call ~/.agents/hooks, permissions, model, enabled
# plugins and their GitHub-hosted marketplaces) and ~/.claude/remote-mcp.json (MCP servers with no env or
# headers, which could hold secrets). Never copies credentials, history, transcripts, caches or settings env.
# Prints what it left out to stderr.
set -euo pipefail

# shellcheck source=hooks/lib.sh disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/hooks/lib.sh"

out="${1:?usage: $0 <dir> [home]}"
home="${2:-$HOME}"
mkdir -p "$out/.claude" "$out/.codex" "$out/.config/opencode" "$out/.pi/agent" "$out/.agents"
note() { echo "remote-agent-pack: $*" >&2; }

real() { (cd -P "$1" 2>/dev/null && pwd); }
for p in .claude/CLAUDE.md .claude/agents .claude/commands .claude/rules .codex/AGENTS.md .codex/agents .codex/prompts \
  .config/opencode/AGENTS.md .config/opencode/agents .config/opencode/commands .pi/agent/AGENTS.md .pi/agent/skills \
  .pi/agent/prompts .agents/skills .agents/hooks; do
  [ -e "$home/$p" ] || continue
  cp -RL "$home/$p" "$out/$p" 2>/dev/null || note "some of ~/$p could not be copied (broken links?)"
done
# ~/.claude/skills: a skill that is the same folder as ~/.agents/skills/<name> (install.sh links it there) becomes a
# relative link to that copy, not a second one; `synced`, Claude's copy of the account's claude.ai skills, stays here.
if [ -d "$home/.claude/skills" ]; then
  mkdir -p "$out/.claude/skills"
  for s in "$home/.claude/skills"/* "$home/.claude/skills"/.[!.]*; do
    [ -e "$s" ] || continue
    name="$(basename "$s")"
    case "$name" in synced | .DS_Store) continue ;; esac
    if [ -d "$home/.agents/skills/$name" ] && [ "$(real "$s")" = "$(real "$home/.agents/skills/$name")" ]; then
      ln -s "../../.agents/skills/$name" "$out/.claude/skills/$name"
    else
      cp -RL "$s" "$out/.claude/skills/$name" 2>/dev/null || note "some of ~/.claude/skills/$name could not be copied (broken links?)"
    fi
  done
fi

# Secret-looking files never leave the machine, wherever they sit.
while IFS= read -r -d '' f; do
  if is_secret "${f#"$out"/}"; then rm -f "$f"; note "left out ${f#"$out"/} (looks like a secret)"; fi
done < <(find "$out" -type f -print0)
find "$out" -name .DS_Store -delete
# Python's caches: rebuilt where the scripts run, and most of a skill folder's size.
find "$out" -name __pycache__ -type d -prune -exec rm -rf {} +
find "$out" -name '*.pyc' -type f -delete

mkdir -p "$out/.agents/bin"
cp "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/capped" "$out/.agents/bin/capped"
chmod +x "$out/.agents/bin/capped"

settings="$home/.claude/settings.json"
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

if [ -f "$home/.claude.json" ]; then
  jq '.mcpServers // {} | with_entries(select(((.value.env // {}) | length) == 0 and ((.value.headers // {}) | length) == 0))' \
    "$home/.claude.json" >"$out/.claude/remote-mcp.json"
  left="$(jq -r '.mcpServers // {} | to_entries[] | select(((.value.env // {}) | length) > 0 or ((.value.headers // {}) | length) > 0) | .key' \
    "$home/.claude.json" | paste -sd, -)"
  [ -z "$left" ] || note "MCP servers left out (they need env or headers): $left"
fi

note "setup staged: $(du -sh "$out" | cut -f1)"
