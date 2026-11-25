#!/usr/bin/env bash
# Checks the remote-agent setup, run as `remote-agent check`: remote.env exists and holds a Claude token (never
# printed) and no Settings left from before settings.json, a remote.backend is set, the backend's own requirements
# (`<backend> check`: one line per missing thing), then a dry run: acquire a sandbox, put a one-file snapshot in
# ~/work, run a trivial command that copies it to ~/out, finds claude and runs `node --version` and `claude --version`
# (a binary present but broken, e.g. a missing shared library, fails here with its own error), get ~/out back,
# compare, release. No Claude API call, so no tokens spent. The sandbox is released even when a step fails.
# The dry run is skipped when a requirement is missing. Exit 0 only when everything passes; prints ok at the end.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=remote-settings.sh disable=SC1091
. "$REPO/scripts/remote-settings.sh"

say() { echo "remote-agent: $*" >&2; }

if [ ! -f "$REMOTE_SECRET_FILE" ]; then
  echo "no $REMOTE_SECRET_FILE: run \`remote-agent setup\` (without it, skills run subagents on this machine)"
  exit 1
fi
problems=0
if [ -z "$(remote_token)" ]; then
  echo "CLAUDE_CODE_OAUTH_TOKEN is not set in $REMOTE_SECRET_FILE (from \`claude setup-token\`)"
  problems=1
fi
# The dry run needs no token: keep it away from the backend.
unset CLAUDE_CODE_OAUTH_TOKEN
left="$(remote_leftovers)"
if [ -n "$left" ]; then
  echo "$REMOTE_SECRET_FILE still sets $left, now ignored: run \`remote-agent setup\` to move them to settings.json"
  problems=1
fi
remote_settings "$PWD" || exit 1
if [ -z "${REMOTE_BACKEND:-}" ]; then
  echo "no remote.backend set: run \`remote-agent setup\` (until then, skills run subagents on this machine)"
  exit 1
fi

backend_name="$REMOTE_BACKEND"
known=()
for f in "$REPO"/scripts/remote-backends/*.sh; do
  [ "$(basename "$f")" = lib.sh ] || known+=("$(basename "$f" .sh)")
done
backend="$REPO/scripts/remote-backends/$backend_name.sh"
if [ "$backend_name" = lib ] || [[ "$backend_name" == */* ]] || [ ! -f "$backend" ]; then
  echo "unknown REMOTE_BACKEND=$backend_name (known: ${known[*]})"
  exit 1
fi
while IFS= read -r v; do export "${v?}"; done < <(compgen -v REMOTE_)

say "checking the $backend_name backend"
"$backend" check || problems=1
# Each agent's own image (remote.images; the docker backend and ssh in container mode), checked as the backend checks
# its default one. Each agent's own pool (remote.pools; the cluster backend) is checked for its form only, as the
# backend's check does not look at the pool either.
remote_agent_value "" "${REMOTE_POOLS:-}" remote.pools >/dev/null || problems=1
if remote_agent_value "" "${REMOTE_IMAGES:-}" remote.images >/dev/null; then
  case "$backend_name:${REMOTE_SSH_MODE:-container}" in
    docker:* | ssh:container)
      IFS=, read -ra entries <<<"${REMOTE_IMAGES:-}"
      for entry in ${entries[@]+"${entries[@]}"}; do
        say "checking ${entry%%=*}'s image ${entry#*=} (remote.images)"
        REMOTE_IMAGE="${entry#*=}" "$backend" check || problems=1
      done
      ;;
  esac
else
  problems=1
fi
if [ "$problems" -ne 0 ]; then
  echo "dry run skipped: fix the above first"
  exit 1
fi

run="agent-check-$(date +%Y%m%d-%H%M%S)-$RANDOM"
tmp="$(mktemp -d)"
acquired=""
release() {
  [ -z "$acquired" ] || "$backend" release "$run" || true
  acquired=""
}
trap 'release; rm -rf "$tmp"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
step_failed() { echo "dry run failed at $1"; exit 1; }

say "dry run: acquire, put, run, get, release"
printf 'ping %s\n' "$run" >"$tmp/ping"
mkdir "$tmp/back"
acquired=1
handle="$("$backend" acquire "$run" </dev/null)" || step_failed "acquire"
[ -n "$handle" ] || step_failed "acquire"
export COPYFILE_DISABLE=1
# shellcheck disable=SC2154 # remote_tar: set by remote-settings.sh
"${remote_tar[@]}" -C "$tmp" -cf - ping | gzip | "$backend" put "$run" "$handle" work || step_failed "put"
code=0
# shellcheck disable=SC2016 # expanded in the sandbox
found="$("$backend" run "$run" "$handle" sh -c 'mkdir -p "$HOME/out" && cat "$HOME/work/ping" >"$HOME/out/ping" &&
  { command -v claude || exit 3; } &&
  { v=$(node --version 2>&1) || { printf "%s\n" "$v"; exit 4; }; echo "node $v"; } &&
  { v=$(claude --version 2>&1) || { printf "%s\n" "$v"; exit 5; }; echo "claude $v"; }' </dev/null)" || code=$?
# The last line of a failed tool's output: its error.
why="$(printf '%s\n' "$found" | grep . | tail -1 || true)"
case "$code" in
  0) ;;
  3) step_failed "run: claude not found in the sandbox" ;;
  4) step_failed "run: node does not run in the sandbox: ${why:-no output}" ;;
  5) step_failed "run: claude does not run in the sandbox: ${why:-no output}" ;;
  *) step_failed "run (exit $code)" ;;
esac
say "claude in the sandbox: $(printf '%s\n' "$found" | sed -n 1p) ($(printf '%s\n' "$found" | sed -n 's/^claude //p'))"
say "node in the sandbox: $(printf '%s\n' "$found" | sed -n 's/^node //p')"
# Drained after extracting: bsdtar stops at the end-of-archive marker, and the sender's trailing padding would then
# hit a closed pipe and fail the get (SIGPIPE) though the file came back whole.
"$backend" get "$run" "$handle" out </dev/null | { tar -xf - -C "$tmp/back" && cat >/dev/null; } || step_failed "get"
cmp -s "$tmp/ping" "$tmp/back/ping" || step_failed "get: the file that came back differs from the one sent"
release
echo "ok: $backend_name backend ready, dry run passed"
