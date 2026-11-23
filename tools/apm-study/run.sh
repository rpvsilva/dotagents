#!/usr/bin/env bash
# One APM trial: install a public Group for one tool at user scope in a throwaway $HOME, list what landed, run each
# installed hook once, print one result line. Never touches the real $HOME.
# Usage: tools/apm-study/run.sh <group> <claude|codex|cursor>
# Env: APM_VERSION (default below), KEEP=1 keeps the throwaway folder and prints it on stderr.
set -euo pipefail

APM_VERSION="${APM_VERSION:-0.36.1}"
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"

[ $# -eq 2 ] || { echo "usage: run.sh <group> <claude|codex|cursor>" >&2; exit 2; }
group="$1"
tool="$2"
case "$tool" in
  claude) skills_dir=".claude/skills" agents_dir=".claude/agents" cmds_dir=".claude/commands" hook_files=".claude/settings.json" ;;
  codex) skills_dir=".agents/skills" agents_dir=".codex/agents" cmds_dir=".codex/prompts" hook_files=".codex/hooks.json" ;;
  cursor) skills_dir=".agents/skills" agents_dir=".cursor/agents" cmds_dir=".cursor/commands" hook_files=".cursor/hooks.json" ;;
  *) echo "run.sh: unknown tool: $tool" >&2; exit 2 ;;
esac
bash "$repo/scripts/parts.sh" groups | cut -f1 | grep -vx -e core -e personal | grep -qx "$group" ||
  { echo "run.sh: not a public group: $group" >&2; exit 2; }

work="$(mktemp -d)"
[ -n "${KEEP:-}" ] && echo "run.sh: kept $work" >&2
[ -n "${KEEP:-}" ] || trap 'rm -rf "$work"' EXIT
real_home="$HOME"
export HOME="$work/home"
mkdir -p "$HOME"
unset APM_HOME XDG_CONFIG_HOME XDG_CACHE_HOME XDG_DATA_HOME
cd "$HOME"

result() { # status detail...
  printf 'RESULT group=%s tool=%s apm=%s %s\n' "$group" "$tool" "$APM_VERSION" "$*"
}

if ! python3 -m venv "$work/venv" || ! "$work/venv/bin/pip" install -q "apm-cli==$APM_VERSION" >"$work/pip.log" 2>&1; then
  result "install=blocked reason=apm-not-installable"
  exit 3
fi
apm="$work/venv/bin/apm"

python3 -I "$here/gen.py" --out "$work/out" --group "$group" >"$work/gen.log"
# APM copies a local package before resolving its dependencies, so a `../x` path dependency points nowhere: use
# absolute paths in this throwaway copy (the study records that as a breakage).
sed -i.bak "s#\"\\.\\./#\"$work/out/packages/#" "$work/out/packages/"*/apm.yml
rm -f "$work/out/packages/"*/apm.yml.bak

set +e
"$apm" install -g --target "$tool" "$work/out/packages/dotagents-$group" </dev/null >"$work/install.log" 2>&1
code=$?
set -e
warn="$(grep -c '^ *\[!\]' "$work/install.log" || true)"

count() { # dir -> number of entries
  if [ -d "$HOME/$1" ]; then find "$HOME/$1" -mindepth 1 -maxdepth 1 | wc -l | tr -d ' '; else echo 0; fi
}
n_skills="$(count "$skills_dir")"
n_agents="$(count "$agents_dir")"
n_cmds="$(count "$cmds_dir")"

# Expected skills and agents: the Group's closure without core (APM ships core as one package).
missing=""
while IFS=$'\t' read -r part kind _; do
  case "$kind" in
    skill) [ -e "$HOME/$skills_dir/$part/SKILL.md" ] || missing="$missing $part" ;;
    agent) ls "$HOME/$agents_dir/$part".* >/dev/null 2>&1 || missing="$missing $part" ;;
  esac
done < <(bash "$repo/scripts/parts.sh" closure "$group" | while read -r p; do
  bash "$repo/scripts/parts.sh" list | awk -F'\t' -v p="$p" '$1 == p'
done)
[ -e "$HOME/$skills_dir/dotagents-shared" ] || missing="$missing _shared"

# Hooks: every hook script or hook wiring file APM left in the tool's folders.
hooks_found=0 hooks_ok=0 hooks_failed=""
payload='{"tool_name":"Bash","tool_input":{"command":"echo hi"},"command":"echo hi","cwd":"/tmp"}'
while IFS= read -r script; do
  hooks_found=$((hooks_found + 1))
  if printf '%s' "$payload" | bash "$script" >/dev/null 2>"$work/hook.err"; then
    hooks_ok=$((hooks_ok + 1))
  else
    hooks_failed="$hooks_failed $(basename "$script")"
  fi
done < <(find "$HOME/.claude" "$HOME/.codex" "$HOME/.cursor" "$HOME/.agents" -path '*/hooks/*.sh' 2>/dev/null || true)
[ -f "$HOME/$hook_files" ] && grep -q hooks "$HOME/$hook_files" && hooks_found=$((hooks_found + 1))

result "exit=$code warnings=$warn skills=$n_skills agents=$n_agents commands=$n_cmds hooks=$hooks_found hooks_ok=$hooks_ok hooks_failed=[${hooks_failed# }] missing=[${missing# }]"
[ "$real_home" != "$HOME" ] || exit 1
