#!/usr/bin/env bash
# Runs scripts/gh-runner.sh against a stub GitHub API (curl) and a stub docker, with a throwaway App key.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
S="$SANDBOX/state"
mkdir -p "$SANDBOX/bin" "$S"
fails=0

fail() { echo "FAIL: $*"; fails=$((fails + 1)); }

openssl genrsa -out "$SANDBOX/app.pem" 2048 2>/dev/null

# Stub curl: logs each request; answers the App token calls, registration tokens (failing the first
# $S/token-fails ones), and a runner list holding every runner docker started plus one from elsewhere.
cat >"$SANDBOX/bin/curl" <<EOF
#!/usr/bin/env bash
S="$S"
EOF
cat >>"$SANDBOX/bin/curl" <<'EOF'
method=GET
while [ $# -gt 1 ]; do
  case "$1" in -X) method="$2"; shift ;; -H|-d) shift ;; esac
  shift
done
url="$1"
echo "$method $url" >>"$S/requests"
case "$method $url" in
  "GET https://api.test/repos/o/r/installation") echo '{"id":42}' ;;
  "POST https://api.test/app/installations/42/access_tokens") echo '{"token":"ghs_app"}' ;;
  "POST https://api.test/repos/o/r/actions/runners/registration-token")
    left="$(cat "$S/token-fails" 2>/dev/null || echo 0)"
    if [ "$left" -gt 0 ]; then echo $((left - 1)) >"$S/token-fails"; exit 22; fi
    echo '{"token":"reg_secret"}' ;;
  "GET https://api.test/repos/o/r/actions/runners?per_page=100")
    { echo "elsewhere"; cat "$S/names" 2>/dev/null; } |
      jq -Rn '{runners: [inputs | {id: (if . == "elsewhere" then 1 else 100 + input_line_number end), name: .}]}' ;;
  "DELETE https://api.test/repos/o/r/actions/runners/"*) ;;
  *) exit 22 ;;
esac
EOF

# Stub docker: `run` records its argv and RUNNER_TOKEN and the runner's name, then returns (the job is done), or
# with $S/hang sleeps until killed; `ps` lists the running ones; `rm` logs.
cat >"$SANDBOX/bin/docker" <<EOF
#!/usr/bin/env bash
S="$S"
EOF
cat >>"$SANDBOX/bin/docker" <<'EOF'
case "$1" in
  run)
    echo "${*//$'\n'/ }" >>"$S/runs"
    echo "$RUNNER_TOKEN" >>"$S/env-tokens"
    name="$(printf '%s\n' "$@" | grep -A1 -x -- --name | tail -1)"
    echo "$name" >>"$S/names"
    [ ! -e "$S/hang" ] || { touch "$S/running"; sleep 30 & wait; } ;;
  ps) [ ! -e "$S/running" ] || echo c1 ;;
  rm) echo "rm $*" >>"$S/rms" ;;
esac
EOF
chmod +x "$SANDBOX/bin/curl" "$SANDBOX/bin/docker"

# exec: run in the background is then the script itself, so the test can signal it.
run() {
  exec env PATH="$SANDBOX/bin:$PATH" GITHUB_API_URL=https://api.test DOTAGENTS_APP_ID=1 DOTAGENTS_APP_KEY="$SANDBOX/app.pem" \
    XDG_CONFIG_HOME="$SANDBOX/config" RUNNER_RETRY_SECONDS=0 "$@"
}

# --once with two runners: one registration token and one ephemeral container each, then deregistration of
# this run's runners only.
(run RUNNER_COUNT=2 "$REPO/scripts/gh-runner.sh" --once o/r) 2>"$SANDBOX/err" || fail "--once exited non-zero: $(cat "$SANDBOX/err")"
[ "$(grep -c 'registration-token' "$S/requests")" = 2 ] || fail "want 2 registration tokens: $(cat "$S/requests")"
[ "$(wc -l <"$S/runs" | tr -d ' ')" = 2 ] || fail "want 2 containers: $(cat "$S/runs")"
grep -q 'ghcr.io/actions/actions-runner:' "$S/runs" || fail "not GitHub's runner image: $(cat "$S/runs")"
grep -q -- '--ephemeral' "$S/runs" || fail "runner not ephemeral: $(cat "$S/runs")"
grep -q 'https://github.com/o/r .* dotagents' "$S/runs" || fail "runner not registered to o/r with label dotagents: $(cat "$S/runs")"
if grep -q reg_secret "$S/runs"; then fail "registration token in docker's argv"; fi
[ "$(sort -u "$S/env-tokens")" = reg_secret ] || fail "registration token not passed in RUNNER_TOKEN: $(cat "$S/env-tokens")"
[ "$(sort -u "$S/names" | wc -l | tr -d ' ')" = 2 ] || fail "runner names not unique: $(cat "$S/names")"
[ "$(grep -c '^DELETE' "$S/requests")" = 2 ] || fail "want this run's 2 runners removed: $(cat "$S/requests")"
if grep -q 'runners/1$' "$S/requests"; then fail "removed a runner this run did not start"; fi

# A failed registration token is retried, not fatal.
rm -f "$S"/*
echo 1 >"$S/token-fails"
(run RUNNER_COUNT=1 "$REPO/scripts/gh-runner.sh" --once o/r) 2>"$SANDBOX/err" || fail "retry exited non-zero: $(cat "$SANDBOX/err")"
grep -q 'no registration token' "$SANDBOX/err" || fail "failed token not reported: $(cat "$SANDBOX/err")"
[ "$(wc -l <"$S/runs" | tr -d ' ')" = 1 ] || fail "want 1 container after the retry: $(cat "$S/runs" 2>/dev/null)"

# TERM while a runner waits for its job removes its container and deregisters it.
rm -f "$S"/*
touch "$S/hang"
run RUNNER_COUNT=1 "$REPO/scripts/gh-runner.sh" o/r 2>"$SANDBOX/err" &
pid=$!
for _ in $(seq 50); do [ -e "$S/running" ] && break; sleep 0.1; done
kill -TERM "$pid"
code=0
wait "$pid" || code=$?
[ "$code" = 130 ] || fail "TERM exited $code, want 130: $(cat "$SANDBOX/err")"
grep -q 'rm -f c1' "$S/rms" 2>/dev/null || fail "container not removed on TERM: $(cat "$S/rms" 2>/dev/null)"
[ "$(grep -c '^DELETE' "$S/requests")" = 1 ] || fail "runner not deregistered on TERM: $(cat "$S/requests")"

# Bad input fails before touching anything.
if (run RUNNER_COUNT=0 "$REPO/scripts/gh-runner.sh" o/r) 2>/dev/null; then fail "RUNNER_COUNT=0 exited 0"; fi
if (run "$REPO/scripts/gh-runner.sh" notarepo) 2>/dev/null; then fail "bad repo exited 0"; fi

[ "$fails" -eq 0 ] || { echo "gh-runner.test.sh: $fails failure(s)"; exit 1; }
echo "gh-runner.test.sh: ok"
