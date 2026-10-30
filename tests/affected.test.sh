#!/usr/bin/env bash
# Runs tests/affected.sh in a throwaway repo with its own suites.txt, and checks which checks it picks.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
fails=0

fail() { echo "FAIL: $*"; fails=$((fails + 1)); }

R="$SANDBOX/repo"
mkdir -p "$R/tests" "$R/scripts" "$R/docs"
cp "$REPO/tests/affected.sh" "$R/tests/"
cat >"$R/tests/suites.txt" <<'EOF'
# A comment, then the checks.
all tests/suites.txt
shellcheck *.sh
repo.test.sh *
a.test.sh scripts/a.sh scripts/lib/*
b.test.sh scripts/b.sh
EOF
touch "$R/scripts/a.sh" "$R/scripts/b.sh" "$R/docs/x.md" "$R/tests/a.test.sh" "$R/tests/b.test.sh"
git -C "$R" init -q
git -C "$R" -c user.name=t -c user.email=t@t add .
git -C "$R" -c user.name=t -c user.email=t@t commit -qm base
git -C "$R" update-ref refs/remotes/origin/main HEAD

picks() { (cd "$R" && tests/affected.sh "$@" | tr '\n' ' '); }
expect() {
  local desc="$1" want="$2"; shift 2
  local got; got="$(picks "$@")"
  [ "$got" = "$want" ] || fail "$desc: wanted '$want', got '$got'"
}

ALL="shellcheck repo.test.sh a.test.sh b.test.sh "
expect "no change" ""
expect "--all" "$ALL" --all
expect "a doc" "repo.test.sh " -- docs/x.md
expect "a script" "shellcheck repo.test.sh a.test.sh " -- scripts/a.sh
expect "a nested path" "repo.test.sh a.test.sh " -- scripts/lib/deep/x.py
expect "a suite's own file" "shellcheck repo.test.sh b.test.sh " -- tests/b.test.sh
expect "the map itself" "$ALL" -- tests/suites.txt
expect "a pattern is not expanded against the repo's files" "repo.test.sh " -- docs/x.md

echo change >>"$R/scripts/b.sh"
expect "an uncommitted change" "shellcheck repo.test.sh b.test.sh "
git -C "$R" checkout -q -- scripts/b.sh
touch "$R/docs/new.md"
expect "an untracked file" "repo.test.sh "
git -C "$R" -c user.name=t -c user.email=t@t add docs/new.md
git -C "$R" -c user.name=t -c user.email=t@t commit -qm doc
expect "a committed change" "repo.test.sh "
expect "--base" "" --base HEAD

git -C "$R" update-ref -d refs/remotes/origin/main
expect "no merge base" "$ALL"

(cd "$R" && tests/affected.sh --bogus >/dev/null 2>&1) && fail "an unknown option: did not fail"

[ "$fails" -eq 0 ] || { echo "affected.test.sh: $fails failure(s)"; exit 1; }
echo "affected.test.sh: ok"
