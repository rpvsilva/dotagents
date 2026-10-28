#!/usr/bin/env bash
# Runs scripts/merge-pr.sh against a stub gh and checks when it merges.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
fails=0

fail() { echo "FAIL: $*"; fails=$((fails + 1)); }

# Stub gh: `pr view` prints $SANDBOX/view.json; `workflow run` and `pr merge` log their arguments;
# `run list` lists one gate run named in $SANDBOX/title, created now; `run watch` swaps in
# $SANDBOX/after.json when present (the gate's new verdict).
mkdir "$SANDBOX/bin"
cat >"$SANDBOX/bin/gh" <<EOF
#!/usr/bin/env bash
case "\$1 \$2" in
  "pr view") cat "$SANDBOX/view.json" ;;
  "pr merge") echo "\$*" >>"$SANDBOX/merged" ;;
  "workflow run") echo "\$*" >>"$SANDBOX/dispatched" ;;
  "run list") jq -n --arg t "\$(cat "$SANDBOX/title")" '[{databaseId: 42, displayTitle: \$t, createdAt: (now | todate)}]' ;;
  "run watch") echo "\$*" >>"$SANDBOX/watched"; [ ! -e "$SANDBOX/after.json" ] || mv "$SANDBOX/after.json" "$SANDBOX/view.json" ;;
esac
EOF
chmod +x "$SANDBOX/bin/gh"

check() { printf '{"__typename":"CheckRun","name":"%s","conclusion":"%s"}' "$1" "$2"; }
gate() { printf '{"__typename":"StatusContext","context":"agent-pr-gate","state":"%s"}' "$1"; }
green="$(check lint SUCCESS),$(check 'test (ubuntu-latest)' SUCCESS),$(check agent-pr-gate-job SUCCESS)"

# Writes a PR view with state $1, draft $2 and rollup entries $3 (to file $4, default view.json).
pr() { printf '{"state":"%s","isDraft":%s,"headRefOid":"abc123","statusCheckRollup":[%s]}' "$1" "$2" "$3" >"$SANDBOX/${4:-view.json}"; }

# Runs merge-pr.sh on PR 7; $1 is "merge" or "refuse".
expect() {
  local want="$1" desc="$2" got
  rm -f "$SANDBOX/merged" "$SANDBOX/dispatched" "$SANDBOX/watched"
  if PATH="$SANDBOX/bin:$PATH" MERGE_PR_TRIES="${MERGE_PR_TRIES:-30}" "$REPO/scripts/merge-pr.sh" 7 >"$SANDBOX/out" 2>&1; then got=merge; else got=refuse; fi
  [ "$got" = "$want" ] || { fail "$desc: wanted $want, got $got: $(cat "$SANDBOX/out")"; return; }
  if [ "$want" = merge ]; then
    grep -q -- "--match-head-commit abc123" "$SANDBOX/merged" || fail "$desc: merged without pinning the head commit"
    grep -qx "workflow run agent-pr-gate.yml -f pr=7" "$SANDBOX/dispatched" || fail "$desc: gate not started for PR 7"
    grep -q "run watch 42" "$SANDBOX/watched" || fail "$desc: did not wait for the gate run"
  elif [ -e "$SANDBOX/merged" ]; then
    fail "$desc: refused but still called gh pr merge"
  fi
}

echo "agent-pr-gate #7" >"$SANDBOX/title"
pr OPEN false "$green,$(gate SUCCESS)";                         expect merge  "all green"
pr OPEN false "$green,$(gate FAILURE)";                         expect refuse "gate blocked"
pr OPEN false "$(check lint FAILURE),$(gate SUCCESS)";          expect refuse "a check failed"
pr OPEN false "$(check lint ''),$(gate SUCCESS)";               expect refuse "a check still running"
pr OPEN false "$green,$(check portability SKIPPED),$(gate SUCCESS)"; expect merge "skipped check"
pr OPEN false "$green,$(gate PENDING)";                         expect refuse "gate pending"
pr OPEN false "$green";                                         expect refuse "gate never ran"
pr OPEN false "$(check agent-pr-gate-job SUCCESS),$(gate SUCCESS)"; expect refuse "no CI checks"
pr OPEN true "$green,$(gate SUCCESS)";                          expect refuse "draft"
pr MERGED false "$green,$(gate SUCCESS)";                       expect refuse "already merged"
pr OPEN false "$green,$(gate FAILURE),$(gate SUCCESS)";         expect merge  "latest gate status wins"

# The verdict read after the gate run is the one that counts.
pr OPEN false "$green,$(gate FAILURE)"
pr OPEN false "$green,$(gate FAILURE),$(gate SUCCESS)" after.json
expect merge "gate turns green during the run"

# A gate run for another PR is never mistaken for this one.
echo "agent-pr-gate #8" >"$SANDBOX/title"
pr OPEN false "$green,$(gate SUCCESS)"
MERGE_PR_TRIES=1 expect refuse "only another PR's gate run"
grep -q "did not start" "$SANDBOX/out" || fail "missing run: reason not given"

[ "$fails" -eq 0 ] || { echo "merge-pr.test.sh: $fails failure(s)"; exit 1; }
echo "merge-pr.test.sh: ok"
