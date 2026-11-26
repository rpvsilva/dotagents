#!/usr/bin/env bash
# Runs `scripts/remote-agent.sh setup` with answers on stdin in a throwaway XDG_CONFIG_HOME: the config it writes
# for each backend, its mode, that the token never shows, and that an aborted setup leaves no file behind.
# shellcheck disable=SC2015 # `a && b || fail`: fail only reports
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# The runner image the `image` Setting defaults to.
IMAGE="$(jq -r .image.default "$REPO/remote/settings.json" | tr -d '\r')"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
fails=0

fail() { echo "FAIL: $*"; fails=$((fails + 1)); }

TOKEN="sk-claude-setup-secret"
CFG="$SANDBOX/config/dotagents"
FILE="$CFG/remote.env"

# A stub kubectl whose current context is my-ctx.
mkdir -p "$SANDBOX/bin"
cat >"$SANDBOX/bin/kubectl" <<'EOF'
#!/bin/sh
[ "$*" = "config current-context" ] && echo my-ctx
EOF
chmod +x "$SANDBOX/bin/kubectl"

# Runs setup with the given answers, one per line, on stdin; output in $SANDBOX/out.
setup() {
  printf '%s' "$1" | env -i HOME="$SANDBOX" PATH="$SANDBOX/bin:$PATH" XDG_CONFIG_HOME="$SANDBOX/config" \
    "$REPO/scripts/remote-agent.sh" setup >"$SANDBOX/out" 2>&1
}

SETTINGS="$CFG/settings.json"

# Prints the token from remote.env, or a remote Setting (named by its REMOTE_* variable) as the machine Level holds
# it: unset when there is none.
value() {
  if [ "$1" = CLAUDE_CODE_OAUTH_TOKEN ]; then
    [ -f "$FILE" ] || { printf unset; return; }
    # shellcheck disable=SC1090
    (set +u; . "$FILE"; printf '%s' "${CLAUDE_CODE_OAUTH_TOKEN-unset}")
    return
  fi
  local v
  v="$(XDG_CONFIG_HOME="$SANDBOX/config" "$REPO/scripts/settings.sh" --dir "$SANDBOX" get remote \
    "$(printf '%s' "${1#REMOTE_}" | tr '[:upper:]' '[:lower:]')" --machine)"
  printf '%s' "${v:-unset}"
}
# Prints Secret $1 from remote.env, or unset.
secret() {
  [ -f "$FILE" ] || { printf unset; return; }
  # shellcheck disable=SC1090
  (unset "$1"; set +u; . "$FILE"; printf '%s' "${!1-unset}")
}
# Starts a case with neither file.
fresh() { rm -f "$FILE" "$SETTINGS"; }

mode() { stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1"; }

# Checks the file exists with mode 600, holds the token, and the token never showed.
check_written() {
  [ -f "$FILE" ] || { fail "$1: no file written: $(cat "$SANDBOX/out")"; return; }
  # Git Bash on Windows has no Unix modes to check.
  [ -n "${MSYSTEM:-}" ] || [ "$(mode "$FILE")" = 600 ] || fail "$1: mode is $(mode "$FILE"), not 600"
  [ "$(value CLAUDE_CODE_OAUTH_TOKEN)" = "$2" ] || fail "$1: token not stored"
  grep -q "$2" "$SANDBOX/out" && fail "$1: the token showed in the output"
  grep -q "remote-agent check" "$SANDBOX/out" || fail "$1: does not suggest remote-agent check"
  find "$CFG" -name '.remote.env.*' | grep -q . && fail "$1: temp file left behind"
  grep -q "REMOTE_" "$FILE" && fail "$1: a Setting written to remote.env: $(grep REMOTE_ "$FILE")"
  grep -q "$2" "$SETTINGS" 2>/dev/null && fail "$1: the token reached settings.json"
  return 0
}

# Docker: the image, its default shown and taken on enter.
fresh
setup $'docker\n\n\n\n'"$TOKEN"$'\n' || fail "docker: exited non-zero: $(cat "$SANDBOX/out")"
check_written docker "$TOKEN"
[ "$(value REMOTE_BACKEND)" = docker ] || fail "docker: REMOTE_BACKEND is $(value REMOTE_BACKEND)"
[ "$(value REMOTE_IMAGE)" = "$IMAGE" ] || fail "docker: default image not written"
grep -qF "$IMAGE" "$SANDBOX/out" || fail "docker: default image not shown"
grep -q "claude setup-token" "$SANDBOX/out" || fail "docker: does not say to run claude setup-token"

# SSH, bare mode, from a remote.env written before settings.json: its Settings are the defaults shown and move to
# settings.json, those it does not ask about too; remote.env keeps the token and its other lines.
fresh
printf 'CLAUDE_CODE_OAUTH_TOKEN=old-token\nREMOTE_HOURS=5\nREMOTE_SSH_HOST=old@box\nexport REMOTE_KEEP=1\nOTHER=kept\n' >"$SANDBOX/seed"
mkdir -p "$CFG" && cp "$SANDBOX/seed" "$FILE"
setup $'ssh\n\n\n\nbare\n\n\n\n' || fail "ssh bare: exited non-zero: $(cat "$SANDBOX/out")"
check_written "ssh bare" old-token
[ "$(value REMOTE_BACKEND)" = ssh ] || fail "ssh bare: REMOTE_BACKEND is $(value REMOTE_BACKEND)"
[ "$(value REMOTE_SSH_HOST)" = old@box ] || fail "ssh bare: REMOTE_SSH_HOST is $(value REMOTE_SSH_HOST), not the old one"
[ "$(value REMOTE_SSH_MODE)" = bare ] || fail "ssh bare: REMOTE_SSH_MODE is $(value REMOTE_SSH_MODE)"
[ "$(value REMOTE_IMAGE)" = unset ] || fail "ssh bare: an image was written"
[ "$(value REMOTE_HOURS)" = 5 ] || fail "ssh bare: a Setting it does not ask about was not moved"
[ "$(value REMOTE_KEEP)" = true ] || fail "ssh bare: an exported Setting was not moved"
grep -qx "OTHER=kept" "$FILE" || fail "ssh bare: a line that is not a Setting was lost: $(cat "$FILE")"

# Switching backend drops the old backend's Settings; enter keeps the token and the Settings it does not ask about.
setup $'docker\n\n\n\n\n' || fail "switch: exited non-zero: $(cat "$SANDBOX/out")"
[ "$(value REMOTE_SSH_HOST)" = unset ] || fail "switch: the ssh host was kept"
[ "$(value REMOTE_HOURS)" = 5 ] || fail "switch: hours, not asked about, was lost"
[ "$(value CLAUDE_CODE_OAUTH_TOKEN)" = old-token ] || fail "switch: the token changed"

# SSH, container mode: an empty host and an unknown mode are asked again; the image is asked.
fresh
setup $'ssh\n\nme@box\n\n\nweird\ncontainer\nmy/image:1\n\n\n'"$TOKEN"$'\n' || fail "ssh container: exited non-zero: $(cat "$SANDBOX/out")"
check_written "ssh container" "$TOKEN"
[ "$(value REMOTE_SSH_HOST)" = me@box ] || fail "ssh container: REMOTE_SSH_HOST is $(value REMOTE_SSH_HOST)"
[ "$(value REMOTE_SSH_MODE)" = container ] || fail "ssh container: REMOTE_SSH_MODE is $(value REMOTE_SSH_MODE)"
[ "$(value REMOTE_IMAGE)" = my/image:1 ] || fail "ssh container: REMOTE_IMAGE is $(value REMOTE_IMAGE)"
[ "$(value REMOTE_SSH_KEY)" = unset ] || fail "ssh container: agent answered, yet a key file was written"

# SSH logins. A password: an empty one asked again, read like the token, kept in remote.env only.
fresh
setup $'ssh\nme@box\npassword\n\npw-1\nbare\n\n\n'"$TOKEN"$'\n' || fail "ssh password: exited non-zero: $(cat "$SANDBOX/out")"
check_written "ssh password" "$TOKEN"
[ "$(secret SSH_PASSWORD)" = pw-1 ] || fail "ssh password: not stored: $(secret SSH_PASSWORD)"
grep -q "pw-1" "$SANDBOX/out" && fail "ssh password: the password showed in the output"
grep -q "pw-1" "$SETTINGS" && fail "ssh password: the password reached settings.json"
[ "$(value REMOTE_SSH_KEY)" = unset ] || fail "ssh password: a key file was written"
# Enter on every question keeps it all, password login included.
setup $'ssh\n\n\n\n\n\n\n\n' || fail "ssh password again: exited non-zero: $(cat "$SANDBOX/out")"
[ "$(secret SSH_PASSWORD)" = pw-1 ] || fail "ssh password again: not kept: $(secret SSH_PASSWORD)"
# A key file with a passphrase: the password goes.
setup $'ssh\n\nkey\n~/.ssh/box\nphrase-1\n\n\n\n\n' || fail "ssh key: exited non-zero: $(cat "$SANDBOX/out")"
# shellcheck disable=SC2088 # stored as typed
[ "$(value REMOTE_SSH_KEY)" = "~/.ssh/box" ] || fail "ssh key: key file is $(value REMOTE_SSH_KEY)"
[ "$(secret SSH_KEY_PASSPHRASE)" = phrase-1 ] || fail "ssh key: passphrase not stored"
grep -q "phrase-1" "$SANDBOX/out" && fail "ssh key: the passphrase showed in the output"
[ "$(secret SSH_PASSWORD)" = unset ] || fail "ssh key: the password was kept"
# The ssh agent: no key file, no passphrase.
setup $'ssh\n\n\nagent\n\n\n\n\n' || fail "ssh agent: exited non-zero: $(cat "$SANDBOX/out")"
[ "$(value REMOTE_SSH_KEY)" = unset ] || fail "ssh agent: key file kept: $(value REMOTE_SSH_KEY)"
[ "$(secret SSH_KEY_PASSPHRASE)" = unset ] || fail "ssh agent: passphrase kept"
check_written "ssh agent" "$TOKEN"

# Cluster: the current kube context by default, then namespace and pool; the pool has no default, so enter asks
# again; a token with quotes survives.
fresh
odd="sk-it's \$x \"y\""
setup $'\n\nmy-ns\n\nmy-pool\n\n\n'"$odd"$'\n' || fail "cluster: exited non-zero: $(cat "$SANDBOX/out")"
[ "$(grep -o "Warm pool[^:]*:" "$SANDBOX/out" | sort -u)" = "Warm pool:" ] || fail "cluster: the pool prompt offers a default: $(cat "$SANDBOX/out")"
[ "$(grep -o "Warm pool:" "$SANDBOX/out" | wc -l | tr -d ' ')" = 2 ] || fail "cluster: an empty pool was not asked again: $(cat "$SANDBOX/out")"
check_written cluster "$odd"
[ "$(value REMOTE_BACKEND)" = cluster ] || fail "cluster: REMOTE_BACKEND is $(value REMOTE_BACKEND)"
[ "$(value REMOTE_CONTEXT)" = my-ctx ] || fail "cluster: REMOTE_CONTEXT is $(value REMOTE_CONTEXT), not the current context"
[ "$(value REMOTE_NAMESPACE)" = my-ns ] || fail "cluster: REMOTE_NAMESPACE is $(value REMOTE_NAMESPACE)"
[ "$(value REMOTE_POOL)" = my-pool ] || fail "cluster: REMOTE_POOL is $(value REMOTE_POOL)"
[ "$(value REMOTE_IMAGE)" = unset ] || fail "cluster: an image was written"

# Re-running keeps the saved kube context over the current one (enter through every question).
XDG_CONFIG_HOME="$SANDBOX/config" "$REPO/scripts/settings.sh" --dir "$SANDBOX" set remote context saved-ctx --machine
setup $'\n\n\n\n\n\n\n' || fail "cluster re-run: exited non-zero: $(cat "$SANDBOX/out")"
[ "$(value REMOTE_CONTEXT)" = saved-ctx ] || fail "cluster re-run: REMOTE_CONTEXT is $(value REMOTE_CONTEXT), not the saved one"
[ "$(value REMOTE_NAMESPACE)" = my-ns ] || fail "cluster re-run: REMOTE_NAMESPACE is $(value REMOTE_NAMESPACE)"
[ "$(value REMOTE_POOL)" = my-pool ] || fail "cluster re-run: REMOTE_POOL is $(value REMOTE_POOL), not the saved one"
[ "$(value CLAUDE_CODE_OAUTH_TOKEN)" = "$odd" ] || fail "cluster re-run: the token with quotes did not survive"

# Harnesses and logins. Codex with an API key: the harness and credential Settings, the key in remote.env, no gateway;
# the Claude token already there stays, so switching back keeps it.
fresh
mkdir -p "$CFG" && printf 'CLAUDE_CODE_OAUTH_TOKEN=keep-me\n' >"$FILE"
setup $'docker\n\ncodex\n\nsk-codex-1\n' || fail "codex: exited non-zero: $(cat "$SANDBOX/out")"
[ "$(value REMOTE_HARNESS)" = codex ] || fail "codex: REMOTE_HARNESS is $(value REMOTE_HARNESS)"
[ "$(value REMOTE_CREDENTIAL)" = CODEX_API_KEY ] || fail "codex: REMOTE_CREDENTIAL is $(value REMOTE_CREDENTIAL)"
[ "$(value REMOTE_BASE_URL)" = unset ] || fail "codex: a base URL was written"
[ "$(secret CODEX_API_KEY)" = sk-codex-1 ] || fail "codex: the key not stored: $(secret CODEX_API_KEY)"
[ "$(secret CLAUDE_CODE_OAUTH_TOKEN)" = keep-me ] || fail "codex: the Claude token was dropped"
grep -q "sk-codex-1" "$SANDBOX/out" "$SETTINGS" && fail "codex: the key showed in the output or settings.json"
# A gateway for claude: a bad URL asked again; its key is GATEWAY_API_KEY, its API claude's own (anthropic).
setup $'docker\n\nclaude\ngateway\nnot a url\nhttps://gw.example.com/anthropic\ngw-secret\n' || fail "gateway: exited non-zero: $(cat "$SANDBOX/out")"
[ "$(value REMOTE_HARNESS)" = claude ] || fail "gateway: REMOTE_HARNESS is $(value REMOTE_HARNESS)"
[ "$(value REMOTE_CREDENTIAL)" = GATEWAY_API_KEY ] || fail "gateway: REMOTE_CREDENTIAL is $(value REMOTE_CREDENTIAL)"
[ "$(value REMOTE_GATEWAY_API)" = anthropic ] || fail "gateway: REMOTE_GATEWAY_API is $(value REMOTE_GATEWAY_API)"
[ "$(value REMOTE_BASE_URL)" = https://gw.example.com/anthropic ] || fail "gateway: REMOTE_BASE_URL is $(value REMOTE_BASE_URL)"
grep -q "not an http or https URL" "$SANDBOX/out" || fail "gateway: a bad URL was not asked again: $(cat "$SANDBOX/out")"
[ "$(secret GATEWAY_API_KEY)" = gw-secret ] || fail "gateway: the key not stored"
[ "$(secret CODEX_API_KEY)" = sk-codex-1 ] || fail "gateway: the Codex key was dropped"
# Enter through it all keeps the gateway; then oauth drops the URL and takes the kept Claude token on enter.
setup $'docker\n\n\n\n\n\n' || fail "gateway again: exited non-zero: $(cat "$SANDBOX/out")"
[ "$(value REMOTE_BASE_URL)" = https://gw.example.com/anthropic ] || fail "gateway again: the URL was not kept"
[ "$(value REMOTE_CREDENTIAL)" = GATEWAY_API_KEY ] || fail "gateway again: the credential changed to $(value REMOTE_CREDENTIAL)"
# The same gateway for codex: enter keeps the URL and the shared key; the API becomes codex's own.
setup $'docker\n\ncodex\n\n\n\n' || fail "codex gateway: exited non-zero: $(cat "$SANDBOX/out")"
[ "$(value REMOTE_CREDENTIAL)" = GATEWAY_API_KEY ] && [ "$(value REMOTE_GATEWAY_API)" = openai-responses ] \
  && [ "$(value REMOTE_BASE_URL)" = https://gw.example.com/anthropic ] && [ "$(secret GATEWAY_API_KEY)" = gw-secret ] \
  || fail "codex gateway: not the shared gateway with codex's API: $(value REMOTE_CREDENTIAL) $(value REMOTE_GATEWAY_API) $(value REMOTE_BASE_URL)"
setup $'docker\n\nclaude\n\n\n\n' || fail "claude gateway again: exited non-zero: $(cat "$SANDBOX/out")"
setup $'docker\n\n\noauth\n\n' || fail "back to oauth: exited non-zero: $(cat "$SANDBOX/out")"
[ "$(value REMOTE_BASE_URL)" = unset ] && [ "$(value REMOTE_GATEWAY_API)" = unset ] || fail "back to oauth: the gateway was kept"
[ "$(value REMOTE_CREDENTIAL)" = CLAUDE_CODE_OAUTH_TOKEN ] || fail "back to oauth: REMOTE_CREDENTIAL is $(value REMOTE_CREDENTIAL)"
[ "$(secret CLAUDE_CODE_OAUTH_TOKEN)" = keep-me ] || fail "back to oauth: the Claude token was not kept"
# OpenCode: an OpenAI key (its own credential), then a gateway, whose API it asks for (openai-chat on enter, an
# unknown one asked again); the other harnesses' credentials stay.
setup $'docker\n\nopencode\nopenai\nsk-openai-1\n' || fail "opencode: exited non-zero: $(cat "$SANDBOX/out")"
[ "$(value REMOTE_HARNESS)" = opencode ] && [ "$(value REMOTE_CREDENTIAL)" = OPENAI_API_KEY ] && [ "$(value REMOTE_BASE_URL)" = unset ] \
  || fail "opencode: not opencode on OPENAI_API_KEY: $(value REMOTE_HARNESS) $(value REMOTE_CREDENTIAL) $(value REMOTE_BASE_URL)"
[ "$(secret OPENAI_API_KEY)" = sk-openai-1 ] || fail "opencode: the key not stored"
[ "$(secret CLAUDE_CODE_OAUTH_TOKEN)" = keep-me ] && [ "$(secret CODEX_API_KEY)" = sk-codex-1 ] || fail "opencode: another harness's credential was dropped"
grep -q "sk-openai-1" "$SANDBOX/out" "$SETTINGS" && fail "opencode: the key showed in the output or settings.json"
setup $'docker\n\n\n\n\n' || fail "opencode again: exited non-zero: $(cat "$SANDBOX/out")"
[ "$(value REMOTE_CREDENTIAL)" = OPENAI_API_KEY ] || fail "opencode again: enter did not keep openai: $(value REMOTE_CREDENTIAL)"
setup $'docker\n\nopencode\ngateway\nhttps://openrouter.ai/api/v1\n\n\n' || fail "opencode gateway: exited non-zero: $(cat "$SANDBOX/out")"
[ "$(value REMOTE_CREDENTIAL)" = GATEWAY_API_KEY ] && [ "$(value REMOTE_GATEWAY_API)" = openai-chat ] \
  && [ "$(value REMOTE_BASE_URL)" = https://openrouter.ai/api/v1 ] && [ "$(secret GATEWAY_API_KEY)" = gw-secret ] \
  || fail "opencode gateway: not the gateway on openai-chat: $(value REMOTE_CREDENTIAL) $(value REMOTE_GATEWAY_API) $(value REMOTE_BASE_URL)"
setup $'docker\n\nopencode\ngateway\n\nmessages\nanthropic\n\n' || fail "opencode gateway api: exited non-zero: $(cat "$SANDBOX/out")"
[ "$(value REMOTE_GATEWAY_API)" = anthropic ] || fail "opencode gateway api: REMOTE_GATEWAY_API is $(value REMOTE_GATEWAY_API)"
grep -q "one of: openai-chat openai-responses anthropic" "$SANDBOX/out" || fail "opencode gateway api: an unknown API not asked again: $(cat "$SANDBOX/out")"
setup $'docker\n\nopencode\ngateway\n\n\n\n' || fail "opencode gateway api again: exited non-zero: $(cat "$SANDBOX/out")"
[ "$(value REMOTE_GATEWAY_API)" = anthropic ] || fail "opencode gateway api again: enter did not keep anthropic: $(value REMOTE_GATEWAY_API)"
# Pi logs in as OpenCode does: an Anthropic key, then the shared gateway with the API kept on enter.
setup $'docker\n\npi\nanthropic\nsk-ant-1\n' || fail "pi: exited non-zero: $(cat "$SANDBOX/out")"
[ "$(value REMOTE_HARNESS)" = pi ] && [ "$(value REMOTE_CREDENTIAL)" = ANTHROPIC_API_KEY ] && [ "$(value REMOTE_BASE_URL)" = unset ] \
  && [ "$(secret ANTHROPIC_API_KEY)" = sk-ant-1 ] || fail "pi: not pi on ANTHROPIC_API_KEY: $(value REMOTE_HARNESS) $(value REMOTE_CREDENTIAL)"
setup $'docker\n\npi\ngateway\nhttps://openrouter.ai/api/v1\nopenai-chat\n\n' || fail "pi gateway: exited non-zero: $(cat "$SANDBOX/out")"
[ "$(value REMOTE_CREDENTIAL)" = GATEWAY_API_KEY ] && [ "$(value REMOTE_GATEWAY_API)" = openai-chat ] && [ "$(secret GATEWAY_API_KEY)" = gw-secret ] \
  || fail "pi gateway: not the shared gateway on openai-chat: $(value REMOTE_CREDENTIAL) $(value REMOTE_GATEWAY_API)"

# A bad value in settings.json stops setup before it asks anything, naming the key; neither file changes.
fresh
cp "$SANDBOX/seed" "$FILE"
echo '{"remote": {"hours": "soon"}}' >"$SETTINGS"
cp "$SETTINGS" "$SANDBOX/bad.json"
code=0; setup $'docker\n\n\n' || code=$?
[ "$code" != 0 ] || fail "bad settings.json: exited 0"
grep -q "remote.hours" "$SANDBOX/out" || fail "bad settings.json: the key not named: $(cat "$SANDBOX/out")"
cmp -s "$SANDBOX/bad.json" "$SETTINGS" && cmp -s "$SANDBOX/seed" "$FILE" || fail "bad settings.json: a file changed"

# An unknown backend is asked again.
fresh
setup $'nope\ndocker\n\n\n\n'"$TOKEN"$'\n' || fail "unknown backend: exited non-zero: $(cat "$SANDBOX/out")"
[ "$(value REMOTE_BACKEND)" = docker ] || fail "unknown backend: not asked again"

# EOF midway (before any token, or with no token to keep) writes neither file, or leaves the existing ones untouched.
fresh
for answers in "" $'docker\n' $'ssh\nme@box\n' $'docker\n\n'; do
  code=0; setup "$answers" || code=$?
  [ "$code" != 0 ] || fail "EOF after $(printf '%q' "$answers"): exited 0"
  [ ! -e "$FILE" ] && [ ! -e "$SETTINGS" ] || fail "EOF after $(printf '%q' "$answers"): a file was written"
  if find "$SANDBOX/config" -name '.remote.env.*' -o -name '.settings.json.*' | grep -q .; then fail "EOF after $(printf '%q' "$answers"): temp file left behind"; fi
done
cp "$SANDBOX/seed" "$FILE"
echo '{"remote": {"backend": "ssh"}}' >"$SETTINGS"
cp "$SETTINGS" "$SANDBOX/seed.json"
code=0; setup $'docker\n' || code=$?
[ "$code" != 0 ] || fail "EOF with existing files: exited 0"
cmp -s "$SANDBOX/seed" "$FILE" || fail "EOF with existing files: remote.env changed"
cmp -s "$SANDBOX/seed.json" "$SETTINGS" || fail "EOF with existing files: settings.json changed"

# On a terminal without gum (an install from before install.sh installed it), setup offers `brew install gum`: yes
# runs it, no (or enter) does not, and either way the plain questions follow. `script` gives it a terminal; the
# answer comes late, so it reaches the question rather than the terminal's start. Skipped without `script`.
tty_setup() {
  local nogum="" d dirs cmd
  IFS=: read -ra dirs <<<"$PATH"
  for d in "${dirs[@]}"; do [ -x "$d/gum" ] || nogum+="${nogum:+:}$d"; done
  cmd=(env -i HOME="$SANDBOX/tty" PATH="$SANDBOX/brewbin:$nogum" XDG_CONFIG_HOME="$SANDBOX/tty/config" TERM=dumb
    "$REPO/scripts/remote-agent.sh" setup)
  if script --version 2>/dev/null | grep -q util-linux; then
    (sleep 2; printf '%s\n' "$1"; sleep 2) | script -qec "$(printf '%q ' "${cmd[@]}")" /dev/null
  else
    (sleep 2; printf '%s\n' "$1"; sleep 2) | script -q /dev/null "${cmd[@]}"
  fi >"$SANDBOX/tty.out" 2>&1 || true
}
if command -v script >/dev/null 2>&1; then
  mkdir -p "$SANDBOX/brewbin" "$SANDBOX/tty"
  printf '#!/bin/sh\necho "$*" >>"%s/brew.log"\n' "$SANDBOX" >"$SANDBOX/brewbin/brew"
  chmod +x "$SANDBOX/brewbin/brew"
  tty_setup y
  [ "$(cat "$SANDBOX/brew.log" 2>/dev/null)" = "install gum" ] || fail "yes to the gum offer did not run brew install gum: $(cat "$SANDBOX/tty.out")"
  grep -q "Backend (cluster, docker, ssh)" "$SANDBOX/tty.out" || fail "after the gum offer, no plain question: $(cat "$SANDBOX/tty.out")"
  rm -f "$SANDBOX/brew.log"
  tty_setup n
  [ ! -e "$SANDBOX/brew.log" ] || fail "no to the gum offer ran brew"
  grep -q "Install gum for menus?" "$SANDBOX/tty.out" || fail "no gum offer on a terminal: $(cat "$SANDBOX/tty.out")"
else
  echo "remote-agent-setup.test.sh: the gum offer skipped (no script)"
fi
# Without a terminal (every case above), no offer.
grep -q "Install gum" "$SANDBOX/out" && fail "the gum offer showed without a terminal" || true

[ "$fails" -eq 0 ] || { echo "remote-agent-setup.test.sh: $fails failure(s)"; exit 1; }
echo "remote-agent-setup.test.sh: ok"
