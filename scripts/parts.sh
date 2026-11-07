#!/usr/bin/env bash
# Answers what parts of the setup exist and what each needs (GLOSSARY.md: Part, Group, Need, Core). install.sh,
# uninstall.sh and tests/repo.test.sh read the parts only through it.
# Usage: parts.sh list
#        parts.sh groups
#        parts.sh needs <part>
#        parts.sh owner <agent>
#        parts.sh closure [--optional] <group|part>...
#        parts.sh files <part>
# A part declares its group and needs in a needs.json beside it: {"group": "<g>", "needs": {"<part>": "required" |
# "optional"}, "agents": ["<stem>", ...]}. Declarations: skills/<skill>/needs.json (not skills/_*),
# adapters/claude/mods/<mod>/needs.json, scripts/hooks/<hook>.needs.json, and the folder parts remote/needs.json,
# mcp/needs.json, global/needs.json. A skill's `agents` names the subagents whose briefs it holds: each is a part of
# kind agent, in the skill's group. Groups are named once in groups.json, each {"about": "<one line>"}.
# list prints `name TAB kind TAB group TAB declaration` (kinds skill, agent, mod, hook, remote, mcp, global); groups
# prints `name TAB about`; needs prints the declared `name TAB required|optional`; owner prints the agent's skill.
# closure prints, one per line and sorted, the selection (a group stands for its parts), the core parts (`_shared`
# and every part of group core) and, transitively, every required need, with --optional every optional one too; an
# agent requires its owning skill and a skill its agents. files prints the repo-relative files a part ships.
# `_shared` is core and has no declaration: list leaves it out, closure always has it, files gives its folder.
# The repo is the one holding this script, or $PARTS_REPO. Exit 2 on an unknown subcommand, part or group.
# shellcheck disable=SC2016 # the single-quoted strings are jq programs, expanded by jq
set -euo pipefail

src="${BASH_SOURCE[0]}"
while [ -L "$src" ]; do
  dir="$(cd "$(dirname "$src")" && pwd)"
  src="$(readlink "$src")"
  [[ "$src" == /* ]] || src="$dir/$src"
done
REPO="${PARTS_REPO:-$(cd "$(dirname "$src")/.." && pwd)}"
cd "$REPO"
# Globs and sort in byte order, the same on every machine.
LC_ALL=C
export LC_ALL

usage() { grep -E "^# +(Usage: )?parts.sh " "$src" | sed -E "s/^# +(Usage: )?/usage: /" >&2; exit 2; }

cmd="${1:-}"
[ $# -eq 0 ] || shift

shopt -s nullglob
decls=()
for f in skills/*/needs.json adapters/claude/mods/*/needs.json scripts/hooks/*.needs.json \
  remote/needs.json mcp/needs.json global/needs.json; do
  case "$f" in skills/_*) continue ;; esac
  [ ! -f "$f" ] || decls+=("$f")
done
# With no file to read, jq would read the declarations from stdin.
[ ${#decls[@]} -gt 0 ] || decls=(/dev/null)

# One jq run reads every declaration and groups.json and answers the command; errors go to stderr with exit 2.
PARTS_JQ='
def kindname($f):
  if ($f | startswith("skills/")) then {name: ($f | split("/")[1]), kind: "skill"}
  elif ($f | startswith("adapters/claude/mods/")) then {name: ($f | split("/")[3]), kind: "mod"}
  elif ($f | startswith("scripts/hooks/")) then {name: ($f | ltrimstr("scripts/hooks/") | rtrimstr(".needs.json")), kind: "hook"}
  else ($f | split("/")[0]) as $n | {name: $n, kind: $n} end;
def fail($msg): "parts: \($msg)\n" | halt_error(2);
($groups[0] // {}) as $groups
| [inputs as $d | input_filename as $f
    | kindname($f) + {group: $d.group, needs: ($d.needs // {}), agents: ($d.agents // []), decl: $f}] as $decls
| ($decls + [$decls[] | select(.kind == "skill") as $s | $s.agents[]
    | {name: ., kind: "agent", group: $s.group, needs: {}, agents: [], decl: $s.decl, owner: $s.name}]) as $parts
| ($parts | map({key: .name, value: .}) | from_entries) as $by
| def part($n): $by[$n] // fail("unknown part: \($n)");
  def edges($opt): (.needs | to_entries[] | select(.value == "required" or $opt) | .key),
    (.owner // empty), .agents[];
  def closure($opt):
    def grow: (. + [.[] as $n | $by[$n] // empty | edges($opt)] | unique) as $next
      | if $next == . then . else $next | grow end;
    grow;
  if $cmd == "list" then $parts[] | [.name, .kind, .group, .decl] | join("\t")
  elif $cmd == "groups" then $groups | to_entries[] | "\(.key)\t\(.value.about)"
  elif $cmd == "needs" then part($ARGS.positional[0]).needs | to_entries[] | "\(.key)\t\(.value)"
  elif $cmd == "owner" then part($ARGS.positional[0]).owner // fail("not an agent: \($ARGS.positional[0])")
  elif $cmd == "kind" then if $ARGS.positional[0] == "_shared" then "skill" else part($ARGS.positional[0]).kind end
  elif $cmd == "closure" then
    [$ARGS.positional[] as $w
        | if $groups | has($w) then ($parts[] | select(.group == $w) | .name)
          elif $w == "_shared" or ($by | has($w)) then $w
          else fail("unknown part or group: \($w)") end]
    | . + ["_shared"] + [$parts[] | select(.group == "core") | .name]
    | unique | closure($optional)[]
  else fail("unknown command: \($cmd)") end
'
answer() {
  local cmd="$1" groups=(--argjson groups '[{}]')
  shift
  [ ! -f groups.json ] || groups=(--slurpfile groups groups.json)
  jq -nr --arg cmd "$cmd" --argjson optional "$optional" "${groups[@]}" "$PARTS_JQ" "${decls[@]}" --args "$@" \
    | tr -d '\r'
}

# Prints each existing file under the folders and paths given, in byte order, leaving out Python caches.
ship() { find "$@" -type f ! -path '*/__pycache__/*' 2>/dev/null | sort; }

optional=false
case "$cmd" in
  list | groups) [ $# -eq 0 ] || usage; answer "$cmd" ;;
  needs | owner) [ $# -eq 1 ] || usage; answer "$cmd" "$1" ;;
  closure)
    if [ "${1:-}" = --optional ]; then optional=true; shift; fi
    [ $# -ge 1 ] || usage
    # jq would read a word starting with - as its own option.
    for w in "$@"; do case "$w" in -*) echo "parts: unknown part or group: $w" >&2; exit 2 ;; esac; done
    answer closure "$@"
    ;;
  files)
    [ $# -eq 1 ] || usage
    part="$1"
    kind="$(answer kind "$part")"
    case "$kind" in
      skill) ship "skills/$part" ;;
      mod) ship "adapters/claude/mods/$part" ;;
      hook) for f in "scripts/hooks/$part.sh" adapters/*/hooks/"$part.json"; do [ ! -f "$f" ] || echo "$f"; done ;;
      agent) for f in adapters/*/agents/"$part".*; do echo "$f"; done ;;
      remote) ship remote; for f in scripts/remote-*.sh scripts/remote-backends/*; do echo "$f"; done ;;
      mcp) [ ! -f mcp/servers.json ] || echo mcp/servers.json ;;
      global)
        for f in global/AGENTS.md adapters/claude/settings/*.json adapters/codex/rules/*.rules \
          adapters/claude/plugins.txt; do
          [ ! -f "$f" ] || echo "$f"
        done
        ;;
    esac
    ;;
  *) usage ;;
esac
