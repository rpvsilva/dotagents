#!/usr/bin/env bash
# Opt-in smoke test: one tiny real Claude run through scripts/remote-agent.sh on each backend, against the real
# sandbox, with this machine's token, remote Settings and setup. Proves the patch, the transcript and the session come back, the
# session places for /resume, no kept file or output holds the token, and the sandbox is gone afterwards.
# Off unless REMOTE_SMOKE is set, so tests/run.sh and CI stay green without it:
#   REMOTE_SMOKE=1 (or all)        every backend whose own check passes; the others are skipped, with why
#   REMOTE_SMOKE="docker ssh"      just these (spaces or commas); one whose check fails is a failure
# Spends a few Claude tokens per backend. Each run uses this machine's token (remote.env or the environment) and its
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
trap 'rm -rf "$SANDBOX"' EXIT
fails=0
fail() { echo "FAIL: $*"; fails=$((fails + 1)); }

# shellcheck source=../scripts/remote-settings.sh disable=SC1091
. "$REPO/scripts/remote-settings.sh"
token="$(remote_token)"
if [ -z "$token" ]; then
  echo "FAIL: no CLAUDE_CODE_OAUTH_TOKEN in $REMOTE_SECRET_FILE or the environment (remote-agent setup)"
  exit 1
fi
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
    run="$1"; label="sandbox.users.io/remote-agent-run=$run"
    case "$REMOTE_BACKEND" in
      docker) docker ps -aq --filter "label=$label" ;;
      cluster)
        kubectl --context "${REMOTE_CONTEXT:-maxibombo}" -n "${REMOTE_NAMESPACE:-agents}" get sandboxclaim "$run" \
          --ignore-not-found -o jsonpath="{.metadata.name}{\" \"}{.metadata.deletionTimestamp}" | awk "NF == 1" ;;
      ssh)
        if [ "${REMOTE_SSH_MODE:-container}" = bare ]; then
          dir="${REMOTE_SSH_DIR:-.remote-agent/runs}"; dir="${dir#\~/}"
          ssh -T -o BatchMode=yes "$REMOTE_SSH_HOST" "test ! -e \"$dir/$run\" || echo \"$dir/$run\"" </dev/null
        else
          ssh -T -o BatchMode=yes "$REMOTE_SSH_HOST" "docker ps -aq --filter label=$label" </dev/null
        fi ;;
    esac' smoke "$run" 2>&1 || true
}

ran=0
for b in ${wanted[@]+"${wanted[@]}"}; do
  backend="$REPO/scripts/remote-backends/$b.sh"
  if [ "$b" = lib ] || [[ "$b" == */* ]] || [ ! -f "$backend" ]; then
    fail "unknown backend $b in REMOTE_SMOKE (known: ${known[*]})"
    continue
  fi
  W="$SANDBOX/$b/work"
  git init -q "$W"
  git -C "$W" config user.email smoke@example.com
  git -C "$W" config user.name smoke
  printf 'remote-agent smoke test\n' >"$W/README.md"
  git -C "$W" add -A
  git -C "$W" commit -qm base
  if ! why="$(with_settings "$backend" check 2>&1)"; then
    if [ -n "$strict" ]; then
      fail "$b: not configured: $why"
    else
      echo "== $b: skipped, not configured: $(printf '%s' "$why" | paste -sd ';' -)"
    fi
    continue
  fi
  echo "== $b: real run"
  ran=$((ran + 1))

  out="$SANDBOX/$b/out"
  code=0
  (cd "$W" && REMOTE_BACKEND="$b" REMOTE_HOURS=1 "$REPO/scripts/remote-agent.sh" --name "smoke-$b" \
    "Create a file named smoke.txt in the current folder whose only content is the line: smoke ok. Do nothing else.") \
    >"$out" 2>&1 || code=$?
  [ "$code" -eq 0 ] || fail "$b: remote-agent exited $code: $(tail -5 "$out")"

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
  jq -e 'select(.type == "result")' "$keep/transcript.jsonl" >/dev/null 2>&1 || fail "$b: transcript has no result event"
  [ -s "$keep/session.jsonl" ] || fail "$b: no session came back"
  holds_token "$keep" && fail "$b: the token is in a kept file: $(printf '%s\n' "$token" | grep -rlF -f - "$keep")"
  holds_token "$out" && fail "$b: the token is in remote-agent's output"

  # The session places for /resume in the worktree, its cwd the worktree.
  cc="$SANDBOX/$b/claude-config"
  if [ -s "$keep/session.jsonl" ]; then
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
