#!/usr/bin/env bash
# Runs tests/run.sh in a throwaway repo against a stub shellcheck and docker, and checks which way it lints.
set -euo pipefail
# The caller's EVALS (as under `EVALS=1 tests/run.sh`) would change every run below; each case sets its own.
unset EVALS

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
fails=0

fail() { echo "FAIL: $*"; fails=$((fails + 1)); }

# A repo holding tests/run.sh, the pins, one shell script and one passing suite.
R="$SANDBOX/repo"
mkdir -p "$R/tests" "$R/scripts"
cp "$REPO/tests/run.sh" "$REPO/tests/affected.sh" "$REPO/tests/parallel.sh" "$R/tests/"
printf 'shellcheck *.sh\nok.test.sh\n' >"$R/tests/suites.txt"
echo 0.11.0 >"$R/scripts/shellcheck-version"
echo "koalaman/shellcheck:v0.11.0@sha256:abc" >"$R/scripts/shellcheck-image"
printf '#!/bin/sh\necho hi\n' >"$R/a.sh"
printf '#!/bin/sh\nexit 0\n' >"$R/tests/ok.test.sh"
chmod +x "$R/tests/run.sh" "$R/tests/affected.sh" "$R/tests/ok.test.sh"
git -C "$R" init -q
git -C "$R" add .

# PATH holds only the tools run.sh needs, so a real shellcheck, docker or actionlint stays out of the way.
mkdir "$SANDBOX/real"
for tool in bash cat date dirname env git ln mktemp rm sed sh sort tr uname wc xargs; do
  printf '#!/bin/sh\nexec '\''%s'\'' "$@"\n' "$(command -v "$tool")" >"$SANDBOX/real/$tool"
  chmod +x "$SANDBOX/real/$tool"
done
BASH_BIN="$(command -v bash)"

# Stub shellcheck reports version $1 and logs its lint runs; "none" leaves it out.
# Stub docker logs its runs, and `docker info` fails when $2 is "stopped"; "none" leaves it out.
# Then runs run.sh; $3 is the exit code wanted, $4 a pattern its output must hold.
expect() {
  local version="$1" docker="$2" want="$3" pattern="$4" desc="$1 shellcheck, $2 docker" got=0
  rm -rf "$SANDBOX/stubs" "$SANDBOX/log"
  mkdir "$SANDBOX/stubs"
  if [ "$version" != none ]; then
    cat >"$SANDBOX/stubs/shellcheck" <<EOF
#!/bin/sh
if [ "\$1" = --version ]; then printf 'ShellCheck\r\nversion: $version\r\n'; exit 0; fi
echo "shellcheck \$*" >>"$SANDBOX/log"
EOF
    chmod +x "$SANDBOX/stubs/shellcheck"
  fi
  if [ "$docker" != none ]; then
    cat >"$SANDBOX/stubs/docker" <<EOF
#!/bin/sh
if [ "\$1" = info ]; then [ "$docker" = running ]; exit; fi
echo "docker \$*" >>"$SANDBOX/log"
EOF
    chmod +x "$SANDBOX/stubs/docker"
  fi
  PATH="$SANDBOX/stubs:$SANDBOX/real" "$BASH_BIN" "$R/tests/run.sh" >"$SANDBOX/out" 2>&1 || got=$?
  [ "$got" = "$want" ] || fail "$desc: wanted exit $want, got $got: $(cat "$SANDBOX/out")"
  grep -q -- "$pattern" "$SANDBOX/out" || fail "$desc: output lacks '$pattern': $(cat "$SANDBOX/out")"
}
ran() { cat "$SANDBOX/log" 2>/dev/null || true; }

expect 0.11.0 running 0 "== shellcheck$"
[ "$(ran)" = "shellcheck a.sh tests/affected.sh tests/ok.test.sh tests/parallel.sh tests/run.sh" ] || fail "matching shellcheck: did not lint with it: $(ran)"

expect 0.10.0 running 0 "== shellcheck (koalaman/shellcheck:v0.11.0@sha256:abc)"
case "$(ran)" in
  "docker run --rm -v "*":/mnt:ro -w /mnt koalaman/shellcheck:v0.11.0@sha256:abc a.sh tests/affected.sh tests/ok.test.sh tests/parallel.sh tests/run.sh") ;;
  *) fail "mismatch with docker: not the pinned image on a read-only mount: $(ran)" ;;
esac

expect 0.10.0 stopped 0 "docker daemon is not running, skipped"
[ -z "$(ran)" ] || fail "mismatch with a stopped daemon: still linted: $(ran)"

expect none stopped 0 "docker daemon is not running, skipped"
[ -z "$(ran)" ] || fail "no shellcheck, stopped daemon: still linted: $(ran)"

expect 0.10.0 none 1 "found 0.10.0, need 0.11.0"
expect none none 0 "shellcheck: not installed, skipped"

# The evals step. With no python3 and no evals/pick.py, as above, the skipped line still prints and the status holds.
grep -q '^== evals: skipped (EVALS=1 runs the picked ones, EVALS=all every one):' "$SANDBOX/out" \
  || fail "no evals/pick.py: no skipped line: $(cat "$SANDBOX/out")"

# Stub python3 runs a stub script with sh; stub evals/pick.py logs its arguments and prints $SANDBOX/picked;
# stub evals/compare.py logs its arguments and exits with the code in $SANDBOX/compare-exit.
mkdir "$R/evals"
printf '#!/bin/sh\necho "pick $*" >>"%s/log"\ncat "%s/picked"\n' "$SANDBOX" "$SANDBOX" >"$R/evals/pick.py"
# shellcheck disable=SC2016 # the stub expands these itself
printf '#!/bin/sh\necho "compare $*" >>"%s/log"\nexit "$(cat "%s/compare-exit")"\n' "$SANDBOX" "$SANDBOX" \
  >"$R/evals/compare.py"
printf 'configure/a\nconfigure/b\n' >"$SANDBOX/picked"
echo 0 >"$SANDBOX/compare-exit"

# Runs run.sh with EVALS set to $1 ("unset" leaves it out), $3 the uname it sees and the rest as its arguments;
# $2 is the exit code wanted.
evals() {
  local value="$1" want="$2" os="${3:-Linux}" got=0
  shift 2; [ "$#" -eq 0 ] || shift
  rm -rf "$SANDBOX/stubs" "$SANDBOX/log"
  mkdir "$SANDBOX/stubs"
  # shellcheck disable=SC2016 # the stub expands these itself
  printf '#!/bin/sh\n[ "$1" = -I ] && shift\nexec sh "$@"\n' >"$SANDBOX/stubs/python3"
  printf '#!/bin/sh\necho %s\n' "$os" >"$SANDBOX/stubs/uname"
  chmod +x "$SANDBOX/stubs/python3" "$SANDBOX/stubs/uname"
  if [ "$value" = unset ]; then
    PATH="$SANDBOX/stubs:$SANDBOX/real" "$BASH_BIN" "$R/tests/run.sh" "$@" >"$SANDBOX/out" 2>&1 || got=$?
  else
    EVALS="$value" PATH="$SANDBOX/stubs:$SANDBOX/real" "$BASH_BIN" "$R/tests/run.sh" "$@" >"$SANDBOX/out" 2>&1 || got=$?
  fi
  [ "$got" = "$want" ] || fail "EVALS=$value: wanted exit $want, got $got: $(cat "$SANDBOX/out")"
}
compared() { sed -n 's/^compare //p' "$SANDBOX/log" 2>/dev/null || true; }
picks() { sed -n 's/^pick *//p' "$SANDBOX/log" 2>/dev/null || true; }

evals unset 0
grep -q '^== evals: skipped (EVALS=1 runs the picked ones, EVALS=all every one): configure/a configure/b$' \
  "$SANDBOX/out" || fail "EVALS unset: skipped line does not name the picked cases: $(cat "$SANDBOX/out")"
[ -z "$(compared)" ] || fail "EVALS unset: still compared: $(compared)"

evals 1 0
[ "$(compared)" = "--tool claude --base origin/main --head HEAD configure/a configure/b" ] \
  || fail "EVALS=1: did not compare the picked cases on HEAD against origin/main: $(compared)"

evals 1 0 Linux -- a.sh
[ "$(picks)" = "-- a.sh" ] || fail "EVALS=1 -- a.sh: run.sh did not pass its arguments to pick.py: $(picks)"

# On macOS the suites run twice; the evals step must not.
evals 1 0 Darwin
grep -q '(bash 3.2)' "$SANDBOX/out" || fail "Darwin: no bash 3.2 pass to check against: $(cat "$SANDBOX/out")"
[ "$(compared)" = "--tool claude --base origin/main --head HEAD configure/a configure/b" ] \
  || fail "EVALS=1 on Darwin: not compared exactly once: $(compared)"

evals all 0
[ "$(compared)" = "--tool claude --base origin/main --head HEAD --all" ] \
  || fail "EVALS=all: did not compare every case: $(compared)"

echo 1 >"$SANDBOX/compare-exit"
evals 1 1
echo 0 >"$SANDBOX/compare-exit"

: >"$SANDBOX/picked"
evals 1 0
grep -q '^== evals: no case picked' "$SANDBOX/out" || fail "empty pick: not said: $(cat "$SANDBOX/out")"
[ -z "$(compared)" ] || fail "empty pick: still compared: $(compared)"

# A picked name holding a glob character reaches compare.py as it is, never expanded against the checkout.
mkdir -p "$R/configure/x"
echo 'configure/*' >"$SANDBOX/picked"
evals 1 0
[ "$(compared)" = "--tool claude --base origin/main --head HEAD configure/*" ] \
  || fail "glob in a picked name: expanded: $(compared)"
rm -rf "$R/configure"

evals yes 2
grep -q '^usage: EVALS=1|all' "$SANDBOX/out" || fail "EVALS=yes: no usage line: $(cat "$SANDBOX/out")"
grep -q '^== tests/' "$SANDBOX/out" && fail "EVALS=yes: suites still ran: $(cat "$SANDBOX/out")"

# EVALS=1 that cannot pick: no evals/pick.py, then no python3. It says so and fails the run.
mv "$R/evals/pick.py" "$SANDBOX/pick.py"
evals 1 1
grep -q '^== evals: cannot pick the cases' "$SANDBOX/out" || fail "no evals/pick.py: not said: $(cat "$SANDBOX/out")"
[ -z "$(compared)" ] || fail "no evals/pick.py: still compared: $(compared)"
mv "$SANDBOX/pick.py" "$R/evals/pick.py"
got=0
rm -f "$SANDBOX/stubs/python3" "$SANDBOX/log"
EVALS=1 PATH="$SANDBOX/stubs:$SANDBOX/real" "$BASH_BIN" "$R/tests/run.sh" >"$SANDBOX/out" 2>&1 || got=$?
[ "$got" = 1 ] || fail "no python3: wanted exit 1, got $got: $(cat "$SANDBOX/out")"
grep -q '^== evals: cannot pick the cases' "$SANDBOX/out" || fail "no python3: not said: $(cat "$SANDBOX/out")"
[ -z "$(picks)" ] || fail "no python3: still picked: $(picks)"

[ "$fails" -eq 0 ] || { echo "run.test.sh: $fails failure(s)"; exit 1; }
echo "run.test.sh: ok"
