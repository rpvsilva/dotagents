#!/usr/bin/env bash
# shellcheck disable=SC2016 # the commands under test are literal text, never run
# Runs scripts/hooks/allow-task-cleanup.sh in a throwaway repo and checks which commands it approves.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK="$REPO/scripts/hooks/allow-task-cleanup.sh"
SNIPPET="$REPO/adapters/claude/hooks/allow-task-cleanup.json"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
fails=0

fail() { echo "FAIL: $*"; fails=$((fails + 1)); }

R="$SANDBOX/repo"
git init -q -b main "$R"
git -C "$R" -c user.name=t -c user.email=t@t commit -q --allow-empty -m init
git -C "$R" worktree add -q -b task/gh-1-x/01 "$SANDBOX/wt-task"
git -C "$R" worktree add -q -b feat/gh-1-x "$SANDBOX/wt-ticket"
git -C "$R" worktree add -q --detach "$SANDBOX/wt-detached"
git -C "$R" branch feat/gh-2-merged
git -C "$R" branch feat/gh-4-open
git -C "$R" worktree add -q -b feat/gh-5-wt "$SANDBOX/wt-merged"
tip="$(git -C "$R" rev-parse HEAD)"
git -C "$SANDBOX/wt-ticket" -c user.name=t -c user.email=t@t commit -q --allow-empty -m later

# Stub gh: `pr list --head <b> --state merged` prints the heads listed for <b> in $SANDBOX/merged
# ("<branch> <sha>" lines). It fails outside a repo, and when $SANDBOX/offline exists.
mkdir "$SANDBOX/bin"
cat >"$SANDBOX/bin/gh" <<EOF
#!/usr/bin/env bash
[ ! -e "$SANDBOX/offline" ] || exit 1
git rev-parse --show-toplevel >/dev/null 2>&1 || exit 1
[ "\$1 \$2" = "pr list" ] || exit 1
head=""; state=""
while [ \$# -gt 0 ]; do
  case "\$1" in --head) head="\$2"; shift ;; --state) state="\$2"; shift ;; esac
  shift
done
[ "\$state" = merged ] || exit 1
awk -v b="\$head" '\$1 == b { print \$2 }' "$SANDBOX/merged"
EOF
chmod +x "$SANDBOX/bin/gh"
# feat/gh-2-merged and feat/gh-5-wt: merged at their tip. feat/gh-1-x: merged, then a commit was added.
# main: a PR from a fork's main, never a reason to delete main. Others: no merged PR.
printf '%s\n' "feat/gh-2-merged $tip" "feat/gh-5-wt $tip" "feat/gh-1-x $tip" "main $tip" >"$SANDBOX/merged"
export PATH="$SANDBOX/bin:$PATH"

# Runs the hook in the repo on command $2, the way Claude Code calls it; $1 is "allow" or "ask".
expect() {
  local want="$1" cmd="$2" got json
  json="$(jq -n --arg c "$cmd" --arg d "$R" '{tool_name: "Bash", tool_input: {command: $c}, cwd: $d}')"
  if "$HOOK" <<<"$json" 2>/dev/null; then got=allow; else got=ask; fi
  [ "$got" = "$want" ] || fail "'$cmd': wanted $want, got $got"
}

expect allow 'git branch -D task/gh-1-x/01'
expect allow 'git branch -d task/gh-1-x/01-tests'
expect allow 'git branch --delete --force task/gh-1-x/02 task/gh-1-x/02-tests'
expect allow 'git branch -D fix/security-tokens/01'
expect allow "git -C $R branch -D task/gh-1-x/01"
expect allow "cd $R && git branch -D task/gh-1-x/01"
expect allow "git -C \"$R\" branch -D 'task/gh-1-x/01'"
expect allow "git worktree remove $SANDBOX/wt-task && git branch -D task/gh-1-x/01"
expect allow "git worktree remove --force $SANDBOX/wt-task; git branch -D task/gh-1-x/01"
expect allow "git worktree remove ../wt-task && git worktree prune && git branch -D task/gh-1-x/01"

# A branch whose tip is the head of a merged PR is finished too, ticket branches included.
expect allow 'git branch -D feat/gh-2-merged'
expect allow 'git branch -d feat/gh-2-merged task/gh-1-x/01'
expect allow "git -C $R branch -D feat/gh-2-merged"
expect allow "git worktree remove $SANDBOX/wt-merged && git branch -D feat/gh-5-wt"
expect ask 'git branch -D feat/gh-4-open'
expect ask 'git branch -D feat/gh-2-merged feat/gh-4-open'
expect ask 'git branch -D feat/nowhere'
touch "$SANDBOX/offline"
expect ask 'git branch -D feat/gh-2-merged'
rm "$SANDBOX/offline"
mv "$SANDBOX/bin/gh" "$SANDBOX/gh.off"
expect ask 'git branch -D feat/gh-2-merged'
mv "$SANDBOX/gh.off" "$SANDBOX/bin/gh"

# Ticket and fix branches with no merged PR at their tip ask: feat/gh-1-x got a commit after its merge.
expect ask 'git branch -D feat/gh-1-x'
expect ask 'git branch -D fix/security-tokens'
expect ask 'git branch -D task/gh-1-x/01 feat/gh-1-x'
expect ask 'git branch -D main'
expect ask 'git branch -D task/gh-1-x'
expect ask 'git branch -D task/gh-1-x/01/deeper'
expect ask 'git branch -D task/gh-1-x/-x'
expect ask "git worktree remove --force $SANDBOX/wt-ticket"
expect ask "git worktree remove $SANDBOX/wt-detached"
expect ask "git worktree remove $SANDBOX/missing"
expect ask "git worktree remove $SANDBOX/wt-task $SANDBOX/wt-ticket"

# Anything else in the command, or anything the hook can't read plainly, asks.
expect ask 'git branch'
expect ask 'git branch task/gh-1-x/09'
expect ask 'git branch -m task/gh-1-x/01 feat/gh-1-x'
expect ask 'git branch -D task/gh-1-x/01 && rm -rf /'
expect ask 'git branch -D task/gh-1-x/01 & git push origin main'
expect ask 'git branch -D task/gh-1-x/01 | cat'
expect ask 'git branch -D $(git branch --format=%\(refname:short\))'
expect ask 'git branch -D `echo feat/gh-1-x`'
expect ask 'git branch -D task/*'
expect ask 'git branch -D task/gh-1-x/01 > /dev/null'
expect ask $'git branch -D task/gh-1-x/01\ngit branch -D feat/gh-1-x'
expect ask '"git" branch -D feat/gh-1-x'
expect ask 'git -c core.x=y branch -D task/gh-1-x/01'
expect ask 'git push origin --delete task/gh-1-x/01'
expect ask "cd $R"
expect ask 'cd /nowhere && git branch -D task/gh-1-x/01'
expect ask ';'
expect ask ''

# The command as an argument works too.
(cd "$R" && "$HOOK" 'git branch -D task/gh-1-x/01') || fail "argument form did not allow"

# Claude's wiring prints an allow decision on approval, nothing otherwise, and never fails.
run_snippet() {
  local json
  json="$(jq -n --arg c "$1" --arg d "$R" '{tool_name: "Bash", tool_input: {command: $c}, cwd: $d}')"
  HOME="$SANDBOX/home" bash -c "$(jq -r '.hooks.PreToolUse[0].hooks[0].command' "$SNIPPET")" <<<"$json"
}
mkdir -p "$SANDBOX/home/.agents"
ln -s "$REPO/scripts/hooks" "$SANDBOX/home/.agents/hooks"
out="$(run_snippet 'git branch -D task/gh-1-x/01')" || fail "snippet failed on approval"
[ "$(jq -r '.hookSpecificOutput.permissionDecision' <<<"$out")" = allow ] || fail "snippet did not print allow: $out"
out="$(run_snippet 'git branch -D feat/gh-1-x')" || fail "snippet failed on no opinion"
[ -z "$out" ] || fail "snippet printed on no opinion: $out"

[ "$fails" -eq 0 ] || { echo "allow-task-cleanup.test.sh: $fails failure(s)"; exit 1; }
echo "allow-task-cleanup.test.sh: ok"
