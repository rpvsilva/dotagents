#!/usr/bin/env bash
# Runs scripts/protect-main.sh against a stub gh and checks the ruleset it sends, and to which endpoint.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
fails=0

fail() { echo "FAIL: $*"; fails=$((fails + 1)); }

# Stub gh: `repo view` prints o/r; `api` logs "<arguments>" and any stdin body to $SANDBOX/calls and body.<n>.
# `api repos/o/r/rulesets` (the list) prints $SANDBOX/list.json; when $SANDBOX/deny holds an HTTP code, every
# api call prints gh's error line for it and fails.
mkdir "$SANDBOX/bin"
cat >"$SANDBOX/bin/gh" <<EOF2
#!/usr/bin/env bash
if [ "\$1 \$2" = "repo view" ]; then echo o/r; exit 0; fi
echo "\$*" >>"$SANDBOX/calls"
if [ -s "$SANDBOX/deny" ]; then echo "gh: Upgrade to GitHub Pro (HTTP \$(cat "$SANDBOX/deny"))" >&2; exit 1; fi
if [ "\$*" = "api repos/o/r/rulesets" ]; then cat "$SANDBOX/list.json"; exit 0; fi
cat >"$SANDBOX/body"
echo '{}'
EOF2
chmod +x "$SANDBOX/bin/gh"

# Runs the script with the stub first on PATH and a dummy token; output in $SANDBOX/out, status in $?.
run() {
  rm -f "$SANDBOX/calls" "$SANDBOX/body"
  PATH="$SANDBOX/bin:$PATH" GH_TOKEN=dummy "$REPO/scripts/protect-main.sh" "$@" >"$SANDBOX/out" 2>&1
}

echo '[{"id":5,"name":"other"}]' >"$SANDBOX/list.json"
: >"$SANDBOX/deny"

# Create: no ruleset named main.
run || fail "create: exited non-zero: $(cat "$SANDBOX/out")"
grep -qx "api -X POST repos/o/r/rulesets --input -" "$SANDBOX/calls" || fail "create: no POST to repos/o/r/rulesets: $(cat "$SANDBOX/calls")"
grep -q "PUT" "$SANDBOX/calls" && fail "create: also sent a PUT"

# The JSON that was sent.
body="$SANDBOX/body"
[ "$(jq -r .name "$body")" = main ] || fail "json: name is not main"
[ "$(jq -r '.conditions.ref_name.include[0]' "$body")" = refs/heads/main ] || fail "json: not targeting refs/heads/main"
[ "$(jq -r '[.rules[].type] | sort | join(",")' "$body")" = "deletion,non_fast_forward,pull_request,required_status_checks" ] || fail "json: wrong rule types"
[ "$(jq -r '[.rules[] | select(.type == "required_status_checks") | .parameters.required_status_checks[].context] | join(",")' "$body")" = "lint,test,secrets" ] || fail "json: required checks are not lint, test, secrets"
grep -q agent-pr-gate-job "$body" && fail "json: requires agent-pr-gate-job"
pr='.rules[] | select(.type == "pull_request") | .parameters'
[ "$(jq -r "$pr | .require_code_owner_review" "$body")" = true ] || fail "json: code owner review is off"
[ "$(jq -r "$pr | .required_approving_review_count" "$body")" = 0 ] || fail "json: approving review count is not 0"
[ "$(jq -r "$pr | .dismiss_stale_reviews_on_push" "$body")" = true ] || fail "json: stale reviews are not dismissed on push"
[ "$(jq -r "$pr | .allowed_merge_methods | join(\",\")" "$body")" = "squash,merge" ] || fail "json: merge methods are not squash and merge"

# Update: a ruleset named main exists, by id, and no second one is created.
echo '[{"id":5,"name":"other"},{"id":42,"name":"main"}]' >"$SANDBOX/list.json"
run || fail "update: exited non-zero: $(cat "$SANDBOX/out")"
grep -qx "api -X PUT repos/o/r/rulesets/42 --input -" "$SANDBOX/calls" || fail "update: no PUT to ruleset 42: $(cat "$SANDBOX/calls")"
grep -q "POST" "$SANDBOX/calls" && fail "update: also sent a POST"

# An explicit repo argument replaces the default.
run other/repo || fail "repo argument: exited non-zero"
grep -q "repos/other/repo/rulesets" "$SANDBOX/calls" || fail "repo argument: not used: $(cat "$SANDBOX/calls")"

# --dry-run prints the JSON and writes nothing.
run --dry-run || fail "dry-run: exited non-zero"
[ ! -e "$SANDBOX/body" ] || fail "dry-run: sent a body"
if [ -e "$SANDBOX/calls" ]; then grep -qE "POST|PUT|-X" "$SANDBOX/calls" && fail "dry-run: wrote: $(cat "$SANDBOX/calls")"; fi
jq -e '.rules | length == 4' "$SANDBOX/out" >/dev/null 2>&1 || fail "dry-run: output is not the ruleset JSON"

# A 403 or 404 (private repo on Free): non-zero, one line saying the repo must be public.
for code in 403 404; do
  echo "$code" >"$SANDBOX/deny"
  if run; then fail "HTTP $code: exited zero"; fi
  [ "$(wc -l <"$SANDBOX/out" | tr -d ' ')" = 1 ] || fail "HTTP $code: not one line: $(cat "$SANDBOX/out")"
  grep -q "need the repo public" "$SANDBOX/out" || fail "HTTP $code: message does not say the repo must be public: $(cat "$SANDBOX/out")"
done

[ "$fails" -eq 0 ] && echo "protect-main.test.sh: ok"
exit "$fails"
