#!/usr/bin/env bash
# Runs tests/affected.sh in a throwaway repo with its own suites.txt, and checks which checks it picks; then which
# test folders tests/python.test.sh picks from the changed files tests/run.sh hands it.
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

# --files: the changed files instead of the checks.
expect "--files with given paths" "docs/x.md scripts/a.sh " --files -- docs/x.md scripts/a.sh
expect "--files --all" "--all " --files --all
expect "--files with no merge base" "--all " --files
git -C "$R" update-ref refs/remotes/origin/main HEAD
echo change >>"$R/scripts/b.sh"
expect "--files: an uncommitted change" "scripts/b.sh " --files

# python.test.sh, on this repo's own test folders (PYTHON_TEST_LIST=1 prints them without running).
unset PYTHON_TEST_FILES # CI exports the PR's own list; these cases set their own
pydirs() { (cd "$REPO" && PYTHON_TEST_FILES="$(printf '%s\n' "$@")" PYTHON_TEST_LIST=1 tests/python.test.sh | tr '\n' ' '); }
pyexpect() {
  local desc="$1" want="$2"; shift 2
  local got; got="$(pydirs "$@")"
  [ "$got" = "$want" ] || fail "python.test.sh, $desc: wanted '$want', got '$got'"
}
PYALL="$(cd "$REPO" && PYTHON_TEST_LIST=1 tests/python.test.sh | tr '\n' ' ')"
pyexpect "a file in a folder" "skills/telemetry " skills/telemetry/report.py
pyexpect "the deepest of nested folders" "skills/build-epic/scripts " skills/build-epic/scripts/steplib/core.py
pyexpect "the outer of nested folders" "skills/build-epic " skills/build-epic/plan_check.py
pyexpect "two folders" "adapters/claude/telemetry skills/telemetry " skills/telemetry/a.py adapters/claude/telemetry/b.json
pyexpect "a .py in no folder" "$PYALL" skills/telemetry/a.py scripts/x.py
pyexpect "a file Python tests read" "$PYALL" scripts/hooks/secret-patterns.txt
pyexpect "a file no Python test reads is ignored" "skills/telemetry " docs/x.md skills/telemetry/a.py
pyexpect "--all" "$PYALL" --all
pyexpect "a file other folders' tests run by path" "adapters/claude/telemetry evals evals/_runner/adapters " \
  adapters/claude/telemetry/extract.py
pyexpect "a file other folders' tests read by path" "evals evals/_runner evals/_runner/adapters skills/telemetry " \
  skills/telemetry/pricing.json
# This repo's own suites.txt: a fixture a Python test reads selects python.test.sh, and its test folder.
case " $(cd "$REPO" && tests/affected.sh -- evals/_runner/fixtures/cases/good/configure/minimal/case.json | tr '\n' ' ')" in
  *" python.test.sh "*) ;; *) fail "suites.txt: an evals fixture does not select python.test.sh" ;;
esac
# The real repo files the evals tests extract from HEAD and run select python.test.sh, and the folders running them.
for f in install.sh scripts/parts.sh scripts/capped skills/configure/SKILL.md skills/configure/needs.json \
  skills/build-epic/needs.json; do
  case " $(cd "$REPO" && tests/affected.sh -- "$f" | tr '\n' ' ')" in
    *" python.test.sh "*) ;; *) fail "suites.txt: $f, run by the evals tests, does not select python.test.sh" ;;
  esac
done
pyexpect "install.sh, run by the evals tests" "evals evals/_runner evals/_runner/adapters " install.sh
pyexpect "parts.sh, run by the evals tests" "evals evals/_runner evals/_runner/adapters " scripts/parts.sh
pyexpect "capped, run by the evals tests" "evals evals/_runner evals/_runner/adapters " scripts/capped
pyexpect "the configure skill, installed by the evals tests" "evals evals/_runner evals/_runner/adapters " skills/configure/SKILL.md
pyexpect "the configure skill's needs, read by the evals tests" "evals evals/_runner evals/_runner/adapters " skills/configure/needs.json
pyexpect "the build-epic skill's needs, read by the evals tests" "evals evals/_runner/adapters skills/build-epic " \
  skills/build-epic/needs.json
pyexpect "an evals fixture" "evals/_runner " evals/_runner/fixtures/cases/good/configure/minimal/case.json
[ "$(cd "$REPO" && PYTHON_TEST_FILES="" PYTHON_TEST_LIST=1 tests/python.test.sh | tr '\n' ' ')" = "$PYALL" ] ||
  fail "python.test.sh, no files given: not every folder"
case "$PYALL" in *"skills/telemetry "*) ;; *) fail "python.test.sh: every folder is missing skills/telemetry: '$PYALL'" ;; esac

[ "$fails" -eq 0 ] || { echo "affected.test.sh: $fails failure(s)"; exit 1; }
echo "affected.test.sh: ok"
