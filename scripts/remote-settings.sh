#!/usr/bin/env bash
# Sourced by remote-agent.sh, remote-agent-check.sh and remote-agent-setup.sh: loads the remote setup. The Claude
# token is a Secret, in remote.env (written by `remote-agent setup`) or the environment; every other remote Setting
# comes from scripts/settings.sh (section remote), exported as the REMOTE_* variables the backends read. Needs $REPO.

REMOTE_SECRET_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/dotagents/remote.env"

# Remote Setting <-> its variable: ssh_host <-> REMOTE_SSH_HOST.
remote_var() { printf 'REMOTE_%s' "$(printf '%s' "$1" | tr '[:lower:]' '[:upper:]')"; }
remote_key() { printf '%s' "${1#REMOTE_}" | tr '[:upper:]' '[:lower:]'; }

# Prints the Claude token: remote.env's when it sets one, else the environment's; nothing when neither does.
remote_token() {
  # shellcheck disable=SC1090
  (set +u; [ ! -f "$REMOTE_SECRET_FILE" ] || . "$REMOTE_SECRET_FILE" >/dev/null 2>&1; printf '%s' "${CLAUDE_CODE_OAUTH_TOKEN-}")
}

# Prints the REMOTE_* names remote.env still sets, space-separated: Settings from before settings.json, now ignored.
remote_leftovers() {
  [ -f "$REMOTE_SECRET_FILE" ] || return 0
  sed -nE 's/^[[:space:]]*(export[[:space:]]+)?(REMOTE_[A-Za-z0-9_]+)=.*/\2/p' "$REMOTE_SECRET_FILE" | sort -u | paste -sd' ' -
}

# Exports the remote Settings for the repo at $1 as REMOTE_* variables; fails, saying why, on a bad value.
remote_settings() {
  local lines
  lines="$("$REPO/scripts/settings.sh" --dir "$1" env remote)" || return 1
  eval "$lines"
}

# Prints why the repo at $1 may not send subagents to a backend, or nothing when it may. Run after remote_settings.
remote_blocker() {
  local allowed left
  allowed="$("$REPO/scripts/settings.sh" --dir "$1" get remote allowed)" || return 1
  if [ "$allowed" = false ]; then
    echo "remote.allowed is false for $1: its subagents run on this machine"
  elif [ -z "${REMOTE_BACKEND:-}" ]; then
    left="$(remote_leftovers)"
    echo "no remote.backend set: run \`remote-agent setup\`${left:+ (it moves $left out of remote.env)}; until then, subagents run on this machine"
  fi
}
