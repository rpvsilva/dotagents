#!/usr/bin/env bash
# Runs scripts/remote-agent.sh end to end on each non-cluster backend against stubs (tests/lib/remote-stubs.sh):
# the patch comes back, the exit codes match the cluster's, the token stays off the command line, and the
# sandbox goes away after success, failure and TERM.
# Opt-in real run: REMOTE_DOCKER_IMAGE_TEST=<image> runs one task in a real container of <image> (needs Docker).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/remote-stubs.sh disable=SC1091
. "$REPO/tests/lib/remote-stubs.sh"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
fails=0

fail() { echo "FAIL: $*"; fails=$((fails + 1)); }

S="$SANDBOX/state"
H="$SANDBOX/home"
W="$SANDBOX/work"
mkdir -p "$H" "$SANDBOX/config/dotagents"
stub_docker "$SANDBOX/bin" "$S"
make_worktree "$W"

# Runs remote-agent.sh in $W with the remote.env lines given after the fake run's exit code $1 and patch $2.
run() {
  echo "$1" >"$S/code"
  printf '%s' "$2" >"$S/patch"
  shift 2
  { echo "CLAUDE_CODE_OAUTH_TOKEN=sk-claude-secret"; printf '%s\n' "$@"; } >"$SANDBOX/config/dotagents/remote.env"
  rm -rf "$S/calls" "$S/stdin"
  (cd "$W" && env -u CLAUDE_CODE_OAUTH_TOKEN HOME="$H" PATH="$SANDBOX/bin:$PATH" XDG_CONFIG_HOME="$SANDBOX/config" \
    "$REPO/scripts/remote-agent.sh" "add a hello file") >"$SANDBOX/out" 2>&1
}
# The container a run used: the name given to `docker run`.
container() { sed -n 's/^run .*--name \([^ ]*\) .*/\1/p' "$S/calls" 2>/dev/null | head -1 || true; }
gone() { [ -n "$1" ] && [ ! -e "$S/containers/$1" ] && grep -q "^rm -f $1\$" "$S/calls"; }

# --- docker
run 0 "$HELLO_PATCH" REMOTE_BACKEND=docker || fail "docker: exited non-zero: $(cat "$SANDBOX/out")"
c="$(container)"
[ "$(cat "$W/hello.txt" 2>/dev/null)" = hello ] || fail "docker: patch not applied: $(cat "$SANDBOX/out")"
git -C "$W" diff --quiet HEAD -- tracked.txt || fail "docker: worktree changed beyond the patch"
[ -z "$(git -C "$W" log --oneline -1 -- hello.txt)" ] || fail "docker: patch committed"
grep -q "did the thing" "$SANDBOX/out" || fail "docker: summary not printed"
[ -f "$W/.git/remote-agent/$c/changes.patch" ] || fail "docker: patch not kept under .git/remote-agent/$c"
grep -q "^run -d --rm --name $c .*ghcr.io/xgeekshq/agent-runner:main sleep 7200\$" "$S/calls" \
  || fail "docker: run -d lacks --rm, the default image or sleep 7200: $(grep '^run' "$S/calls")"
grep -q -- "--platform" "$S/calls" && fail "docker: --platform passed though REMOTE_PLATFORM is unset"
grep -q "^exec -i $c .*remote-agent-sandbox" "$S/calls" || fail "docker: sandbox script not run with exec -i"
[ "$(sed -n 1p "$S/stdin")" = sk-claude-secret ] || fail "docker: stdin line 1 is not the Claude token"
[ "$(sed -n '2,$p' "$S/stdin")" = "add a hello file" ] || fail "docker: brief does not follow the token"
grep -q "sk-claude-secret" "$S/calls" "$SANDBOX/out" && fail "docker: the token leaked into docker arguments or the output"
gone "$c" || fail "docker: container $c not removed after success"
rm -f "$W/hello.txt"

# Settings: image, platform and hours.
run 0 "$HELLO_PATCH" REMOTE_BACKEND=docker REMOTE_IMAGE=local/runner:dev REMOTE_PLATFORM=linux/arm64 REMOTE_HOURS=1 \
  || fail "docker settings: exited non-zero: $(cat "$SANDBOX/out")"
grep -q "^run -d --rm .*--platform linux/arm64 local/runner:dev sleep 3600\$" "$S/calls" \
  || fail "docker settings: image, platform or hours not used: $(grep '^run' "$S/calls")"
rm -f "$W/hello.txt"

# No changes: exit 3.
code=0; run 0 "" REMOTE_BACKEND=docker || code=$?
[ "$code" = 3 ] || fail "docker no changes: wanted exit 3, got $code"
gone "$(container)" || fail "docker no changes: container not removed"

# A failed run keeps its exit code, applies nothing, and removes the container.
code=0; run 5 "$HELLO_PATCH" REMOTE_BACKEND=docker || code=$?
[ "$code" = 5 ] || fail "docker failed run: wanted exit 5, got $code"
[ ! -e "$W/hello.txt" ] || fail "docker failed run: patch applied anyway"
gone "$(container)" || fail "docker failed run: container not removed"

# A patch that does not apply: exit 4.
code=0; run 0 "$STALE_PATCH" REMOTE_BACKEND=docker || code=$?
[ "$code" = 4 ] || fail "docker stale patch: wanted exit 4, got $code"
[ "$(cat "$W/tracked.txt")" = one ] || fail "docker stale patch: worktree changed"

# REMOTE_KEEP keeps the container and says so.
code=0; run 0 "" REMOTE_BACKEND=docker REMOTE_KEEP=1 || code=$?
c="$(container)"
[ -d "$S/containers/$c" ] || fail "docker keep: container removed"
grep -q "kept container $c" "$SANDBOX/out" || fail "docker keep: not reported: $(cat "$SANDBOX/out")"
rm -rf "${S:?}/containers/$c"

# TERM mid-run removes the container at once, without waiting for the sandbox run to end.
: >"$S/hang"
echo 0 >"$S/code"
: >"$S/patch"
printf 'CLAUDE_CODE_OAUTH_TOKEN=sk-claude-secret\nREMOTE_BACKEND=docker\n' >"$SANDBOX/config/dotagents/remote.env"
rm -rf "$S/calls" "$S/started"
(cd "$W" && exec env -u CLAUDE_CODE_OAUTH_TOKEN HOME="$H" PATH="$SANDBOX/bin:$PATH" XDG_CONFIG_HOME="$SANDBOX/config" \
  "$REPO/scripts/remote-agent.sh" "add a hello file") >"$SANDBOX/out" 2>&1 &
pid=$!
for _ in $(seq 100); do [ -e "$S/started" ] && break; sleep 0.1; done
start=$SECONDS
kill -TERM "$pid"
code=0; wait "$pid" || code=$?
[ "$code" = 143 ] || fail "docker TERM: wanted exit 143, got $code: $(cat "$SANDBOX/out")"
[ $((SECONDS - start)) -lt 10 ] || fail "docker TERM: waited for the sandbox run to end"
gone "$(container)" || fail "docker TERM: container not removed"
rm -f "$S/hang"

# Opt-in: one real task in a real container of the image named, with the real token from the environment.
if [ -n "${REMOTE_DOCKER_IMAGE_TEST:-}" ]; then
  if ! docker info >/dev/null 2>&1 || [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
    fail "real docker: REMOTE_DOCKER_IMAGE_TEST needs Docker running and CLAUDE_CODE_OAUTH_TOKEN set"
  else
    R="$SANDBOX/real"
    make_worktree "$R"
    printf 'REMOTE_BACKEND=docker\nREMOTE_IMAGE=%s\n' "$REMOTE_DOCKER_IMAGE_TEST" >"$SANDBOX/config/dotagents/remote.env"
    (cd "$R" && HOME="$H" XDG_CONFIG_HOME="$SANDBOX/config" \
      "$REPO/scripts/remote-agent.sh" "Create hello.txt containing the word hello.") >"$SANDBOX/out" 2>&1 \
      || fail "real docker: exited non-zero: $(tail -5 "$SANDBOX/out")"
    [ -f "$R/hello.txt" ] || fail "real docker: hello.txt not created"
    left="$(docker ps -aq --filter label=dotagents.remote-agent=1)"
    [ -z "$left" ] || fail "real docker: containers left behind: $left"
  fi
fi

[ "$fails" -eq 0 ] || { echo "remote-backends.test.sh: $fails failure(s)"; exit 1; }
echo "remote-backends.test.sh: ok"
