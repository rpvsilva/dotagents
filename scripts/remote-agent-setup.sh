#!/usr/bin/env bash
# Guided setup for scripts/remote-agent.sh, run as `remote-agent setup`: asks for the backend and its details (for SSH,
# how to log in: a key, from the agent, ~/.ssh/config or a key file with an optional passphrase, or a password), then
# for the harness (claude, codex, opencode or pi) and how it logs in: for claude a subscription token (oauth, from `claude
# setup-token`) or an API key (api_key); for codex an API key; for opencode and pi an Anthropic or OpenAI key
# (anthropic, openai); for any a gateway (LiteLLM, OpenRouter, ...: its URL and key, GATEWAY_API_KEY, shared by every harness; the
# gateway_api Setting is the harness's own API, asked under opencode and pi, which speak several). Secrets are read without echo. The answers are remote Settings (harness, credential, base_url among
# them), written to the machine Level through scripts/settings.sh; the credential, the SSH password and the key
# passphrase are Secrets, written to ~/.config/dotagents/remote.env readable only by the user (mode 600), through a
# temp file in the same folder moved into place. An SSH login method not chosen drops the other's Secret; another
# harness's credential stays, so switching back keeps it.
# Enter takes the default shown: the machine Level's value, else one left in remote.env from before settings.json,
# else the Declaration's default (for the kube context, the current one first); for a Secret, it keeps the one
# already in remote.env (for the key passphrase, none when there is none; `none` drops one). Of the Settings it asks about, those the chosen backend does not use are unset; the others
# (hours, keep, ...) stay. REMOTE_* lines left in remote.env move to settings.json, unless the machine Level already
# holds that Setting; remote.env keeps the token and its lines that are not Settings. Reads answers from stdin when
# it is not a terminal (for tests). EOF or Ctrl-C before the end writes nothing: existing files stay as they were.
# On a terminal with gum (https://github.com/charmbracelet/gum; install.sh installs it: scripts/remote-gum.sh), the questions
# are gum menus and inputs, a review of the answers asks before writing, and it offers to run `remote-agent check` at
# the end; DOTAGENTS_PLAIN=1 keeps the plain prompts.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/dotagents"
FILE="$CONFIG/remote.env"
# The Settings this script asks about; the ones the chosen backend does not use are unset.
ASKED="backend context namespace pool ssh_host ssh_key ssh_mode image harness credential base_url gateway_api"
# The Secrets it may write to remote.env; it rewrites the SSH ones and the chosen credential, and keeps every other line.
SECRETS="CLAUDE_CODE_OAUTH_TOKEN ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN CODEX_API_KEY OPENAI_API_KEY GATEWAY_API_KEY SSH_PASSWORD SSH_KEY_PASSPHRASE"

tmp=""
trap '[ -z "$tmp" ] || rm -f "$tmp"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

abort() { echo >&2; echo "remote-agent setup: stopped, nothing written" >&2; exit 1; }

# gum draws the prompts only where a human answers them: stdin and stderr terminals. GUM is its path
# (scripts/remote-gum.sh path: on PATH, else ~/.agents/bin/gum). Without it there (an install from before install.sh
# installed gum), it offers to install it (scripts/remote-gum.sh install: brew, else the pinned release on Linux and
# macOS), no by default; on Windows it names the command instead.
GUM=""
if [ -t 0 ] && [ -t 2 ] && [ -z "${DOTAGENTS_PLAIN:-}" ]; then
  GUM="$("$REPO/scripts/remote-gum.sh" path)" || GUM=""
  if [ -z "$GUM" ]; then
    how=""
    if command -v brew >/dev/null 2>&1; then how="brew install gum"
    else case "$(uname -s)" in Linux | Darwin) how="download it to ~/.agents/bin" ;; esac; fi
    if [ -n "$how" ]; then
      printf 'Install gum for menus? (%s) [y/N]: ' "$how" >&2
      IFS= read -r reply || abort
      case "$reply" in
        y | Y | yes)
          "$REPO/scripts/remote-gum.sh" install >&2 || true
          GUM="$("$REPO/scripts/remote-gum.sh" path)" || GUM=""
          ;;
      esac
    else
      echo "Tip: with gum (https://github.com/charmbracelet/gum#installation; on Windows \`winget install charmbracelet.gum\`), these questions are menus." >&2
    fi
  fi
fi

# done_q <question> <shown>: under gum, which clears its prompt, leaves the answer on screen.
done_q() {
  [ -n "$GUM" ] || return 0
  if [ -n "${NO_COLOR:-}" ]; then printf '  ✔ %s · %s\n' "$1" "$2" >&2
  else printf '  \033[32m✔\033[0m %s \033[2m·\033[0m %s\n' "$1" "$2" >&2; fi
}


# ask <question> [default] [choices...]: prints the answer, asking until it is non-empty (and one of the choices).
ask() {
  local question="$1" default="${2:-}" answer
  shift 2
  while :; do
    if [ -n "$GUM" ] && [ $# -gt 0 ]; then
      if [ -n "$default" ]; then answer="$("$GUM" choose --header "$question" --selected "$default" "$@")" || abort
      else answer="$("$GUM" choose --header "$question" "$@")" || abort; fi
    elif [ -n "$GUM" ]; then
      answer="$("$GUM" input --header "$question" --value "$default")" || abort
    else
      printf '%s%s: ' "$question" "${default:+ [$default]}" >&2
      IFS= read -r answer || abort
    fi
    answer="${answer:-$default}"
    [ -n "$answer" ] || continue
    if [ $# -eq 0 ] || [[ " $* " == *" $answer "* ]]; then break; fi
    echo "  one of: $*" >&2
  done
  done_q "$question" "$answer"
  printf '%s' "$answer"
}

# ask_secret <prompt> <current> [optional]: prints a Secret read without echo (plain lines when stdin is not a
# terminal). Enter keeps <current>; with none, it asks again, unless optional, where enter (or `none`) means none.
ask_secret() {
  local prompt="$1" current="$2" optional="${3:-}" answer=""
  while :; do
    if [ -n "$current" ]; then prompt="$1 (enter keeps the current one${optional:+, none drops it})"
    elif [ -n "$optional" ]; then prompt="$1 (enter: none)"; fi
    if [ -n "$GUM" ]; then
      answer="$("$GUM" input --password --header "$prompt")" || abort
    elif [ -t 0 ]; then
      printf '%s: ' "$prompt" >&2
      IFS= read -rs answer || abort
      echo >&2
    else
      printf '%s: ' "$prompt" >&2
      IFS= read -r answer || abort
    fi
    if [ -n "$optional" ] && [ "$answer" = none ]; then answer=""; break; fi
    answer="${answer:-$current}"
    [ -z "$answer" ] && [ -z "$optional" ] || break
  done
  done_q "$1" "$([ -n "$answer" ] && echo hidden || echo none)"
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
  # shellcheck disable=SC2086 # the names, split
  (unset "$1" $SECRETS; set +u; . "$FILE" >/dev/null 2>&1; printf '%s' "${!1-}")
}
# Prints the default for Setting $1: the machine Level's value, else remote.env's, else fallback $2, else the
# Declaration's.
old() {
  local v
  v="$(settings get remote "$1" --machine)"
  [ -n "$v" ] || v="$(legacy "$(remote_var "$1")")"
  [ -n "$v" ] || v="${2:-}"
  [ -n "$v" ] || v="$(jq -r --arg k "$1" '.[$k].default // empty' "$REPO/remote/settings.json" | tr -d '\r')"
  printf '%s' "$v"
}

echo "Sets up remote subagents (the remote Settings in $CONFIG/settings.json, the credential in $FILE)." >&2
# A bad value already in settings.json stops here, before anything is asked or written.
settings check >/dev/null || exit 1
# Answers, as "<setting> <value>" lines.
answers=()
old_backend="$(old backend)"
backend="$(ask "Backend (cluster, docker, ssh)" "${old_backend:-cluster}" cluster docker ssh)"
answers+=("backend $backend")
mode=""
password=""
passphrase=""
case "$backend" in
  cluster)
    context="$(ask "Kube context" "$(old context "$(kubectl config current-context 2>/dev/null || true)")")"
    namespace="$(ask "Namespace" "$(old namespace)")"
    pool="$(ask "Warm pool" "$(old pool)")"
    answers+=("context $context" "namespace $namespace" "pool $pool")
    ;;
  ssh)
    host="$(ask "SSH host (user@host or a ~/.ssh/config alias)" "$(old ssh_host)")"
    old_login=key
    [ -z "$(legacy SSH_PASSWORD)" ] || old_login=password
    login="$(ask "Log in with (key, password)" "$old_login" key password)"
    if [ "$login" = key ]; then
      echo "A key file, or agent: the ssh agent's keys or ~/.ssh/config's for the host." >&2
      key="$(ask "Key file" "$(old ssh_key agent)")"
      if [ "$key" != agent ]; then
        answers+=("ssh_key $key")
        passphrase="$(ask_secret "Key passphrase" "$(legacy SSH_KEY_PASSPHRASE)" optional)"
      fi
    else
      password="$(ask_secret "SSH password for $host" "$(legacy SSH_PASSWORD)")"
    fi
    mode="$(ask "Mode (container, or bare: weaker isolation)" "$(old ssh_mode)" container bare)"
    answers+=("ssh_host $host" "ssh_mode $mode")
    ;;
esac
if [ "$backend" = docker ] || [ "$mode" = container ]; then
  image="$(ask "Runner image" "$(old image)")"
  answers+=("image $image")
fi

# The harness and how it logs in: the login's default follows the credential (and gateway) already set.
harness="$(ask "Harness (claude, codex, opencode, pi)" "$(old harness)" claude codex opencode pi)"
answers+=("harness $harness")
old_credential="$(old credential)"
old_url="$(old base_url)"
if [ -n "$old_url" ]; then old_login=gateway
else
  case "$old_credential" in ANTHROPIC_API_KEY | CODEX_API_KEY) old_login=api_key ;; ANTHROPIC_AUTH_TOKEN | GATEWAY_API_KEY) old_login=gateway ;; *) old_login="" ;; esac
fi
if [ "$harness" = claude ]; then
  login="$(ask "Log in with (oauth: a Claude subscription, api_key, gateway)" "${old_login:-oauth}" oauth api_key gateway)"
  case "$login" in oauth) credential=CLAUDE_CODE_OAUTH_TOKEN ;; api_key) credential=ANTHROPIC_API_KEY ;; gateway) credential=GATEWAY_API_KEY ;; esac
elif [ "$harness" = opencode ] || [ "$harness" = pi ]; then
  case "$old_login:$old_credential" in gateway:*) ;; *:OPENAI_API_KEY) old_login=openai ;; *) old_login=anthropic ;; esac
  login="$(ask "Log in with (anthropic: an Anthropic API key, openai: an OpenAI API key, gateway)" "$old_login" anthropic openai gateway)"
  case "$login" in anthropic) credential=ANTHROPIC_API_KEY ;; openai) credential=OPENAI_API_KEY ;; gateway) credential=GATEWAY_API_KEY ;; esac
else
  [ "$old_login" != oauth ] || old_login=""
  login="$(ask "Log in with (api_key, gateway)" "${old_login:-api_key}" api_key gateway)"
  credential=CODEX_API_KEY
  [ "$login" != gateway ] || credential=GATEWAY_API_KEY
fi
answers+=("credential $credential")
if [ "$login" = gateway ]; then
  url_re='^https?://[A-Za-z0-9._~:/?#@!$&()*+,;=%-]+$'
  while :; do
    url="$(ask "Gateway URL (http or https; it must speak $(remote_harness_apis "$harness" | sed 's/ / or /g'), e.g. LiteLLM or OpenRouter)" "$old_url")"
    [[ ! "$url" =~ $url_re ]] || break
    echo "  not an http or https URL" >&2
    old_url=""
  done
  apis="$(remote_harness_apis "$harness")"
  api="${apis%% *}"
  if [ "$api" != "$apis" ]; then
    old_api="$(old gateway_api)"
    [[ " $apis " == *" $old_api "* ]] || old_api="$api"
    # shellcheck disable=SC2086 # the APIs, one word each
    api="$(ask "Gateway API (${apis// /, })" "$old_api" $apis)"
  fi
  answers+=("base_url $url" "gateway_api $api")
fi
case "$credential" in
  CLAUDE_CODE_OAUTH_TOKEN)
    echo "Get a Claude token: run \`claude setup-token\` in another terminal, then paste it here (it does not show)." >&2
    token="$(ask_secret "Claude token" "$(legacy CLAUDE_CODE_OAUTH_TOKEN)")" ;;
  ANTHROPIC_API_KEY) token="$(ask_secret "Anthropic API key" "$(legacy ANTHROPIC_API_KEY)")" ;;
  GATEWAY_API_KEY) token="$(ask_secret "Gateway key (GATEWAY_API_KEY)" "$(legacy GATEWAY_API_KEY)")" ;;
  CODEX_API_KEY) token="$(ask_secret "OpenAI key (CODEX_API_KEY)" "$(legacy CODEX_API_KEY)")" ;;
  OPENAI_API_KEY) token="$(ask_secret "OpenAI API key (OPENAI_API_KEY)" "$(legacy OPENAI_API_KEY)")" ;;
esac

# Under gum, the answers are shown once more and nothing is written unless confirmed.
if [ -n "$GUM" ]; then
  review="$(for a in "${answers[@]}"; do printf '%-12s %s\n' "${a%% *}" "${a#* }"; done
    printf '%-12s %s\n' secret "$credential in remote.env (mode 600)"
    [ -z "$password" ] || printf '%-12s %s\n' secret "SSH_PASSWORD in remote.env"
    [ -z "$passphrase" ] || printf '%-12s %s\n' secret "SSH_KEY_PASSPHRASE in remote.env")"
  "$GUM" style --border rounded --padding "0 1" --margin "1 0" "$review" >&2
  "$GUM" confirm "Write these?" || abort
fi

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
  printf '# Written by remote-agent setup: the Secrets only; the remote Settings are in settings.json.\n'
  printf '%s=%s\n' "$credential" "$(sq "$token")"
  [ -z "$password" ] || printf 'SSH_PASSWORD=%s\n' "$(sq "$password")"
  [ -z "$passphrase" ] || printf 'SSH_KEY_PASSPHRASE=%s\n' "$(sq "$passphrase")"
  if [ -f "$FILE" ]; then
    # Keep the lines that are neither a Secret it writes nor a Setting: another credential stays.
    grep -Ev "^[[:space:]]*(export[[:space:]]+)?(REMOTE_[A-Za-z0-9_]+|SSH_PASSWORD|SSH_KEY_PASSPHRASE|$credential)=|^# Written by remote-agent setup" "$FILE" || true
  fi
} >"$tmp"
chmod 600 "$tmp"
mv -f "$tmp" "$FILE"

echo "Wrote the remote Settings to $CONFIG/settings.json, and $FILE (mode 600)."
if [ -n "$GUM" ]; then
  "$GUM" confirm "Run remote-agent check now (a dry run against the backend)?" || exit 0
  exec "$REPO/scripts/remote-agent.sh" check
fi
echo "Next: run \`remote-agent check\` to check the backend with a dry run."
