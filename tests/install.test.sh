#!/usr/bin/env bash
# Runs install.sh against a throwaway $HOME and checks the links it leaves behind.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
fails=0

fail() { echo "FAIL: $*"; fails=$((fails + 1)); }

assert_link() {
  local dest="$1" want="$2" got
  if [ ! -L "$dest" ]; then fail "$dest is not a symlink"; return; fi
  got="$(readlink "$dest")"
  [ "$got" = "$want" ] || fail "$dest -> $got, want $want"
}

# Prints the target of every symlink under $1, so two runs can be compared: the sorted paths, then their targets in
# the same order, from one readlink for all (one each took seconds).
snapshot() {
  find "$1" -type l | sort >"$SANDBOX/links"
  cat "$SANDBOX/links"
  tr '\n' '\0' <"$SANDBOX/links" | xargs -0 readlink
}

run() { HOME="$1" DOTAGENTS_SKIP_PLUGINS=1 "$REPO/install.sh" "${@:2}"; }

# Prints every symlink under $1 as `<path relative to $1> -> <target>`, sorted.
links() {
  local p
  find "$1" -type l ! -path '*/.dotagents-backup/*' | LC_ALL=C sort | while IFS= read -r p; do
    echo "${p#"$1"/} -> $(readlink "$p")"
  done
}

# links "$1" with the checkout path $2 written REPO and the home $1 written HOME, to compare installs across paths.
portable() {
  local l
  links "$1" | while IFS= read -r l; do
    l="${l//"$2"/REPO}"
    echo "${l//"$1"/HOME}"
  done
}

# Prints every regular file under $1 (backups aside) with its content, so two runs can be compared.
configs() {
  local f
  find "$1" -type f ! -path '*/.dotagents-backup/*' | LC_ALL=C sort | while IFS= read -r f; do
    echo "== ${f#"$1"/}"
    cat "$f"
  done
}

# Prints the link paths under $1 whose name is in folder $2, one per line, sorted: `.claude/agents` gives the stems.
names_in() { links "$1" | sed -n "s|^$2/\([^/.]*\)[^ /]* -> .*|\1|p" | LC_ALL=C sort -u | tr '\n' ' '; }

skills=()
for d in "$REPO"/skills/*/; do skills+=("$(basename "$d")"); done

# ~/.agents/hooks is a real folder of per-file links: lib.sh, secret-patterns.txt and each hook script.
assert_hooks() {
  local h="$1" f
  [ -d "$h/.agents/hooks" ] && [ ! -L "$h/.agents/hooks" ] || { fail "$h/.agents/hooks is not a real folder"; return; }
  for f in "$REPO"/scripts/hooks/*.sh "$REPO/scripts/hooks/secret-patterns.txt"; do
    assert_link "$h/.agents/hooks/${f##*/}" "$f"
  done
}

# The single-tool targets get homes of their own, installed in the background while the all target runs; the
# assertions on them come after `wait`. Each step's output lands in $SANDBOX/<step>.out.
(
  home="$SANDBOX/claude"
  mkdir -p "$home"
  run "$home" claude >"$SANDBOX/claude.out"
  [ ! -e "$home/.codex" ] || touch "$SANDBOX/claude-touched-codex"
  run "$home" codex >"$SANDBOX/codex.out"
  run "$home" cursor >"$SANDBOX/cursor.out"
  home="$SANDBOX/shared"
  mkdir -p "$home"
  run "$home" shared >"$SANDBOX/shared.out"
  # A home from before per-file hook links: ~/.agents/hooks is one link to the repo folder.
  home="$SANDBOX/migrate"
  mkdir -p "$home/.agents"
  ln -s "$REPO/scripts/hooks" "$home/.agents/hooks"
  run "$home" shared >"$SANDBOX/migrate.out"
  # A home from before agent-flow replaced remote-runs: a link to the mod folder this checkout no longer has, and a
  # dangling link that is not ours.
  home="$SANDBOX/stalemod"
  mkdir -p "$home/.claude/skills"
  ln -s "$REPO/adapters/claude/mods/remote-runs" "$home/.claude/skills/remote-runs"
  ln -s "$SANDBOX/nowhere" "$home/.claude/skills/foreign"
  run "$home" claude agent-flow >"$SANDBOX/stalemod.out"
  if run "$SANDBOX/bad" nope >/dev/null 2>&1; then touch "$SANDBOX/bad-exited-0"; fi
) &
single=$!

# Hook migrations and checkout paths, each in its own home. oldfull: main's layout (a full install whose
# ~/.agents/hooks is one link to the repo folder) then a selection that names no hook. foreign: ~/.agents/hooks is a
# link to a folder outside the repo. space: install.sh all from a copy of the checkout at a path with a space.
(
  home="$SANDBOX/oldfull"
  mkdir -p "$home/.claude" "$home/.codex" "$home/.cursor"
  run "$home" all >/dev/null
  rm -rf "$home/.agents/hooks"
  ln -s "$REPO/scripts/hooks" "$home/.agents/hooks"
  run "$home" all small-tools >"$SANDBOX/oldfull.out"
  home="$SANDBOX/foreign"
  mkdir -p "$home/.agents" "$SANDBOX/elsewhere"
  echo mine >"$SANDBOX/elsewhere/mine.sh"
  ln -s "$SANDBOX/elsewhere" "$home/.agents/hooks"
  run "$home" shared >"$SANDBOX/foreign.out"
  mkdir -p "$SANDBOX/sp pr"
  cp -R "$REPO" "$SANDBOX/sp pr/repo"
  home="$SANDBOX/space home"
  mkdir -p "$home/.claude" "$home/.codex" "$home/.cursor"
  HOME="$home" DOTAGENTS_SKIP_PLUGINS=1 "$SANDBOX/sp pr/repo/install.sh" all >"$SANDBOX/space.out"
  portable "$home" "$SANDBOX/sp pr/repo" >"$SANDBOX/space.links"
  # Two checkouts: A (this repo) installed everything, then B (the copy) installs small-tools, which names no
  # safety hook. otherfold: A's ~/.agents/hooks is the old folder link into A. otherfile: per-file links into A, and
  # Claude's config also calls a hook no checkout has.
  for h in otherfold otherfile; do
    home="$SANDBOX/$h"
    mkdir -p "$home/.claude" "$home/.codex" "$home/.cursor"
    run "$home" all >/dev/null
    if [ "$h" = otherfold ]; then
      rm -rf "$home/.agents/hooks"
      ln -s "$REPO/scripts/hooks" "$home/.agents/hooks"
    else
      jq '.hooks.Stop += [{"hooks": [{"type": "command", "command": "~/.agents/hooks/gone.sh"}]}]' \
        "$home/.claude/settings.json" >"$SANDBOX/gone.json"
      mv "$SANDBOX/gone.json" "$home/.claude/settings.json"
    fi
    HOME="$home" DOTAGENTS_SKIP_PLUGINS=1 "$SANDBOX/sp pr/repo/install.sh" all small-tools >"$SANDBOX/$h.out"
  done
) &
migrations=$!

# Selective installs, each in a home with every tool's folder: remote alone, twice; then safety-hooks and pr-loop
# added on top; and claude with epics. Each step's links land in $SANDBOX/<step>.links.
(
  home="$SANDBOX/remote"
  mkdir -p "$home/.claude" "$home/.codex" "$home/.cursor"
  echo '{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "orca"}]}]}}' >"$home/.claude/settings.json"
  run "$home" remote >"$SANDBOX/remote.out"
  links "$home" >"$SANDBOX/remote.links"
  configs "$home" >"$SANDBOX/remote.configs"
  run "$home" remote >"$SANDBOX/remote-again.out"
  links "$home" >"$SANDBOX/remote-again.links"
  configs "$home" >"$SANDBOX/remote-again.configs"
  run "$home" safety-hooks >"$SANDBOX/safety.out"
  links "$home" >"$SANDBOX/safety.links"
  configs "$home" >"$SANDBOX/safety.configs"
  run "$home" pr-loop >"$SANDBOX/pr-loop.out"
  links "$home" >"$SANDBOX/pr-loop.links"
  home="$SANDBOX/epics"
  mkdir -p "$home/.claude" "$home/.codex" "$home/.cursor"
  run "$home" epics claude >"$SANDBOX/epics.out"
  links "$home" >"$SANDBOX/epics.links"
  home="$SANDBOX/codex-epics"
  mkdir -p "$home/.codex"
  run "$home" epics codex >/dev/null
  links "$home" >"$SANDBOX/codex-epics.links"
  home="$SANDBOX/badsel"
  mkdir -p "$home/.claude"
  if run "$home" remote nope >"$SANDBOX/badsel.out" 2>&1; then touch "$SANDBOX/badsel-exited-0"; fi
  links "$home" >"$SANDBOX/badsel.links"
  home="$SANDBOX/twotools"
  mkdir -p "$home/.claude" "$home/.codex"
  if run "$home" claude codex remote >"$SANDBOX/twotools.out" 2>&1; then touch "$SANDBOX/twotools-exited-0"; fi
  links "$home" >"$SANDBOX/twotools.links"
) &
selective=$!

# all: a pre-existing real file is backed up, then everything is linked. Cursor's hooks.json already holds another
# tool's hook, which the merge keeps.
home="$SANDBOX/all"
mkdir -p "$home/.claude" "$home/.codex" "$home/.cursor"
echo "old instructions" >"$home/.claude/CLAUDE.md"
echo '{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "orca"}]}]}}' >"$home/.cursor/hooks.json"
run "$home" all >/dev/null

for s in "${skills[@]}"; do
  assert_link "$home/.agents/skills/$s" "$REPO/skills/$s"
  assert_link "$home/.claude/skills/$s" "$home/.agents/skills/$s"
  [[ "$s" == _* ]] || [ -f "$home/.claude/skills/$s/SKILL.md" ] || fail "$s/SKILL.md not reachable through ~/.claude/skills"
done
# Claude mods (a folder with .claude-plugin/plugin.json) sit beside the skills: Claude Code loads each as a plugin.
mods=0
for m in "$REPO"/adapters/claude/mods/*/; do
  [ -f "$m.claude-plugin/plugin.json" ] || continue
  m="${m%/}"
  assert_link "$home/.claude/skills/${m##*/}" "$m"
  mods=$((mods + 1))
done
[ "$mods" -gt 0 ] || fail "no mod found under adapters/claude/mods"
assert_hooks "$home"
assert_link "$home/.agents/bin/remote-agent" "$REPO/scripts/remote-agent.sh"
assert_link "$home/.agents/bin/settings" "$REPO/scripts/settings.sh"
assert_link "$home/.agents/bin/capped" "$REPO/scripts/capped"
assert_link "$home/.agents/bin/build-epic" "$REPO/adapters/claude/build-epic/launch.sh"
assert_link "$home/.claude/CLAUDE.md" "$REPO/global/AGENTS.md"
assert_link "$home/.codex/AGENTS.md" "$REPO/global/AGENTS.md"
for a in "$REPO"/adapters/claude/agents/*.md; do assert_link "$home/.claude/agents/$(basename "$a")" "$a"; done
for a in "$REPO"/adapters/codex/agents/*.toml; do assert_link "$home/.codex/agents/$(basename "$a")" "$a"; done
for r in "$REPO"/adapters/codex/rules/*.rules; do assert_link "$home/.codex/rules/$(basename "$r")" "$r"; done
for a in "$REPO"/adapters/cursor/agents/*.md; do assert_link "$home/.cursor/agents/$(basename "$a")" "$a"; done
# The guards source lib.sh (and through it secret-patterns.txt) beside themselves: the per-file links carry them.
code=0
"$home/.agents/hooks/guard-secrets.sh" 'cat .env' 2>/dev/null || code=$?
[ "$code" -eq 2 ] || fail "guard-secrets through ~/.agents/hooks: wanted exit 2 on cat .env, got $code"
"$home/.agents/hooks/guard-secrets.sh" 'cat src/app.ts' || fail "guard-secrets through ~/.agents/hooks blocked a plain file"
git init -q "$SANDBOX/gated" && touch "$SANDBOX/gated/.review-gate.yml"
code=0
(cd "$SANDBOX/gated" && "$home/.agents/hooks/guard-merge.sh" 'gh pr merge 1' 2>/dev/null) || code=$?
[ "$code" -eq 2 ] || fail "guard-merge through ~/.agents/hooks: wanted exit 2 on gh pr merge, got $code"
grep -rqx "old instructions" "$home/.dotagents-backup" 2>/dev/null || fail "replaced CLAUDE.md was not backed up"

# Second run, with no target (the default, all): same links, nothing backed up, every tool installed again.
before="$(snapshot "$home")"
out="$(run "$home")"
[ "$before" = "$(snapshot "$home")" ] || fail "second run changed the links"
if grep -q "backed up" <<<"$out"; then fail "second run backed something up"; fi
for t in shared claude codex cursor; do grep -qx "$t" <<<"$out" || fail "install.sh with no target did not install $t"; done

# Hook and settings snippets are merged into each tool's config, next to what is already there, and the second run
# doubles none of them.
has_guard() {
  local g
  for g in guard-merge guard-secrets; do
    # Once per event: Cursor runs guard-secrets on two.
    jq -e --arg g "$g" '[.hooks[] | [.. | .command? | strings | select(test($g))] | length] | max == 1' "$1" >/dev/null || fail "$1: $g hook missing or doubled"
  done
}
has_guard "$home/.claude/settings.json"
has_guard "$home/.codex/hooks.json"
has_guard "$home/.cursor/hooks.json"
jq -e '[.hooks.SessionEnd[]? | .. | .command? | strings | select(test("telemetry-save"))] | length == 1' "$home/.claude/settings.json" >/dev/null || fail "Claude SessionEnd telemetry-save hook missing or doubled"
jq -e '.hooks.Stop[0].hooks[0].command == "orca"' "$home/.cursor/hooks.json" >/dev/null || fail "existing Cursor hook lost"
# Each merged Cursor guard, run as Cursor runs it (a shell, the event's JSON on stdin): prints the allow Cursor needs
# when the guard passes, and on a block exits non-zero with no allow.
cursor_hook() {
  local cmd="$1" event="$2" json="$3" want="$4" out code=0
  out="$(HOME="$home" sh -c "$cmd" <<<"$json" 2>/dev/null)" || code=$?
  if [ "$want" = allow ]; then
    [ "$code" -eq 0 ] && [ "$out" = '{"permission":"allow"}' ] || fail "Cursor $event hook did not allow $json: exit $code, $out"
  else
    [ "$code" -ne 0 ] && [ -z "$out" ] || fail "Cursor $event hook did not block $json: exit $code, $out"
  fi
}
cursor_hooks=0
while IFS=$'\t' read -r event cmd; do
  cursor_hooks=$((cursor_hooks + 1))
  case "$event:$cmd" in
    beforeShellExecution:*guard-merge*)
      cursor_hook "$cmd" "$event" "$(jq -nc --arg d "$SANDBOX/gated" '{command: "git status", cwd: $d}')" allow
      cursor_hook "$cmd" "$event" "$(jq -nc --arg d "$SANDBOX/gated" '{command: "gh pr merge 1", cwd: $d}')" block ;;
    beforeShellExecution:*guard-secrets*)
      cursor_hook "$cmd" "$event" '{"command": "cat src/app.ts"}' allow
      cursor_hook "$cmd" "$event" '{"command": "cat .env"}' block ;;
    beforeReadFile:*guard-secrets*)
      cursor_hook "$cmd" "$event" '{"file_path": "/repo/src/app.ts"}' allow
      cursor_hook "$cmd" "$event" '{"file_path": "/repo/.env"}' block ;;
    *) fail "Cursor hook not checked: $event: $cmd" ;;
  esac
done < <(jq -r '.hooks | to_entries[] | .key as $e | .value[] | select((.command // "") | test("guard-")) | [$e, .command] | @tsv' \
  "$home/.cursor/hooks.json")
[ "$cursor_hooks" -eq 3 ] || fail "wanted 3 Cursor guard hooks, found $cursor_hooks"
jq -e '.permissions.deny | index("Bash(gh auth token:*)") != null' "$home/.claude/settings.json" >/dev/null || fail "Claude deny rules not merged"

# MCP servers without variables reach Codex and Cursor even with no secrets file.
remote="$(jq -r '[.mcpServers | to_entries[] | select(tojson | test("\\$\\{") | not) | .key][0] // empty' "$REPO/mcp/servers.json")"
if [ -n "$remote" ]; then
  grep -qxF "[mcp_servers.\"$remote\"]" "$home/.codex/config.toml" || fail "MCP server $remote not in Codex config"
  jq -e --arg n "$remote" '.mcpServers[$n]' "$home/.cursor/mcp.json" >/dev/null || fail "MCP server $remote not in Cursor config"
fi

portable "$home" "$REPO" >"$SANDBOX/all.links"

wait "$single" || fail "a single-tool install failed"

# claude: no codex links; codex and cursor: skipped cleanly when their folder is missing.
home="$SANDBOX/claude"
assert_link "$home/.claude/CLAUDE.md" "$REPO/global/AGENTS.md"
assert_link "$home/.claude/skills/agent-flow" "$REPO/adapters/claude/mods/agent-flow"
[ ! -e "$SANDBOX/claude-touched-codex" ] || fail "install.sh claude touched ~/.codex"
grep -q "skipping" "$SANDBOX/codex.out" || fail "codex without ~/.codex did not skip"
grep -q "skipping" "$SANDBOX/cursor.out" || fail "cursor without ~/.cursor did not skip"

# shared: ~/.agents only, no tool's folder.
home="$SANDBOX/shared"
assert_link "$home/.agents/skills/${skills[0]}" "$REPO/skills/${skills[0]}"
assert_hooks "$home"
for d in .claude .codex .cursor; do [ ! -e "$home/$d" ] || fail "install.sh shared created ~/$d"; done

# migrate: the old folder link is replaced by per-file links, and nothing was linked or moved into the repo through it.
home="$SANDBOX/migrate"
assert_hooks "$home"
[ -z "$(find "$REPO/scripts/hooks" -type l)" ] || fail "install.sh linked into the repo through the old ~/.agents/hooks link"
if grep -q "backed up" "$SANDBOX/migrate.out"; then fail "replacing the old ~/.agents/hooks link backed something up"; fi

# stalemod: the link to a mod gone from this checkout is removed and said; a dangling link not ours stays.
home="$SANDBOX/stalemod"
[ ! -L "$home/.claude/skills/remote-runs" ] || fail "install.sh kept the stale remote-runs link"
grep -q "removed stale .*remote-runs" "$SANDBOX/stalemod.out" || fail "install.sh did not say it removed the stale link: $(cat "$SANDBOX/stalemod.out")"
[ -L "$home/.claude/skills/foreign" ] || fail "install.sh removed a dangling link that is not ours"
assert_link "$home/.claude/skills/agent-flow" "$REPO/adapters/claude/mods/agent-flow"

wait "$migrations" || fail "a migration or space-path install failed"

# oldfull: every hook script a merged config calls is linked, though small-tools names no safety hook.
home="$SANDBOX/oldfull"
[ -d "$home/.agents/hooks" ] && [ ! -L "$home/.agents/hooks" ] || fail "oldfull: ~/.agents/hooks is not a real folder"
called="$(cat "$home/.claude/settings.json" "$home/.codex/hooks.json" "$home/.cursor/hooks.json" |
  grep -o '\.agents/hooks/[A-Za-z0-9_-]*\.sh' | sed 's|.*/||' | sort -u)"
for h in guard-merge guard-secrets allow-task-cleanup; do grep -qx "$h.sh" <<<"$called" || fail "oldfull: no config calls $h"; done
for h in $called; do assert_link "$home/.agents/hooks/$h" "$REPO/scripts/hooks/$h"; done
[ -z "$(find "$REPO/scripts/hooks" -type l)" ] || fail "install.sh linked into the repo through the old ~/.agents/hooks link"

# foreign: a ~/.agents/hooks link to a folder outside the repo is backed up, and nothing is linked into that folder.
home="$SANDBOX/foreign"
assert_hooks "$home"
[ "$(ls "$SANDBOX/elsewhere")" = mine.sh ] || fail "install.sh linked into a foreign ~/.agents/hooks folder: $(ls "$SANDBOX/elsewhere")"
grep -q "backed up $home/.agents/hooks" "$SANDBOX/foreign.out" || fail "a foreign ~/.agents/hooks link was not backed up: $(cat "$SANDBOX/foreign.out")"
find "$home/.dotagents-backup" -type l -name '*hooks' 2>/dev/null | grep -q . || fail "the foreign ~/.agents/hooks link is not in the backup"

# otherfold, otherfile: install from checkout B relinks every hook a config calls to B's script, and each runs.
for h in otherfold otherfile; do
  home="$SANDBOX/$h"
  b="$SANDBOX/sp pr/repo/scripts/hooks"
  called="$(cat "$home/.claude/settings.json" "$home/.codex/hooks.json" "$home/.cursor/hooks.json" |
    grep -o '\.agents/hooks/[A-Za-z0-9_-]*\.sh' | sed 's|.*/||' | grep -vx gone.sh | sort -u)"
  for g in guard-merge guard-secrets allow-task-cleanup; do grep -qx "$g.sh" <<<"$called" || fail "$h: no config calls $g"; done
  for g in $called; do
    assert_link "$home/.agents/hooks/$g" "$b/$g"
    [ -x "$home/.agents/hooks/$g" ] || fail "$h: called hook $g does not run"
  done
  code=0
  "$home/.agents/hooks/guard-secrets.sh" 'cat .env' 2>/dev/null || code=$?
  [ "$code" -eq 2 ] || fail "$h: guard-secrets after B's install: wanted exit 2 on cat .env, got $code"
done
grep -q "backed up $SANDBOX/otherfold/.agents/hooks" "$SANDBOX/otherfold.out" ||
  fail "otherfold: A's ~/.agents/hooks folder link was not backed up: $(cat "$SANDBOX/otherfold.out")"
grep -q "gone\.sh.*skipped" "$SANDBOX/otherfile.out" ||
  fail "otherfile: a called hook the checkout lacks was not named: $(cat "$SANDBOX/otherfile.out")"
[ ! -e "$SANDBOX/otherfile/.agents/hooks/gone.sh" ] || fail "otherfile: linked a hook the checkout lacks"

# space: a checkout path with a space links the same as one without.
[ "$(cat "$SANDBOX/space.links")" = "$(cat "$SANDBOX/all.links")" ] ||
  fail "install.sh from a path with a space linked otherwise: $(diff "$SANDBOX/all.links" "$SANDBOX/space.links")"

wait "$selective" || fail "a selective install failed"

# remote: exactly the remote parts plus core; no other skill, agent, hook, instructions or config.
want_remote=".agents/bin/capped -> $REPO/scripts/capped
.agents/bin/remote-agent -> $REPO/scripts/remote-agent.sh
.agents/bin/settings -> $REPO/scripts/settings.sh
.agents/hooks/lib.sh -> $REPO/scripts/hooks/lib.sh
.agents/hooks/secret-patterns.txt -> $REPO/scripts/hooks/secret-patterns.txt
.agents/skills/_shared -> $REPO/skills/_shared
.agents/skills/configure -> $REPO/skills/configure
.agents/skills/remote-subagents -> $REPO/skills/remote-subagents
.claude/skills/_shared -> $SANDBOX/remote/.agents/skills/_shared
.claude/skills/configure -> $SANDBOX/remote/.agents/skills/configure
.claude/skills/remote-subagents -> $SANDBOX/remote/.agents/skills/remote-subagents"
[ "$(cat "$SANDBOX/remote.links")" = "$want_remote" ] ||
  fail "install.sh remote linked: $(diff <(echo "$want_remote") "$SANDBOX/remote.links")"
for t in shared claude codex cursor; do grep -qx "$t" "$SANDBOX/remote.out" || fail "install.sh remote did not print section $t"; done
grep -q "^== .claude/settings.json" "$SANDBOX/remote.configs" || fail "install.sh remote lost ~/.claude/settings.json"
[ "$(grep -c '^== ' "$SANDBOX/remote.configs")" -eq 1 ] || fail "install.sh remote wrote config files: $(grep '^== ' "$SANDBOX/remote.configs")"

# The same selective install twice changes nothing.
cmp -s "$SANDBOX/remote.links" "$SANDBOX/remote-again.links" || fail "second install.sh remote changed the links"
cmp -s "$SANDBOX/remote.configs" "$SANDBOX/remote-again.configs" || fail "second install.sh remote changed a config file"
if grep -q "backed up" "$SANDBOX/remote-again.out"; then fail "second install.sh remote backed something up"; fi

# Installs add up: each later group keeps every link already there and adds its own.
[ -z "$(comm -23 "$SANDBOX/remote.links" "$SANDBOX/safety.links")" ] || fail "install.sh safety-hooks changed the remote links"
[ -z "$(comm -23 "$SANDBOX/safety.links" "$SANDBOX/pr-loop.links")" ] || fail "install.sh pr-loop changed links already there"
for h in guard-merge guard-secrets allow-task-cleanup; do
  grep -qx ".agents/hooks/$h.sh -> $REPO/scripts/hooks/$h.sh" "$SANDBOX/safety.links" || fail "install.sh safety-hooks did not link $h"
done
if grep -q "telemetry-save" "$SANDBOX/safety.links"; then fail "install.sh safety-hooks linked telemetry-save"; fi
home="$SANDBOX/remote"
has_guard "$home/.claude/settings.json"
has_guard "$home/.codex/hooks.json"
has_guard "$home/.cursor/hooks.json"
jq -e '.hooks.Stop[0].hooks[0].command == "orca"' "$home/.claude/settings.json" >/dev/null || fail "foreign Claude hook lost"
jq -e '.permissions == null' "$home/.claude/settings.json" >/dev/null || fail "a selective install without personal merged the deny rules"
[ "$(sed -n '/^== .claude\/settings.json/,/^== /p' "$SANDBOX/safety.configs" | sed '$d')" = \
  "$(configs "$home" | sed -n '/^== .claude\/settings.json/,/^== /p' | sed '$d')" ] || fail "install.sh pr-loop changed the merged hooks"
[ "$(names_in "$home" .claude/agents)" = "pr-fixer pr-reviewer " ] ||
  fail "pr-loop Claude agents: $(names_in "$home" .claude/agents)"
[ "$(names_in "$home" .agents/skills)" = "_shared address-review configure fix-pr remote-subagents renovate-prs review-pr ship-pr " ] ||
  fail "pr-loop on top of remote skills: $(names_in "$home" .agents/skills)"
for f in .claude/CLAUDE.md .codex/AGENTS.md .codex/rules; do [ ! -e "$home/$f" ] || fail "a selective install without personal made ~/$f"; done

# claude epics: the epics and pr-loop agents (epics requires pr-loop parts), for Claude only.
home="$SANDBOX/epics"
[ "$(names_in "$home" .claude/agents)" = "epic-explorer epic-implementer epic-planner pr-fixer pr-reviewer source-reader test-tuner " ] ||
  fail "install.sh claude epics agents: $(names_in "$home" .claude/agents)"
[ -z "$(find "$home/.codex" "$home/.cursor" -mindepth 1)" ] || fail "install.sh claude epics touched codex or cursor"
if grep -q "security\|remote-agent\|agent-flow\|hooks/guard" "$SANDBOX/epics.links"; then
  fail "install.sh claude epics linked parts it does not need"
fi
grep -qx ".agents/bin/build-epic -> $REPO/adapters/claude/build-epic/launch.sh" "$SANDBOX/epics.links" ||
  fail "install.sh claude epics did not link the build-epic launcher"
# The launcher starts Claude Code: a Codex-only install leaves it out.
if grep -q "bin/build-epic" "$SANDBOX/codex-epics.links"; then fail "install.sh codex epics linked the build-epic launcher"; fi
grep -q "^.agents/skills/build-epic " "$SANDBOX/codex-epics.links" || fail "install.sh codex epics did not link build-epic"

# An unknown selection word: usage, exit 1, nothing linked.
[ ! -e "$SANDBOX/badsel-exited-0" ] || fail "install.sh with an unknown part exited 0"
grep -q "^usage: .*<group>" "$SANDBOX/badsel.out" || fail "install.sh with an unknown part did not print the usage: $(cat "$SANDBOX/badsel.out")"
[ ! -s "$SANDBOX/badsel.links" ] || fail "install.sh with an unknown part linked something"

# A second tool word: usage, exit 1, nothing linked.
[ ! -e "$SANDBOX/twotools-exited-0" ] || fail "install.sh claude codex remote exited 0"
grep -q "^usage: " "$SANDBOX/twotools.out" || fail "install.sh with two tool words did not print the usage: $(cat "$SANDBOX/twotools.out")"
[ ! -s "$SANDBOX/twotools.links" ] || fail "install.sh with two tool words linked something"

# --version: VERSION on its release tag, +<short sha> past it or untagged, VERSION alone without git; nothing linked.
vrepo="$SANDBOX/version-repo"
mkdir -p "$vrepo" "$SANDBOX/version-home"
cp "$REPO/install.sh" "$vrepo/"
echo 1.2.3 >"$vrepo/VERSION"
version() { HOME="$SANDBOX/version-home" "$vrepo/install.sh" --version; }
[ "$(version)" = "dotagents 1.2.3" ] || fail "install.sh --version without git: $(version)"
g() { git -C "$vrepo" -c user.name=t -c user.email=t@t -c commit.gpgsign=false -c tag.gpgsign=false "$@" >/dev/null; }
g init -q && g add -A && g commit -qm one
sha="$(git -C "$vrepo" rev-parse HEAD)"
[ "$(version)" = "dotagents 1.2.3+${sha:0:7}" ] || fail "install.sh --version untagged: $(version)"
g tag v1.2.3
[ "$(version)" = "dotagents 1.2.3" ] || fail "install.sh --version on its tag: $(version)"
g commit -q --allow-empty -m two
sha="$(git -C "$vrepo" rev-parse HEAD)"
[ "$(version)" = "dotagents 1.2.3+${sha:0:7}" ] || fail "install.sh --version past its tag: $(version)"
if HOME="$SANDBOX/version-home" "$vrepo/install.sh" --version claude >/dev/null 2>&1; then fail "install.sh --version claude exited 0"; fi
[ -z "$(find "$SANDBOX/version-home" -mindepth 1)" ] || fail "install.sh --version wrote to HOME"

# Unknown target exits non-zero.
[ ! -e "$SANDBOX/bad-exited-0" ] || fail "unknown target exited 0"

[ "$fails" -eq 0 ] || { echo "install.test.sh: $fails failure(s)"; exit 1; }
echo "install.test.sh: ok"
