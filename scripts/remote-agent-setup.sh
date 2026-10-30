#!/usr/bin/env bash
# Guided setup for scripts/remote-agent.sh, run as `remote-agent setup`: asks for the backend and its details, then
# for a Claude token (from `claude setup-token`, read without echo). The answers are remote Settings, written to the
# machine Level through scripts/settings.sh; the token is a Secret, written to ~/.config/dotagents/remote.env readable
# only by the user (mode 600), through a temp file in the same folder moved into place.
# Enter takes the default shown: the machine Level's value, else one left in remote.env from before settings.json,
# else the Declaration's default (for the kube context, the current one first); for the token, it keeps the one
# already in remote.env. Of the Settings it asks about, those the chosen backend does not use are unset; the others
# (hours, keep, ...) stay. REMOTE_* lines left in remote.env move to settings.json, unless the machine Level already
# holds that Setting; remote.env keeps the token and its lines that are not Settings. Reads answers from stdin when
# it is not a terminal (for tests). EOF or Ctrl-C before the end writes nothing: existing files stay as they were.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/dotagents"
FILE="$CONFIG/remote.env"
# The Settings this script asks about; the ones the chosen backend does not use are unset.
ASKED="backend context namespace pool ssh_host ssh_mode image"

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
# shellcheck source=remote-settings.sh disable=SC1091
. "$REPO/scripts/remote-settings.sh"

# Runs scripts/settings.sh outside any repo, so only the machine Level counts.
settings() { "$REPO/scripts/settings.sh" --dir / "$@"; }
# Prints remote.env's own value of variable $1 (the environment's does not count), or nothing.
legacy() {
  [ -f "$FILE" ] || return 0
  # shellcheck disable=SC1090
  (unset "$1" CLAUDE_CODE_OAUTH_TOKEN; set +u; . "$FILE" >/dev/null 2>&1; printf '%s' "${!1-}")
}
# Prints the default for Setting $1: the machine Level's value, else remote.env's, else fallback $2, else the
# Declaration's.
old() {
  local v
  v="$(settings get remote "$1" --machine)"
  [ -n "$v" ] || v="$(legacy "$(remote_var "$1")")"
  [ -n "$v" ] || v="${2:-}"
  [ -n "$v" ] || v="$(jq -r --arg k "$1" '.[$k].default // empty' "$REPO/remote/settings.json")"
  printf '%s' "$v"
}

echo "Sets up remote subagents (the remote Settings in $CONFIG/settings.json, the token in $FILE)." >&2
# A bad value already in settings.json stops here, before anything is asked or written.
settings check >/dev/null || exit 1
# Answers, as "<setting> <value>" lines.
answers=()
old_backend="$(old backend)"
backend="$(ask "Backend (cluster, docker, ssh)" "${old_backend:-cluster}" cluster docker ssh)"
answers+=("backend $backend")
mode=""
case "$backend" in
  cluster)
    context="$(ask "Kube context" "$(old context "$(kubectl config current-context 2>/dev/null || true)")")"
    namespace="$(ask "Namespace" "$(old namespace)")"
    pool="$(ask "Warm pool" "$(old pool)")"
    answers+=("context $context" "namespace $namespace" "pool $pool")
    ;;
  ssh)
    host="$(ask "SSH host (user@host or a ~/.ssh/config alias)" "$(old ssh_host)")"
    mode="$(ask "Mode (container, or bare: weaker isolation)" "$(old ssh_mode)" container bare)"
    answers+=("ssh_host $host" "ssh_mode $mode")
    ;;
esac
if [ "$backend" = docker ] || [ "$mode" = container ]; then
  image="$(ask "Runner image" "$(old image)")"
  answers+=("image $image")
fi

echo "Get a Claude token: run \`claude setup-token\` in another terminal, then paste it here (it does not show)." >&2
old_token="$(legacy CLAUDE_CODE_OAUTH_TOKEN)"
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

# Every answer is in: from here on, write. First the Settings left in remote.env that it does not ask about.
for name in $(remote_leftovers); do
  key="$(remote_key "$name")"
  [[ " $ASKED " != *" $key "* ]] || continue
  code=0
  current="$(settings get remote "$key" --machine 2>/dev/null)" || code=$?
  if [ "$code" = 2 ]; then
    echo "remote.env: $name is not a remote Setting; dropped" >&2
  elif [ -z "$current" ]; then
    settings set remote "$key" "$(legacy "$name")" --machine || echo "remote.env: $name not moved (see above); dropped" >&2
  fi
done
for key in $ASKED; do
  value=""
  for a in "${answers[@]}"; do [ "${a%% *}" != "$key" ] || value="${a#* }"; done
  if [ -n "$value" ]; then settings set remote "$key" "$value" --machine; else settings unset remote "$key" --machine; fi
done

mkdir -p "$CONFIG"
umask 077
tmp="$(mktemp "$CONFIG/.remote.env.XXXXXX")"
{
  printf '# Written by remote-agent setup: the Claude token only; the remote Settings are in settings.json.\n'
  printf 'CLAUDE_CODE_OAUTH_TOKEN=%s\n' "$(sq "$token")"
  if [ -f "$FILE" ]; then
    # Keep the lines that are neither the token nor a Setting.
    grep -Ev "^[[:space:]]*(export[[:space:]]+)?(REMOTE_[A-Za-z0-9_]+|CLAUDE_CODE_OAUTH_TOKEN)=|^# Written by remote-agent setup" "$FILE" || true
  fi
} >"$tmp"
chmod 600 "$tmp"
mv -f "$tmp" "$FILE"

echo "Wrote the remote Settings to $CONFIG/settings.json, and $FILE (mode 600)."
echo "Next: run \`remote-agent check\` to check the backend with a dry run."
