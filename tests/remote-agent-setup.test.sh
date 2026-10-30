#!/usr/bin/env bash
# Runs `scripts/remote-agent.sh setup` with answers on stdin in a throwaway XDG_CONFIG_HOME: the config it writes
# for each backend, its mode, that the token never shows, and that an aborted setup leaves no file behind.
# shellcheck disable=SC2015 # `a && b || fail`: fail only reports
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
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
setup $'docker\n\n'"$TOKEN"$'\n' || fail "docker: exited non-zero: $(cat "$SANDBOX/out")"
check_written docker "$TOKEN"
[ "$(value REMOTE_BACKEND)" = docker ] || fail "docker: REMOTE_BACKEND is $(value REMOTE_BACKEND)"
[ "$(value REMOTE_IMAGE)" = ghcr.io/xgeekshq/agent-runner:main ] || fail "docker: default image not written"
grep -q "ghcr.io/xgeekshq/agent-runner:main" "$SANDBOX/out" || fail "docker: default image not shown"
grep -q "claude setup-token" "$SANDBOX/out" || fail "docker: does not say to run claude setup-token"

# SSH, bare mode, from a remote.env written before settings.json: its Settings are the defaults shown and move to
# settings.json, those it does not ask about too; remote.env keeps the token and its other lines.
fresh
printf 'CLAUDE_CODE_OAUTH_TOKEN=old-token\nREMOTE_HOURS=5\nREMOTE_SSH_HOST=old@box\nexport REMOTE_KEEP=1\nOTHER=kept\n' >"$SANDBOX/seed"
mkdir -p "$CFG" && cp "$SANDBOX/seed" "$FILE"
setup $'ssh\n\nbare\n\n' || fail "ssh bare: exited non-zero: $(cat "$SANDBOX/out")"
check_written "ssh bare" old-token
[ "$(value REMOTE_BACKEND)" = ssh ] || fail "ssh bare: REMOTE_BACKEND is $(value REMOTE_BACKEND)"
[ "$(value REMOTE_SSH_HOST)" = old@box ] || fail "ssh bare: REMOTE_SSH_HOST is $(value REMOTE_SSH_HOST), not the old one"
[ "$(value REMOTE_SSH_MODE)" = bare ] || fail "ssh bare: REMOTE_SSH_MODE is $(value REMOTE_SSH_MODE)"
[ "$(value REMOTE_IMAGE)" = unset ] || fail "ssh bare: an image was written"
[ "$(value REMOTE_HOURS)" = 5 ] || fail "ssh bare: a Setting it does not ask about was not moved"
[ "$(value REMOTE_KEEP)" = true ] || fail "ssh bare: an exported Setting was not moved"
grep -qx "OTHER=kept" "$FILE" || fail "ssh bare: a line that is not a Setting was lost: $(cat "$FILE")"

# Switching backend drops the old backend's Settings; enter keeps the token and the Settings it does not ask about.
setup $'docker\n\n\n' || fail "switch: exited non-zero: $(cat "$SANDBOX/out")"
[ "$(value REMOTE_SSH_HOST)" = unset ] || fail "switch: the ssh host was kept"
[ "$(value REMOTE_HOURS)" = 5 ] || fail "switch: hours, not asked about, was lost"
[ "$(value CLAUDE_CODE_OAUTH_TOKEN)" = old-token ] || fail "switch: the token changed"

# SSH, container mode: an empty host and an unknown mode are asked again; the image is asked.
fresh
setup $'ssh\n\nme@box\nweird\ncontainer\nmy/image:1\n'"$TOKEN"$'\n' || fail "ssh container: exited non-zero: $(cat "$SANDBOX/out")"
check_written "ssh container" "$TOKEN"
[ "$(value REMOTE_SSH_HOST)" = me@box ] || fail "ssh container: REMOTE_SSH_HOST is $(value REMOTE_SSH_HOST)"
[ "$(value REMOTE_SSH_MODE)" = container ] || fail "ssh container: REMOTE_SSH_MODE is $(value REMOTE_SSH_MODE)"
[ "$(value REMOTE_IMAGE)" = my/image:1 ] || fail "ssh container: REMOTE_IMAGE is $(value REMOTE_IMAGE)"

# Cluster: the current kube context by default, then namespace and pool; a token with quotes survives.
fresh
odd="sk-it's \$x \"y\""
setup $'\n\nmy-ns\n\n'"$odd"$'\n' || fail "cluster: exited non-zero: $(cat "$SANDBOX/out")"
check_written cluster "$odd"
[ "$(value REMOTE_BACKEND)" = cluster ] || fail "cluster: REMOTE_BACKEND is $(value REMOTE_BACKEND)"
[ "$(value REMOTE_CONTEXT)" = my-ctx ] || fail "cluster: REMOTE_CONTEXT is $(value REMOTE_CONTEXT), not the current context"
[ "$(value REMOTE_NAMESPACE)" = my-ns ] || fail "cluster: REMOTE_NAMESPACE is $(value REMOTE_NAMESPACE)"
[ "$(value REMOTE_POOL)" = claude-runner ] || fail "cluster: REMOTE_POOL is $(value REMOTE_POOL)"
[ "$(value REMOTE_IMAGE)" = unset ] || fail "cluster: an image was written"

# Re-running keeps the saved kube context over the current one (enter through every question).
XDG_CONFIG_HOME="$SANDBOX/config" "$REPO/scripts/settings.sh" --dir "$SANDBOX" set remote context saved-ctx --machine
setup $'\n\n\n\n\n' || fail "cluster re-run: exited non-zero: $(cat "$SANDBOX/out")"
[ "$(value REMOTE_CONTEXT)" = saved-ctx ] || fail "cluster re-run: REMOTE_CONTEXT is $(value REMOTE_CONTEXT), not the saved one"
[ "$(value REMOTE_NAMESPACE)" = my-ns ] || fail "cluster re-run: REMOTE_NAMESPACE is $(value REMOTE_NAMESPACE)"
[ "$(value CLAUDE_CODE_OAUTH_TOKEN)" = "$odd" ] || fail "cluster re-run: the token with quotes did not survive"

# An unknown backend is asked again.
fresh
setup $'nope\ndocker\n\n'"$TOKEN"$'\n' || fail "unknown backend: exited non-zero: $(cat "$SANDBOX/out")"
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

[ "$fails" -eq 0 ] || { echo "remote-agent-setup.test.sh: $fails failure(s)"; exit 1; }
echo "remote-agent-setup.test.sh: ok"
