#!/usr/bin/env bash
# Sourced by remote-agent.sh, remote-agent-check.sh, remote-agent-setup.sh and remote-ssh-askpass.sh: loads the remote
# setup. The harness's credential (remote.credential names it) and the SSH backend's password and key passphrase are
# Secrets, in remote.env (written by `remote-agent setup`) or the environment; every other remote Setting comes from scripts/settings.sh (section remote),
# exported as the REMOTE_* variables the backends read. Needs $REPO.

REMOTE_SECRET_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/dotagents/remote.env"

# The tar that packs what goes to a backend, as an array: without extended attributes (macOS adds
# com.apple.provenance and others to every file, and GNU tar on the backend warns once per file about each) when this
# tar knows --no-xattrs (bsdtar, GNU tar 1.27+); COPYFILE_DISABLE=1 also keeps macOS tar from adding ._ files.
export COPYFILE_DISABLE=1
remote_tar=(tar)
if tar --no-xattrs -cf - -T /dev/null >/dev/null 2>&1; then remote_tar+=(--no-xattrs); fi

# Remote Setting <-> its variable: ssh_host <-> REMOTE_SSH_HOST.
remote_var() { printf 'REMOTE_%s' "$(printf '%s' "$1" | tr '[:lower:]' '[:upper:]')"; }
remote_key() { printf '%s' "${1#REMOTE_}" | tr '[:upper:]' '[:lower:]'; }

# Prints Secret $1 (a credential, SSH_PASSWORD, SSH_KEY_PASSPHRASE): remote.env's when it sets one, else the
# environment's; nothing when neither does.
remote_secret() {
  # shellcheck disable=SC1090
  (set +u; [ ! -f "$REMOTE_SECRET_FILE" ] || . "$REMOTE_SECRET_FILE" >/dev/null 2>&1; printf '%s' "${!1-}")
}

# The harnesses (remote.harness): the agent CLI a run starts in the sandbox. Prints the credentials harness $1 logs in
# with, its default first; fails for an unknown harness.
remote_harness_credentials() {
  case "$1" in
    claude) echo "CLAUDE_CODE_OAUTH_TOKEN ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN" ;;
    codex) echo "CODEX_API_KEY" ;;
    *) return 1 ;;
  esac
}
# A model name a run may pass to its harness: a gateway's names hold / and : (anthropic/claude-opus-5-5).
remote_model_ok() { [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$ ]]; }

# Sets harness to remote.harness (claude when unset) and credential to the Secret the run logs in with
# (remote.credential, else the harness's default), and checks remote.base_url and remote.models; fails, with why set
# to say so, on a value that does not fit. Run after remote_settings.
remote_credential() {
  local names url_re='^https?://[A-Za-z0-9._~:/?#@!$&()*+,;=%-]+$'
  harness="${REMOTE_HARNESS:-claude}"
  names="$(remote_harness_credentials "$harness")" || { why="unknown remote.harness: $harness (known: claude codex)"; return 1; }
  credential="${REMOTE_CREDENTIAL:-${names%% *}}"
  if [[ " $names " != *" $credential "* ]]; then
    why="remote.credential $credential is not one $harness logs in with (one of: $names)"
    return 1
  fi
  if [ -n "${REMOTE_BASE_URL:-}" ] && [[ ! "$REMOTE_BASE_URL" =~ $url_re ]]; then
    why="remote.base_url is not an http or https URL: $REMOTE_BASE_URL"
    return 1
  fi
  # shellcheck disable=SC2034 # why: read by the caller
  remote_model_map "" >/dev/null || { why="$(remote_model_map "" 2>&1 >/dev/null)"; return 1; }
}
# Prints the credential's value (see remote_credential; CLAUDE_CODE_OAUTH_TOKEN before it runs), as remote_secret.
remote_token() { remote_secret "${credential:-CLAUDE_CODE_OAUTH_TOKEN}"; }

# Prints model $1 as remote.models renames it (its name=model entries, joined by commas in REMOTE_MODELS), else as it
# is; fails, naming the entry, on one that is not name=model with plain model names, or a name given twice.
remote_model_map() {
  local entries entry from to out="$1" seen=" "
  IFS=, read -ra entries <<<"${REMOTE_MODELS:-}"
  for entry in ${entries[@]+"${entries[@]}"}; do
    from="${entry%%=*}"
    to="${entry#*=}"
    if [ "$from" = "$entry" ] || ! remote_model_ok "$from" || ! remote_model_ok "$to"; then
      echo "remote-agent: remote.models entry is not name=model: $entry" >&2
      return 1
    fi
    case "$seen" in *" $from "*) echo "remote-agent: remote.models names $from twice" >&2; return 1 ;; esac
    seen="$seen$from "
    [ "$from" != "$1" ] || out="$to"
  done
  printf '%s' "$out"
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

# Prints the value per-agent list $2 (the agent=value entries of REMOTE_IMAGES or REMOTE_POOLS, joined by commas)
# holds for agent $1, or nothing when it names none or $1 is empty; fails, naming Setting $3, on an entry that is not
# agent=value or an agent named twice. Every entry is checked, not only $1's, so a typo shows on any run.
remote_agent_value() {
  local entries entry agent value found="" seen=" "
  IFS=, read -ra entries <<<"$2"
  for entry in ${entries[@]+"${entries[@]}"}; do
    if ! [[ "$entry" =~ ^([a-z0-9-]+)=([^[:space:]=]+)$ ]]; then
      echo "remote-agent: $3 entry is not agent=value: $entry" >&2
      return 1
    fi
    agent="${BASH_REMATCH[1]}"
    value="${BASH_REMATCH[2]}"
    case "$seen" in *" $agent "*) echo "remote-agent: $3 names $agent twice" >&2; return 1 ;; esac
    seen="$seen$agent "
    [ "$agent" != "$1" ] || found="$value"
  done
  printf '%s' "$found"
}

# Points the run at agent $1's own image (remote.images) and pool (remote.pools), over remote.image and remote.pool.
# $2 and $3 are REMOTE_IMAGE and REMOTE_POOL as the environment set them before remote_settings: a value set for the
# one run wins over the agent's. Run after remote_settings; fails, saying why, on a bad entry.
remote_agent_settings() {
  local image pool
  image="$(remote_agent_value "$1" "${REMOTE_IMAGES:-}" remote.images)" || return 1
  pool="$(remote_agent_value "$1" "${REMOTE_POOLS:-}" remote.pools)" || return 1
  if [ -n "$image" ] && [ -z "$2" ]; then export REMOTE_IMAGE="$image"; fi
  if [ -n "$pool" ] && [ -z "$3" ]; then export REMOTE_POOL="$pool"; fi
  return 0
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
