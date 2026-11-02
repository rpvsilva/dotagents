#!/usr/bin/env bash
# Runs tests/run.sh in a throwaway repo against a stub shellcheck and docker, and checks which way it lints.
set -euo pipefail

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

[ "$fails" -eq 0 ] || { echo "run.test.sh: $fails failure(s)"; exit 1; }
echo "run.test.sh: ok"
