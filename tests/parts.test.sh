#!/usr/bin/env bash
# Runs scripts/parts.sh on a throwaway fixture repo (PARTS_REPO) and checks every subcommand: the parts it finds,
# their needs, the closure of a selection (core always in, optional needs only on request, cycles end) and the files
# each part ships. Then checks the real repo's groups.json through the script's own path.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PARTS="$REPO/scripts/parts.sh"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
fails=0

fail() { echo "FAIL: $*"; fails=$((fails + 1)); }

# Passes when "$1" equals the expected text "$2" (lines joined by newlines).
same() { [ "$1" = "$2" ] || fail "$3"$'\n'"  got:      $(printf '%s' "$1" | tr '\n\t' '|>')"$'\n'"  expected: $(printf '%s' "$2" | tr '\n\t' '|>')"; }

parts() { PARTS_REPO="$FIX" "$PARTS" "$@"; }

# The fixture: groups core, a, b. alpha (a) owns the agent alpha-agent, requires gamma and wants beta optionally;
# gamma requires alpha back (a cycle); beta (b) requires the hook guard and the agent alpha-agent; the mod runs (a)
# requires remote; mcp and global are folder parts; skills/_shared is core and has no declaration.
FIX="$SANDBOX/repo"
mk() { mkdir -p "$(dirname "$FIX/$1")"; printf '%s\n' "${2:-x}" >"$FIX/$1"; }
mk groups.json '{"core": {"about": "Always installed."}, "a": {"about": "Group a."}, "b": {"about": "Group b."}}'
mk skills/_shared/conventions.md
mk skills/configure/SKILL.md
mk skills/configure/needs.json '{"group": "core", "needs": {"beta": "optional"}}'
mk skills/alpha/SKILL.md
mk skills/alpha/alpha-agent-brief.md
mk skills/alpha/scripts/run.py
mk skills/alpha/needs.json '{"group": "a", "needs": {"gamma": "required", "beta": "optional"}, "agents": ["alpha-agent"]}'
mk skills/gamma/SKILL.md
mk skills/gamma/needs.json '{"group": "a", "needs": {"alpha": "required"}}'
mk skills/beta/SKILL.md
mk skills/beta/needs.json '{"group": "b", "needs": {"guard": "required", "alpha-agent": "required"}}'
mk adapters/claude/agents/alpha-agent.md
mk adapters/codex/agents/alpha-agent.toml
mk adapters/claude/agents/other-agent.md
mk scripts/hooks/guard.sh
mk scripts/hooks/guard.needs.json '{"group": "b", "needs": {}}'
mk scripts/hooks/lib.sh
mk adapters/claude/hooks/guard.json '{}'
mk adapters/claude/mods/runs/.claude-plugin/plugin.json '{"name": "runs"}'
mk adapters/claude/mods/runs/hooks/runs.ts
mk adapters/claude/mods/runs/needs.json '{"group": "a", "needs": {"remote": "required"}}'
mk remote/needs.json '{"group": "a", "needs": {}}'
mk remote/README.md
mk scripts/remote-agent.sh
mk scripts/remote-agent-setup.sh
mk scripts/remote-backends/docker.sh
mk scripts/settings.sh
mk mcp/needs.json '{"group": "b", "needs": {}}'
mk mcp/servers.json '{}'
mk global/needs.json '{"group": "b", "needs": {}}'
mk global/AGENTS.md
mk adapters/claude/settings/agent-identity.json '{}'
mk adapters/codex/rules/agent-identity.rules
mk adapters/claude/plugins.txt

T=$'\t'

# list: one line per part with its kind, group and declaration; an agent takes its owning skill's group.
same "$(parts list | sort)" "$(sort <<EOF
alpha${T}skill${T}a${T}skills/alpha/needs.json
alpha-agent${T}agent${T}a${T}skills/alpha/needs.json
beta${T}skill${T}b${T}skills/beta/needs.json
configure${T}skill${T}core${T}skills/configure/needs.json
gamma${T}skill${T}a${T}skills/gamma/needs.json
global${T}global${T}b${T}global/needs.json
guard${T}hook${T}b${T}scripts/hooks/guard.needs.json
mcp${T}mcp${T}b${T}mcp/needs.json
remote${T}remote${T}a${T}remote/needs.json
runs${T}mod${T}a${T}adapters/claude/mods/runs/needs.json
EOF
)" "list: wrong parts"

# groups: every group with its description, in file order.
same "$(parts groups)" "core${T}Always installed.
a${T}Group a.
b${T}Group b." "groups: wrong output"

# needs: the part's declared needs; none for a part that declares none.
same "$(parts needs alpha | sort)" "beta${T}optional
gamma${T}required" "needs alpha: wrong needs"
same "$(parts needs guard)" "" "needs guard: should be empty"

# owner: the skill whose declaration lists the agent.
same "$(parts owner alpha-agent)" "alpha" "owner: wrong skill"

# closure: required needs only, core always in, the alpha/gamma cycle ends.
same "$(parts closure gamma)" "_shared
alpha
alpha-agent
configure
gamma" "closure gamma: optional beta should stay out, core and the cycle's parts in"
same "$(parts closure --optional gamma)" "_shared
alpha
alpha-agent
beta
configure
gamma
guard" "closure --optional gamma: optional needs not added"
# An agent pulls its owning skill, and the skill its agents and required needs.
same "$(parts closure beta)" "_shared
alpha
alpha-agent
beta
configure
gamma
guard" "closure beta: agent's owner or its needs missing"
# A group expands to its parts.
same "$(parts closure b)" "_shared
alpha
alpha-agent
beta
configure
gamma
global
guard
mcp" "closure b: group not expanded"
same "$(parts closure runs)" "_shared
configure
remote
runs" "closure runs: mod's required need missing"
same "$(parts closure core)" "_shared
configure" "closure core: wrong parts"

# Unknown names: a message on stderr, exit 2, nothing on stdout.
for args in "closure nope" "closure alpha nope" "needs nope" "owner alpha" "files nope"; do
  status=0
  # shellcheck disable=SC2086 # split the subcommand from its argument
  out="$(parts $args 2>"$SANDBOX/err")" || status=$?
  [ "$status" -eq 2 ] || fail "$args: exit $status, expected 2"
  [ -z "$out" ] || fail "$args: printed to stdout: $out"
  grep -q nope "$SANDBOX/err" || grep -q alpha "$SANDBOX/err" || fail "$args: stderr does not name it"
done
status=0
parts bogus >/dev/null 2>&1 || status=$?
[ "$status" -eq 2 ] || fail "unknown subcommand: exit $status, expected 2"

# files: the repo paths each part ships.
same "$(parts files alpha)" "skills/alpha/SKILL.md
skills/alpha/alpha-agent-brief.md
skills/alpha/needs.json
skills/alpha/scripts/run.py" "files alpha: wrong skill folder files"
same "$(parts files _shared)" "skills/_shared/conventions.md" "files _shared: wrong"
same "$(parts files runs)" "adapters/claude/mods/runs/.claude-plugin/plugin.json
adapters/claude/mods/runs/hooks/runs.ts
adapters/claude/mods/runs/needs.json" "files runs: wrong mod folder files"
same "$(parts files guard)" "scripts/hooks/guard.sh
adapters/claude/hooks/guard.json" "files guard: script or existing snippets wrong"
same "$(parts files alpha-agent)" "adapters/claude/agents/alpha-agent.md
adapters/codex/agents/alpha-agent.toml" "files alpha-agent: wrong wrappers"
same "$(parts files remote)" "remote/README.md
remote/needs.json
scripts/remote-agent-setup.sh
scripts/remote-agent.sh
scripts/remote-backends/docker.sh" "files remote: wrong"
same "$(parts files mcp)" "mcp/servers.json" "files mcp: wrong"
same "$(parts files global)" "global/AGENTS.md
adapters/claude/settings/agent-identity.json
adapters/codex/rules/agent-identity.rules
adapters/claude/plugins.txt" "files global: wrong"

# A repo with no declarations has no parts, and the script never reads its stdin for them.
mkdir -p "$SANDBOX/empty"
same "$(echo '{"group": "core"}' | PARTS_REPO="$SANDBOX/empty" "$PARTS" list)" "" "empty repo: list read stdin"

# Without PARTS_REPO the script reads the repo it lives in: the real groups.json names the eight groups.
same "$("$PARTS" groups | cut -f1 | tr '\n' ' ')" "core remote pr-loop epics security safety-hooks small-tools personal " \
  "real repo: groups.json does not name the eight groups"
"$PARTS" groups | awk -F'\t' 'NF != 2 || $2 == "" { bad = 1 } END { exit bad || NR != 8 }' || fail "real repo: a group has no about line"

[ "$fails" -eq 0 ] || { echo "parts.test.sh: $fails failure(s)"; exit 1; }
echo "parts.test.sh: ok"
