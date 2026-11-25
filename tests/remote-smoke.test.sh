#!/usr/bin/env bash
# Opt-in smoke test: one tiny real run through scripts/remote-agent.sh on each backend, on the harness remote.harness
# (or REMOTE_HARNESS) names, against the real sandbox, with this machine's credential, remote Settings and setup. First
# `remote-agent check`, whose dry run finds the harness's CLI in a real sandbox, so a runner image without it fails
# there, named. Proves the patch, the transcript, result.json (complete, on that harness) and the session come back, no
# kept file or output holds the credential, and the sandbox is gone afterwards. Under claude, also that the session
# places for /resume and the caps kill a command past them; the other harnesses have no shell prefix (result.json's
# capped_off says so) and no resume. On any failure the temporary folder, with every run folder, is kept and named.
# Off unless REMOTE_SMOKE is set, so tests/run.sh and CI stay green without it:
#   REMOTE_SMOKE=1 (or all)        every backend whose own check passes; the others are skipped, with why
#   REMOTE_SMOKE="docker ssh"      just these (spaces or commas); one whose check fails is a failure
# Spends a few model tokens per backend. Each run uses this machine's credential (remote.env or the environment) and its
# remote Settings (scripts/settings.sh), with REMOTE_BACKEND set to the backend under test and REMOTE_HOURS=1.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -z "${REMOTE_SMOKE:-}" ]; then
  echo "remote-smoke.test.sh: skipped (opt in with REMOTE_SMOKE=1, or REMOTE_SMOKE=\"cluster docker ssh\")"
  exit 0
fi

known=()
for f in "$REPO"/scripts/remote-backends/*.sh; do
  [ "$(basename "$f")" = lib.sh ] || known+=("$(basename "$f" .sh)")
done
case "$REMOTE_SMOKE" in
  1|all) wanted=("${known[@]}"); strict="" ;;
  *) read -r -a wanted <<<"${REMOTE_SMOKE//,/ }"; strict=1 ;;
esac

SANDBOX="$(mktemp -d)"
fails=0
# Kept on a failure: each run folder there holds its progress.log, transcript and result.json.
trap 'if [ "$fails" -eq 0 ]; then rm -rf "$SANDBOX"; else echo "remote-smoke.test.sh: kept for a look: $SANDBOX"; fi' EXIT
fail() { echo "FAIL: $*"; fails=$((fails + 1)); }

# shellcheck source=../scripts/remote-settings.sh disable=SC1091
. "$REPO/scripts/remote-settings.sh"
# The harness and its credential, as a run here picks them (remote_credential sets both).
harness=""
credential=""
remote_settings "$REPO" || exit 1
if ! remote_credential; then
  echo "FAIL: $why"
  exit 1
fi
token="$(remote_token)"
if [ -z "$token" ]; then
  echo "FAIL: no $credential in $REMOTE_SECRET_FILE or the environment (remote-agent setup)"
  exit 1
fi
echo "== harness: $harness (credential $credential)"
# True when a file under $1 (a file or folder) holds the token; the token goes to grep through a pipe, never argv.
holds_token() { printf '%s\n' "$token" | grep -rqF -f - "$1"; }

# Runs "$@" with the remote Settings for worktree $W exported, REMOTE_BACKEND=$b and REMOTE_HOURS=1 overriding them.
with_settings() {
  (
    export REMOTE_BACKEND="$b" REMOTE_HOURS=1
    remote_settings "$W" || exit 1
    "$@"
  )
}

# Prints what backend $b left of run $1, if anything: a container, a claim not being deleted, a run folder.
left_behind() {
  local run="$1"
  # shellcheck disable=SC2016 # expanded by the inner shell
  with_settings bash -c '
    . "$2/scripts/remote-backends/lib.sh"
    run="$1"; label="sandbox.users.io/remote-agent-run=$run"
    case "$REMOTE_BACKEND" in
      docker) docker ps -aq --filter "label=$label" ;;
      cluster)
        kubectl --context "${REMOTE_CONTEXT:-}" -n "${REMOTE_NAMESPACE:-agents}" get sandboxclaim "$run" \
          --ignore-not-found -o jsonpath="{.metadata.name}{\" \"}{.metadata.deletionTimestamp}" | awk "NF == 1" ;;
      ssh)
        if [ "${REMOTE_SSH_MODE:-container}" = bare ]; then
          dir="${REMOTE_SSH_DIR:-.remote-agent/runs}"; dir="${dir#\~/}"
          ssh_client; "${ssh_cmd[@]}" "$REMOTE_SSH_HOST" "test ! -e \"$dir/$run\" || echo \"$dir/$run\"" </dev/null
        else
          ssh_client; "${ssh_cmd[@]}" "$REMOTE_SSH_HOST" "docker ps -aq --filter label=$label" </dev/null
        fi ;;
    esac' smoke "$run" "$REPO" 2>&1 || true
}

# Makes a fresh repo at $1 with one commit, for one remote run.
new_repo() {
  git init -q "$1"
  git -C "$1" config user.email smoke@example.com
  git -C "$1" config user.name smoke
  printf 'remote-agent smoke test\n' >"$1/README.md"
  git -C "$1" add -A
  git -C "$1" commit -qm base
}

# The cap case on backend $b, in its own repo: a command past REMOTE_CAP_SECS is killed by the sandbox's shell prefix
# (scripts/capped), and the run still ends, well under Claude's own 2-minute Bash timeout.
cap_case() {
  local C="$SANDBOX/$b/cap" out="$SANDBOX/$b/cap.out" code=0 start elapsed result run keep off
  new_repo "$C"
  start="$(date +%s)"
  (cd "$C" && REMOTE_BACKEND="$b" REMOTE_HOURS=1 REMOTE_CAP_SECS=5 "$REPO/scripts/remote-agent.sh" --name "smoke-cap-$b" \
    "Run exactly this shell command once, in the foreground (not in the background), with no timeout of your own: python3 -c 'import time; time.sleep(100)'. If it fails or is killed, do not retry it and do not investigate; just stop. Do nothing else.") \
    >"$out" 2>&1 || code=$?
  elapsed=$(($(date +%s) - start))
  # 3: the run changed nothing, which is all this case asks of it.
  [ "$code" -eq 0 ] || [ "$code" -eq 3 ] || fail "$b: cap run: remote-agent exited $code: $(tail -5 "$out")"
  [ "$elapsed" -lt 120 ] || fail "$b: cap run took ${elapsed}s, not under 120s"
  if ! result="$(cd "$C" && "$REPO/scripts/remote-agent.sh" result "smoke-cap-$b" 2>&1)"; then
    fail "$b: cap run: no result.json: $result"
    return
  fi
  off="$(printf '%s' "$result" | jq -r '.capped_off // empty' 2>/dev/null)"
  if [ -n "$off" ]; then
    fail "$b: cap run: the sandbox's caps are OFF, it lacks: $off (the runner image needs it; see docs/remote.md)"
    return
  fi
  printf '%s' "$result" | jq -e '.killed == "timeout"' >/dev/null 2>&1 \
    || fail "$b: cap run: result.json does not say killed: timeout: $(printf '%s' "$result" | jq -c '{killed, lost, capped_kills}' 2>&1)"
  run="$(printf '%s' "$result" | jq -r '.run // empty' 2>/dev/null)"
  keep="$C/.git/remote-agent/$run"
  if [ -z "$run" ] || [ ! -d "$keep" ]; then
    fail "$b: cap run: result.json names no kept run folder (run: ${run:-none})"
    return
  fi
  grep -q "capped: killed: timeout" "$keep/capped.log" 2>/dev/null \
    || fail "$b: cap run: capped.log has no timeout kill: $(tail -3 "$keep/capped.log" 2>&1)"
  holds_token "$keep" && fail "$b: cap run: the token is in a kept file"
  holds_token "$out" && fail "$b: cap run: the token is in remote-agent's output"
  local left
  left="$(left_behind "$run")"
  [ -z "$left" ] || fail "$b: cap run: sandbox left behind after the run: $left"
}

ran=0
for b in ${wanted[@]+"${wanted[@]}"}; do
  backend="$REPO/scripts/remote-backends/$b.sh"
  if [ "$b" = lib ] || [[ "$b" == */* ]] || [ ! -f "$backend" ]; then
    fail "unknown backend $b in REMOTE_SMOKE (known: ${known[*]})"
    continue
  fi
  W="$SANDBOX/$b/work"
  new_repo "$W"
  if ! why="$(with_settings "$backend" check 2>&1)"; then
    if [ -n "$strict" ]; then
      fail "$b: not configured: $why"
    else
      echo "== $b: skipped, not configured: $(printf '%s' "$why" | paste -sd ';' -)"
    fi
    continue
  fi
  echo "== $b: check (a dry run: the sandbox has $harness)"
  if ! why="$(cd "$W" && REMOTE_BACKEND="$b" REMOTE_HOURS=1 "$REPO/scripts/remote-agent.sh" check 2>&1)"; then
    fail "$b: remote-agent check failed: $(printf '%s\n' "$why" | grep -v '^ok$' | tail -5)"
    continue
  fi
  echo "== $b: real run"
  ran=$((ran + 1))

  if [ "$harness" = claude ]; then
    echo "== $b: cap run (a 100 s sleep under a 5 s cap)"
    cap_case
  else
    echo "== $b: cap run skipped: $harness has no shell prefix, so the caps are off"
  fi

  out="$SANDBOX/$b/out"
  code=0
  (cd "$W" && REMOTE_BACKEND="$b" REMOTE_HOURS=1 "$REPO/scripts/remote-agent.sh" --name "smoke-$b" \
    "Create a file named smoke.txt in the current folder whose only content is the line: smoke ok. Do nothing else.") \
    >"$out" 2>&1 || code=$?
  [ "$code" -eq 0 ] || fail "$b: remote-agent exited $code: $(grep -v 'remote-agent-pack:' "$out" | tail -12)"

  grep -qx "smoke ok" "$W/smoke.txt" 2>/dev/null || fail "$b: smoke.txt not applied with 'smoke ok'"
  [ -z "$(git -C "$W" log --oneline -1 -- smoke.txt)" ] || fail "$b: patch committed"
  runs="$(ls "$W/.git/remote-agent" 2>/dev/null || true)"
  if [ "$(printf '%s\n' "$runs" | grep -c .)" -ne 1 ]; then
    fail "$b: expected one run kept under .git/remote-agent, found: ${runs:-none}"
    continue
  fi
  keep="$W/.git/remote-agent/$runs"
  grep -q "smoke.txt" "$keep/changes.patch" 2>/dev/null || fail "$b: changes.patch missing or without smoke.txt"
  [ -s "$keep/transcript.jsonl" ] || fail "$b: transcript.jsonl empty"
  jq -e . "$keep/transcript.jsonl" >/dev/null 2>&1 || fail "$b: transcript.jsonl is not JSON lines"
  jq -e --arg h "$harness" '.complete == true and .harness == $h' "$keep/result.json" >/dev/null 2>&1 \
    || fail "$b: result.json is not a complete $harness run: $(jq -c '{harness, complete, status, exit_code}' "$keep/result.json" 2>&1)"
  if [ "$harness" != claude ]; then
    jq -e --arg p "a shell prefix in $harness" '.capped_off == $p' "$keep/result.json" >/dev/null 2>&1 \
      || fail "$b: result.json does not say the caps were off: $(jq -c '.capped_off' "$keep/result.json" 2>&1)"
  fi
  [ -s "$keep/session.jsonl" ] || fail "$b: no session came back"
  holds_token "$keep" && fail "$b: the token is in a kept file: $(printf '%s\n' "$token" | grep -rlF -f - "$keep")"
  holds_token "$out" && fail "$b: the token is in remote-agent's output"

  # The session places for /resume in the worktree, its cwd the worktree.
  cc="$SANDBOX/$b/claude-config"
  if [ "$harness" = claude ] && [ -s "$keep/session.jsonl" ]; then
    (cd "$W" && CLAUDE_CONFIG_DIR="$cc" "$REPO/scripts/remote-agent.sh" resume "$runs") >"$out.resume" 2>&1 \
      || fail "$b: remote-agent resume failed: $(tail -3 "$out.resume")"
    placed="$(find "$cc/projects" -name '*.jsonl' 2>/dev/null | head -1)"
    real="$(cd "$W" && pwd -P)"
    if [ -z "$placed" ]; then
      fail "$b: resume placed no session"
    elif ! jq -se --arg w "$real" '[.[] | objects | select(has("cwd")) | .cwd] | length > 0 and all(. == $w)' \
      "$placed" >/dev/null 2>&1; then
      fail "$b: placed session's cwd is not $real"
    fi
  fi

  left="$(left_behind "$runs")"
  [ -z "$left" ] || fail "$b: sandbox left behind after the run: $left"
done

[ "$ran" -gt 0 ] || [ "$fails" -gt 0 ] || echo "remote-smoke.test.sh: no backend configured, nothing run"
[ "$fails" -eq 0 ] || { echo "remote-smoke.test.sh: $fails failure(s)"; exit 1; }
echo "remote-smoke.test.sh: ok ($ran backend(s) run)"
