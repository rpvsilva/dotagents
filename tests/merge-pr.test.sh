#!/usr/bin/env bash
# Runs scripts/merge-pr.sh against a stub gh and a stub npx (agent-pr-gate check), and checks when it merges.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
fails=0

fail() { echo "FAIL: $*"; fails=$((fails + 1)); }

# Stub gh: `pr view` prints $SANDBOX/view.json, `repo view` prints main, `pr merge` logs its arguments.
# Stub npx: logs its arguments, prints $SANDBOX/verdict.json and exits with $SANDBOX/code.
mkdir "$SANDBOX/bin"
cat >"$SANDBOX/bin/gh" <<EOF
#!/usr/bin/env bash
case "\$1 \$2" in
  "pr view") cat "$SANDBOX/view.json" ;;
  "repo view") echo main ;;
  "pr merge") echo "\$*" >>"$SANDBOX/merged" ;;
esac
EOF
cat >"$SANDBOX/bin/npx" <<EOF
#!/usr/bin/env bash
echo "\$*" >"$SANDBOX/npx"
cat "$SANDBOX/verdict.json"
exit "\$(cat "$SANDBOX/code")"
EOF
chmod +x "$SANDBOX/bin/gh" "$SANDBOX/bin/npx"

# Sets the PR (state $1, draft $2) and the gate's answer: JSON $3, exit code $4. The base is $BASE, else main.
pr() {
  printf '{"state":"%s","isDraft":%s,"baseRefName":"%s","headRefOid":"abc123"}' "$1" "$2" "${BASE:-main}" >"$SANDBOX/view.json"
  printf '%s' "$3" >"$SANDBOX/verdict.json"
  echo "$4" >"$SANDBOX/code"
}
verdict() { printf '{"pr":7,"head_sha":"abc123","result":"%s","missing":[%s],"problems":[%s]}' "$1" "${2:-}" "${3:-}"; }

# Runs merge-pr.sh on PR 7; $1 is "merge", "merge-ungated" (no gate run) or "refuse".
expect() {
  local want="$1" desc="$2" got
  rm -f "$SANDBOX/merged" "$SANDBOX/npx"
  if PATH="$SANDBOX/bin:$PATH" "$REPO/scripts/merge-pr.sh" 7 >"$SANDBOX/out" 2>&1; then got=merge; else got=refuse; fi
  [ "$got" = merge ] && [ ! -e "$SANDBOX/npx" ] && got=merge-ungated
  [ "$got" = "$want" ] || { fail "$desc: wanted $want, got $got: $(cat "$SANDBOX/out")"; return; }
  if [ "$want" != refuse ]; then
    grep -q -- "--match-head-commit abc123" "$SANDBOX/merged" || fail "$desc: merged without pinning the judged head commit"
    grep -q -- "--squash" "$SANDBOX/merged" || fail "$desc: merged without squashing"
  fi
  if [ "$want" = merge ]; then
    grep -qE "^--yes github:0xgui/agent-pr-gate#[0-9a-f]{40} check 7 --ci-wait-seconds [0-9]+$" "$SANDBOX/npx" || fail "$desc: gate not run as a pinned check of PR 7: $(cat "$SANDBOX/npx")"
  elif [ "$want" = refuse ] && [ -e "$SANDBOX/merged" ]; then
    fail "$desc: refused but still called gh pr merge"
  fi
}

problem='{"id":"approvals","what":"Needs 1 approving human review(s), has 0","fix":"Get a review."}'
pr OPEN false "$(verdict pass)" 0;                                    expect merge  "gate passes"
pr OPEN false "$(verdict out-of-scope)" 0;                            expect merge  "not in scope"
pr OPEN false "$(verdict blocked '"approvals"' "$problem")" 1;        expect refuse "gate blocks"
grep -q "Needs 1 approving human review" "$SANDBOX/out" || fail "blocked: problems not shown"
pr OPEN false '{"pr":7,"result":"stale","missing":[]}' 2;             expect refuse "PR changed while judging"
pr OPEN false '' 2;                                                   expect refuse "gate could not run"
pr OPEN false 'not json' 0;                                           expect refuse "exit 0 without a verdict"
pr OPEN true "$(verdict pass)" 0;                                     expect refuse "draft"
[ ! -e "$SANDBOX/npx" ] || fail "draft: gate still ran"
pr MERGED false "$(verdict pass)" 0;                                  expect refuse "already merged"

# A PR into another branch (a build-epic task PR) merges without the gate; a blocked verdict changes nothing.
BASE=feat/gh-12-due-dates pr OPEN false "$(verdict blocked '"approvals"' "$problem")" 1
expect merge-ungated "PR into a non-default branch"
BASE=feat/gh-12-due-dates pr OPEN true "$(verdict pass)" 0;           expect refuse "draft into a non-default branch"

# --merge merges with a merge commit, gated or not, still pinned to the head (`expect` checks that it squashes without).
BASE=feat/gh-12-due-dates pr OPEN false "$(verdict pass)" 0
rm -f "$SANDBOX/merged"
PATH="$SANDBOX/bin:$PATH" "$REPO/scripts/merge-pr.sh" --merge 7 >"$SANDBOX/out" 2>&1 || fail "--merge into a non-default branch: $(cat "$SANDBOX/out")"
grep -qx -- "pr merge 7 --merge --delete-branch --match-head-commit abc123" "$SANDBOX/merged" || fail "--merge ungated: $(cat "$SANDBOX/merged")"
pr OPEN false "$(verdict pass)" 0
rm -f "$SANDBOX/merged"
PATH="$SANDBOX/bin:$PATH" "$REPO/scripts/merge-pr.sh" --merge 7 >"$SANDBOX/out" 2>&1 || fail "--merge through the gate: $(cat "$SANDBOX/out")"
grep -qx -- "pr merge 7 --merge --delete-branch --match-head-commit abc123" "$SANDBOX/merged" || fail "--merge gated: $(cat "$SANDBOX/merged")"
if PATH="$SANDBOX/bin:$PATH" "$REPO/scripts/merge-pr.sh" --rebase 7 >"$SANDBOX/out" 2>&1; then fail "--rebase: accepted an unknown option"; fi

# The CI wait is configurable, so a caller can judge at once.
pr OPEN false "$(verdict pass)" 0
MERGE_PR_CI_WAIT=0 PATH="$SANDBOX/bin:$PATH" "$REPO/scripts/merge-pr.sh" 7 >/dev/null
grep -q -- "--ci-wait-seconds 0$" "$SANDBOX/npx" || fail "MERGE_PR_CI_WAIT not passed to the gate"

# The pinned gate is the one the workflow runs.
ref="$(sed -n 's/^GATE_REF=//p' "$REPO/scripts/merge-pr.sh")"
grep -q "uses: 0xgui/agent-pr-gate@$ref" "$REPO/.github/workflows/agent-pr-gate.yml" || fail "GATE_REF $ref differs from the gate workflow's pin"

[ "$fails" -eq 0 ] || { echo "merge-pr.test.sh: $fails failure(s)"; exit 1; }
echo "merge-pr.test.sh: ok"
