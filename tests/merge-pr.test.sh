#!/usr/bin/env bash
# Runs scripts/merge-pr.sh against a stub gh and checks when it merges.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
fails=0

fail() { echo "FAIL: $*"; fails=$((fails + 1)); }

# Stub gh: `pr view` prints $SANDBOX/view.json, `pr merge` logs its arguments.
mkdir "$SANDBOX/bin"
cat >"$SANDBOX/bin/gh" <<EOF
#!/usr/bin/env bash
case "\$1 \$2" in
  "pr view") cat "$SANDBOX/view.json" ;;
  "pr merge") echo "\$*" >>"$SANDBOX/merged" ;;
esac
EOF
chmod +x "$SANDBOX/bin/gh"

ci() { printf '{"__typename":"CheckRun","name":"ci-ok","conclusion":"%s"}' "$1"; }
gate() { printf '{"__typename":"StatusContext","context":"agent-pr-gate","state":"%s"}' "$1"; }

# Writes a PR view with state $1, draft $2 and rollup entries $3.
pr() { printf '{"state":"%s","isDraft":%s,"headRefOid":"abc123","statusCheckRollup":[%s]}' "$1" "$2" "$3" >"$SANDBOX/view.json"; }

# Runs merge-pr.sh; $1 is "merge" or "refuse".
expect() {
  local want="$1" desc="$2"
  rm -f "$SANDBOX/merged"
  if PATH="$SANDBOX/bin:$PATH" "$REPO/scripts/merge-pr.sh" 7 >/dev/null 2>&1; then got=merge; else got=refuse; fi
  [ "$got" = "$want" ] || { fail "$desc: wanted $want, got $got"; return; }
  if [ "$want" = merge ]; then
    grep -q -- "--match-head-commit abc123" "$SANDBOX/merged" || fail "$desc: merged without pinning the head commit"
  elif [ -e "$SANDBOX/merged" ]; then
    fail "$desc: refused but still called gh pr merge"
  fi
}

pr OPEN false "$(ci SUCCESS),$(gate SUCCESS)";        expect merge  "both green"
pr OPEN false "$(ci SUCCESS),$(gate FAILURE)";        expect refuse "gate blocked"
pr OPEN false "$(ci FAILURE),$(gate SUCCESS)";        expect refuse "ci failed"
pr OPEN false "$(ci SUCCESS),$(gate PENDING)";        expect refuse "gate pending"
pr OPEN false "$(ci SUCCESS)";                        expect refuse "gate never ran"
pr OPEN false "";                                     expect refuse "no checks"
pr OPEN true "$(ci SUCCESS),$(gate SUCCESS)";         expect refuse "draft"
pr MERGED false "$(ci SUCCESS),$(gate SUCCESS)";      expect refuse "already merged"
pr OPEN false "$(gate FAILURE),$(ci SUCCESS),$(gate SUCCESS)"; expect merge "latest gate status wins"

[ "$fails" -eq 0 ] || { echo "merge-pr.test.sh: $fails failure(s)"; exit 1; }
echo "merge-pr.test.sh: ok"
