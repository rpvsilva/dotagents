#!/usr/bin/env bash
# Guided setup for scripts/remote-agent.sh, run as `remote-agent setup`: asks for the backend and its details, then
# for a Claude token (from `claude setup-token`, read without echo), and writes ~/.config/dotagents/remote.env
# readable only by the user (mode 600), through a temp file in the same folder moved into place.
# Enter takes the default shown: the old file's value, else a fallback (for the kube context, the current one); for
# the token, it keeps the one already in remote.env. Settings it does not ask about (REMOTE_HOURS, REMOTE_KEEP, ...)
# are kept from the old file. Reads answers from stdin when it is not a terminal (for tests). EOF or Ctrl-C before
# the end writes nothing: an existing remote.env stays as it was, and without one skills keep running subagents
# locally.
set -euo pipefail

CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/dotagents"
FILE="$CONFIG/remote.env"
DEFAULT_IMAGE="ghcr.io/xgeekshq/agent-runner:main"
# The settings this script writes; any other line of an existing file is kept.
MANAGED="REMOTE_BACKEND REMOTE_CONTEXT REMOTE_NAMESPACE REMOTE_POOL REMOTE_SSH_HOST REMOTE_SSH_MODE REMOTE_IMAGE CLAUDE_CODE_OAUTH_TOKEN"

tmp=""
trap '[ -z "$tmp" ] || rm -f "$tmp"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

abort() { echo >&2; echo "remote-agent setup: stopped, nothing written" >&2; exit 1; }

# ask <question> [default] [choices...]: prints the answer, asking until it is non-empty (and one of the choices).
ask() {
  local question="$1" default="${2:-}" answer
  shift 2
  while :; do
    printf '%s%s: ' "$question" "${default:+ [$default]}" >&2
    IFS= read -r answer || abort
    answer="${answer:-$default}"
    [ -n "$answer" ] || continue
    if [ $# -eq 0 ] || [[ " $* " == *" $answer "* ]]; then break; fi
    echo "  one of: $*" >&2
  done
  printf '%s' "$answer"
}

# sq quotes a value for the sourced file.
# shellcheck source=remote-backends/lib.sh disable=SC1091
. "$(dirname "${BASH_SOURCE[0]}")/remote-backends/lib.sh"

# Prints setting $1 from the existing file, the default for its question; the environment's own does not count.
old() {
  [ -f "$FILE" ] || return 0
  # shellcheck disable=SC1090,SC2086
  (unset $MANAGED; set +u; . "$FILE" >/dev/null 2>&1; printf '%s' "${!1-}")
}

echo "Sets up remote subagents ($FILE)." >&2
old_backend="$(old REMOTE_BACKEND)"
backend="$(ask "Backend (cluster, docker, ssh)" "${old_backend:-cluster}" cluster docker ssh)"
lines=("REMOTE_BACKEND=$(sq "$backend")")
mode=""
case "$backend" in
  cluster)
    current="$(kubectl config current-context 2>/dev/null || true)"
    old_context="$(old REMOTE_CONTEXT)" old_namespace="$(old REMOTE_NAMESPACE)" old_pool="$(old REMOTE_POOL)"
    context="$(ask "Kube context" "${old_context:-${current:-maxibombo}}")"
    namespace="$(ask "Namespace" "${old_namespace:-agents}")"
    pool="$(ask "Warm pool" "${old_pool:-claude-runner}")"
    lines+=("REMOTE_CONTEXT=$(sq "$context")" "REMOTE_NAMESPACE=$(sq "$namespace")" "REMOTE_POOL=$(sq "$pool")")
    ;;
  ssh)
    old_mode="$(old REMOTE_SSH_MODE)"
    host="$(ask "SSH host (user@host or a ~/.ssh/config alias)" "$(old REMOTE_SSH_HOST)")"
    mode="$(ask "Mode (container, or bare: weaker isolation)" "${old_mode:-container}" container bare)"
    lines+=("REMOTE_SSH_HOST=$(sq "$host")" "REMOTE_SSH_MODE=$(sq "$mode")")
    ;;
esac
if [ "$backend" = docker ] || [ "$mode" = container ]; then
  old_image="$(old REMOTE_IMAGE)"
  image="$(ask "Runner image" "${old_image:-$DEFAULT_IMAGE}")"
  lines+=("REMOTE_IMAGE=$(sq "$image")")
fi

echo "Get a Claude token: run \`claude setup-token\` in another terminal, then paste it here (it does not show)." >&2
old_token="$(old CLAUDE_CODE_OAUTH_TOKEN)"
token=""
while [ -z "$token" ]; do
  printf 'Claude token%s: ' "${old_token:+ (enter keeps the current one)}" >&2
  if [ -t 0 ]; then
    IFS= read -rs token || abort
    echo >&2
  else
    IFS= read -r token || abort
  fi
  token="${token:-$old_token}"
done
lines+=("CLAUDE_CODE_OAUTH_TOKEN=$(sq "$token")")

mkdir -p "$CONFIG"
umask 077
tmp="$(mktemp "$CONFIG/.remote.env.XXXXXX")"
{
  printf '# Written by remote-agent setup.\n'
  printf '%s\n' "${lines[@]}"
  if [ -f "$FILE" ]; then
    # Keep the settings this script does not ask about.
    grep -Ev "^[[:space:]]*(export[[:space:]]+)?(${MANAGED// /|})=|^# Written by remote-agent setup" "$FILE" || true
  fi
} >"$tmp"
chmod 600 "$tmp"
mv -f "$tmp" "$FILE"

echo "Wrote $FILE (mode 600)."
echo "Next: run \`remote-agent check\` to check the backend with a dry run."
