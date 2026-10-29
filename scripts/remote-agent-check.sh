#!/usr/bin/env bash
# Checks the remote-agent setup, run as `remote-agent check`: remote.env exists and holds a Claude token (never
# printed), the backend's own requirements (`<backend> check`: one line per missing thing), then a dry run: acquire a
# sandbox, put a one-file snapshot in ~/work, run a trivial command that copies it to ~/out and finds claude, get
# ~/out back, compare, release. No Claude call, so no tokens spent. The sandbox is released even when a step fails.
# The dry run is skipped when a requirement is missing. Exit 0 only when everything passes; prints ok at the end.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/dotagents"

say() { echo "remote-agent: $*" >&2; }

if [ ! -f "$CONFIG/remote.env" ]; then
  echo "no $CONFIG/remote.env: run \`remote-agent setup\` (without it, skills run subagents on this machine)"
  exit 1
fi
# shellcheck disable=SC1091
. "$CONFIG/remote.env"
problems=0
if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
  echo "CLAUDE_CODE_OAUTH_TOKEN is not set in $CONFIG/remote.env (from \`claude setup-token\`)"
  problems=1
fi
# The dry run needs no token: keep it away from the backend.
unset CLAUDE_CODE_OAUTH_TOKEN

backend_name="${REMOTE_BACKEND:-cluster}"
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
handle="$("$backend" acquire "$run" </dev/null)" && [ -n "$handle" ] || step_failed "acquire"
export COPYFILE_DISABLE=1
tar -C "$tmp" -cf - ping | "$backend" put "$run" "$handle" work || step_failed "put"
code=0
# shellcheck disable=SC2016 # expanded in the sandbox
claude_path="$("$backend" run "$run" "$handle" sh -c 'mkdir -p "$HOME/out" && cat "$HOME/work/ping" >"$HOME/out/ping" &&
  { command -v claude || exit 3; }' </dev/null)" || code=$?
[ "$code" -ne 3 ] || step_failed "run: claude not found in the sandbox"
[ "$code" -eq 0 ] || step_failed "run (exit $code)"
say "claude in the sandbox: $claude_path"
# Drained after extracting: bsdtar stops at the end-of-archive marker, and the sender's trailing padding would then
# hit a closed pipe and fail the get (SIGPIPE) though the file came back whole.
"$backend" get "$run" "$handle" out </dev/null | { tar -xf - -C "$tmp/back" && cat >/dev/null; } || step_failed "get"
cmp -s "$tmp/ping" "$tmp/back/ping" || step_failed "get: the file that came back differs from the one sent"
release
echo "ok: $backend_name backend ready, dry run passed"
