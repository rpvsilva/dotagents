#!/usr/bin/env bash
# shellcheck disable=SC2016 # the commands under test are literal text, never run
# Runs scripts/hooks/guard-merge.sh in throwaway repos and checks which commands it blocks.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK="$REPO/scripts/hooks/guard-merge.sh"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
fails=0

fail() { echo "FAIL: $*"; fails=$((fails + 1)); }

# Makes a repo at $1 on branch $2; gated when $3 is "gated".
mkrepo() {
  git init -q -b "$2" "$1"
  [ "$3" = gated ] && touch "$1/.review-gate.yml"
  return 0
}
mkrepo "$SANDBOX/gated" feat/x gated
mkrepo "$SANDBOX/on-main" main gated
mkrepo "$SANDBOX/plain" main plain

# Runs the hook in repo $2 on command $3, the way Claude Code calls it; $1 is "block" or "allow".
expect() {
  local want="$1" dir="$2" cmd="$3" got
  local json
  json="$(jq -n --arg c "$cmd" --arg d "$SANDBOX/$dir" '{tool_name: "Bash", tool_input: {command: $c}, cwd: $d}')"
  if "$HOOK" <<<"$json" 2>/dev/null; then got=allow; else
    [ $? -eq 2 ] && got=block || got="error"
  fi
  [ "$got" = "$want" ] || fail "$dir: '$cmd': wanted $want, got $got"
}

expect block gated 'gh pr merge 7 --squash'
expect block gated 'GH_TOKEN="$(scripts/gh-app-token.sh)" gh pr merge 7'
expect block gated 'cd x && gh pr merge 7'
expect block gated 'gh api -X PUT repos/o/r/pulls/7/merge'
expect block gated 'gh pr review 7 --approve'
expect block gated 'gh pr review 7 -a'
expect block gated 'gh api repos/o/r/pulls/7/reviews -f event=APPROVE'
expect block gated 'gh api -f event=APPROVE repos/o/r/pulls/7/reviews'
expect block gated 'gh api -X POST repos/o/r/pulls/7/reviews/9/events -f event=APPROVE'
expect block gated 'git push origin main'
expect block gated 'git push -u origin HEAD:main'
expect block gated 'git push --force origin +master'
expect block gated 'git push origin refs/heads/main'
expect block on-main 'git push'
expect block on-main 'git push origin feat/x'

expect allow gated 'scripts/merge-pr.sh 7'
expect allow gated 'GH_TOKEN="$(scripts/gh-app-token.sh)" scripts/merge-pr.sh 7'
expect allow gated 'gh pr view 7'
expect allow gated 'gh pr review 7 --comment -b "looks good"'
expect allow gated 'gh api repos/o/r/pulls/7/reviews -f event=COMMENT -f body=ok'
expect allow gated 'gh api repos/o/r/pulls/7/reviews --input review.json'
expect allow gated 'git push -u origin feat/x'
expect allow gated 'git push origin feat/main-menu'
expect allow gated 'git push origin main-fix'
expect allow gated 'git push origin feat/x && git log main'
expect allow gated 'git pull origin main'
expect allow on-main 'git status'
expect allow plain 'gh pr merge 7'
expect allow plain 'git push origin main'

# A switch before the push decides the branch pushed; the last one wins, and one after the push doesn't count.
expect allow on-main 'git switch -c feat/y && git push -u origin feat/y'
expect allow on-main 'git switch -q feat/y && git pull --ff-only && git push'
expect allow on-main 'git checkout -b feat/y origin/feat/y; git push'
expect allow on-main $'git switch feat/y\ngit push'
expect block gated 'git switch main && git push'
expect block gated 'git switch feat/y && git checkout master && git push'
expect block on-main 'git push && git switch feat/y'
expect block on-main 'git checkout -- README.md && git push'

# Heredoc bodies are data, not commands; what follows the heredoc is still judged.
expect allow gated $'cat >notes.md <<\'EOF\'\nnever run gh pr merge 7 by hand\nEOF'
expect allow gated $'cat >notes.md <<-EOF\n\tgh pr merge 7\n\tEOF'
expect block gated $'cat >notes.md <<EOF\ntext\nEOF\ngh pr merge 7'
expect block gated 'jq . <<<"$x"; gh pr merge 7'

# A single cd into another repo is judged by that repo.
expect block plain "cd $SANDBOX/gated && gh pr merge 7"
expect allow gated "cd $SANDBOX/plain && gh pr merge 7"
expect allow gated "cd \"$SANDBOX/plain\" && gh pr merge 7"
expect allow gated 'cd ../plain; gh pr merge 7'
expect block gated 'cd ../on-main && git push'
expect block gated "cd $SANDBOX/plain && cd - && gh pr merge 7"

# Block messages name no person and no private gate; approval is left to a human.
messages=""
for c in 'gh pr merge 7' 'gh pr review 7 --approve' 'git push origin main'; do
  messages+="$(cd "$SANDBOX/gated" && "$HOOK" "$c" 2>&1 || true)"$'\n'
done
messages+="$(cd "$SANDBOX/on-main" && "$HOOK" 'git push' 2>&1 || true)"
grep -qE 'Rui|agent-pr-gate' <<<"$messages" && fail "a block message names a person or a private gate: $messages"
grep -qF "approving a PR is a human's call; an agent never approves" <<<"$messages" || fail "approval message: $messages"

# The command as an argument works too, from the current directory.
(cd "$SANDBOX/gated" && "$HOOK" 'gh pr merge 7' 2>/dev/null) && fail "argument form did not block"

[ "$fails" -eq 0 ] || { echo "guard-merge.test.sh: $fails failure(s)"; exit 1; }
echo "guard-merge.test.sh: ok"
