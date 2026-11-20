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
  "api -H") printf 'maintainers: [rpvsilva]\\n' ;;
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
  printf '{"state":"%s","isDraft":%s,"baseRefName":"%s","headRefOid":"abc123","isCrossRepository":%s,"author":{"login":"%s"},"reviews":[%s]}' "$1" "$2" "${BASE:-main}" "${FORK:-false}" "${AUTHOR:-rpvsilva}" "${REVIEWS:-}" >"$SANDBOX/view.json"
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

# A fork PR merges without --delete-branch, gated or not; a same-repo PR still deletes its branch.
for b in main feat/gh-12-due-dates; do
  FORK=true BASE=$b pr OPEN false "$(verdict pass)" 0
  rm -f "$SANDBOX/merged"
  PATH="$SANDBOX/bin:$PATH" "$REPO/scripts/merge-pr.sh" 7 >"$SANDBOX/out" 2>&1 || fail "fork PR into $b: $(cat "$SANDBOX/out")"
  grep -qx -- "pr merge 7 --squash --match-head-commit abc123" "$SANDBOX/merged" || fail "fork PR into $b: $(cat "$SANDBOX/merged")"
  BASE=$b pr OPEN false "$(verdict pass)" 0
  rm -f "$SANDBOX/merged"
  PATH="$SANDBOX/bin:$PATH" "$REPO/scripts/merge-pr.sh" 7 >"$SANDBOX/out" 2>&1 || fail "same-repo PR into $b: $(cat "$SANDBOX/out")"
  grep -qx -- "pr merge 7 --squash --delete-branch --match-head-commit abc123" "$SANDBOX/merged" || fail "same-repo PR into $b: $(cat "$SANDBOX/merged")"
done

# An outsider's PR merges only after a maintainer's latest review approves its head; maintainer and App PRs need none.
review() { printf '{"author":{"login":"%s"},"state":"%s","commit":{"oid":"%s"}}' "$1" "$2" "$3"; }
oos="$(verdict out-of-scope)"
for a in rpvsilva app/rpvsilva-agent; do
  AUTHOR=$a pr OPEN false "$oos" 0;                                   expect merge  "maintainer-side author $a, no review"
done
AUTHOR=rpvsilva-agent pr OPEN false "$oos" 0;                         expect refuse "plain user named like the App, no approval"
FORK=true AUTHOR=outsider pr OPEN false "$oos" 0;                     expect refuse "outsider, no approval"
grep -q "not a maintainer" "$SANDBOX/out" || fail "outsider: refusal reason not printed"
[ "$(wc -l <"$SANDBOX/out")" -eq 1 ] || fail "outsider: refusal is not one line"
[ ! -e "$SANDBOX/npx" ] || fail "outsider refused: gate still ran"
FORK=true AUTHOR=outsider REVIEWS="$(review rpvsilva APPROVED abc123)" pr OPEN false "$oos" 0
rm -f "$SANDBOX/merged"
PATH="$SANDBOX/bin:$PATH" "$REPO/scripts/merge-pr.sh" 7 >"$SANDBOX/out" 2>&1 || fail "outsider approved on head: $(cat "$SANDBOX/out")"
grep -qx -- "pr merge 7 --squash --match-head-commit abc123" "$SANDBOX/merged" || fail "outsider approved on head: $(cat "$SANDBOX/merged")"
FORK=true AUTHOR=outsider REVIEWS="$(review rpvsilva APPROVED old999)" pr OPEN false "$oos" 0; expect refuse "approval on an older commit"
FORK=true AUTHOR=outsider REVIEWS="$(review outsider2 APPROVED abc123)" pr OPEN false "$oos" 0; expect refuse "approval by a non-maintainer"
FORK=true AUTHOR=outsider REVIEWS="$(review rpvsilva APPROVED abc123),$(review rpvsilva CHANGES_REQUESTED abc123)" pr OPEN false "$oos" 0; expect refuse "approval then changes requested"
FORK=true AUTHOR=outsider REVIEWS="$(review rpvsilva APPROVED abc123),$(review rpvsilva COMMENTED abc123)" pr OPEN false "$oos" 0; expect merge "comment after approval"
# The head moves while the gate waits: the approval covered abc123, the gate judged def456.
moved='{"pr":7,"head_sha":"def456","result":"out-of-scope","missing":[],"problems":[]}'
FORK=true AUTHOR=outsider REVIEWS="$(review rpvsilva APPROVED abc123)" pr OPEN false "$moved" 0
rm -f "$SANDBOX/merged"
if PATH="$SANDBOX/bin:$PATH" "$REPO/scripts/merge-pr.sh" 7 >"$SANDBOX/out" 2>&1; then fail "head moved after approval: merged"; fi
[ ! -e "$SANDBOX/merged" ] || fail "head moved after approval: still called gh pr merge"
grep -q "head moved" "$SANDBOX/out" || fail "head moved: refusal reason not printed"

# The CI wait is configurable, so a caller can judge at once.
pr OPEN false "$(verdict pass)" 0
MERGE_PR_CI_WAIT=0 PATH="$SANDBOX/bin:$PATH" "$REPO/scripts/merge-pr.sh" 7 >/dev/null
grep -q -- "--ci-wait-seconds 0$" "$SANDBOX/npx" || fail "MERGE_PR_CI_WAIT not passed to the gate"

# The pinned gate is the one the workflow runs.
ref="$(sed -n 's/^GATE_REF=//p' "$REPO/scripts/merge-pr.sh")"
grep -q "uses: 0xgui/agent-pr-gate@$ref" "$REPO/.github/workflows/agent-pr-gate.yml" || fail "GATE_REF $ref differs from the gate workflow's pin"

[ "$fails" -eq 0 ] || { echo "merge-pr.test.sh: $fails failure(s)"; exit 1; }
echo "merge-pr.test.sh: ok"
