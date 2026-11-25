#!/usr/bin/env bash
# Runs `scripts/remote-agent.sh check` against the stubs in tests/lib/remote-stubs.sh (and a small kubectl stub): each
# missing requirement is named on its own line, the dry run drives acquire, put, run, get, release in that order and
# releases when a step fails, a missing config or token is named, and the token never shows.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# The runner image the `image` Setting defaults to.
IMAGE="$(jq -r .image.default "$REPO/remote/settings.json" | tr -d '\r')"
# shellcheck source=lib/remote-stubs.sh disable=SC1091
. "$REPO/tests/lib/remote-stubs.sh"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
fails=0

fail() { echo "FAIL: $*"; fails=$((fails + 1)); }

S="$SANDBOX/state"
H="$SANDBOX/home"
CFG="$SANDBOX/config/dotagents"
mkdir -p "$H" "$CFG"
stub_docker "$SANDBOX/bin" "$S"
stub_ssh "$SANDBOX/bin" "$S"

# The client's PATH without any real claude or kubectl; $CB holds a fake claude for the "sandbox" (the stub docker
# runs container commands here).
#
# A folder that holds claude or kubectl (/usr/bin on GitHub's Linux runners, Chocolatey's bin on Windows) cannot be
# dropped whole: it also holds bash, env, jq and the rest. It is left out of the PATH and replaced by $SANDBOX/shim,
# a wrapper for each of its other commands.
CB="$SANDBOX/claudebin"
SHIM="$SANDBOX/shim"
mkdir -p "$CB" "$SHIM"
base_path=""
IFS=: read -ra dirs <<<"$PATH"
for d in "${dirs[@]}"; do
  if [ -x "$d/claude" ] || [ -x "$d/kubectl" ] || [ -x "$d/claude.exe" ] || [ -x "$d/kubectl.exe" ]; then
    for f in "$d"/*; do
      b="${f##*/}"
      case "$b" in claude | claude.* | kubectl | kubectl.*) continue ;; esac
      if [ -f "$f" ] && [ -x "$f" ] && [ ! -e "$SHIM/$b" ]; then
        printf '#!/bin/sh\nexec "%s" "$@"\n' "$f" >"$SHIM/$b"
      fi
    done
    base_path="$base_path:$SHIM"
  else
    base_path="$base_path:$d"
  fi
done
# One chmod for all: such a folder can hold hundreds of commands (Homebrew's bin), and a chmod each takes seconds.
[ -z "$(ls -A "$SHIM")" ] || chmod +x "$SHIM"/*
# Fake claude and node: they log how they were called and print a version, or fail as a binary missing a shared
# library would with $S/claude-broken or $S/node-broken.
cat >"$CB/claude" <<EOF
#!/bin/sh
echo "claude \$*" >>"$S/claude-called"
[ ! -e "$S/claude-broken" ] || { echo "claude: error while loading shared libraries: libfoo.so.1" >&2; exit 127; }
echo "9.9.9 (Claude Code)"
EOF
cat >"$CB/node" <<EOF
#!/bin/sh
[ ! -e "$S/node-broken" ] || { echo "node: error while loading shared libraries: libatomic.so.1" >&2; exit 127; }
echo v99.0.0
EOF
chmod +x "$CB/claude" "$CB/node"
CLIENT_PATH="$SANDBOX/bin:$CB$base_path"

# Writes remote.env with the token, and settings.json with the remote Settings given (see remote_config).
config() { remote_config "$SANDBOX/config" "$@"; }
# Runs `remote-agent check`; its output (stdout and stderr) in $SANDBOX/out, the stub calls in $S/calls.
check() {
  rm -f "$S/calls"
  (cd "$SANDBOX" && env -u CLAUDE_CODE_OAUTH_TOKEN HOME="$H" PATH="${CHECK_PATH:-$CLIENT_PATH}" XDG_CONFIG_HOME="$SANDBOX/config" \
    "$REPO/scripts/remote-agent.sh" check) >"$SANDBOX/out" 2>&1
}
out() { cat "$SANDBOX/out"; }
# Line number in $S/calls of the first call matching the regex $1 (0 when none).
at() { grep -n -m1 -e "$1" "$S/calls" 2>/dev/null | cut -d: -f1 | grep . || echo 0; }
no_token() {
  if grep -q "sk-claude-secret" "$SANDBOX/out" "$S/calls" 2>/dev/null; then fail "$1: the token showed"; fi
}

# --- docker: all there, so the dry run runs the five ops in order and passes.
config REMOTE_BACKEND=docker
check || fail "docker: exited non-zero: $(out)"
c="$(sed -n 's/^run .*--name \([^ ]*\) .*/\1/p' "$S/calls" | head -1)"
info="$(at '^info')" acquire="$(at "^run -d ")" put="$(at "^exec -i $c sh -c mkdir .*work.* tar -x")"
runs="$(at "^exec -i $c sh -c .*ping.*command -v .* remote-agent-check claude$")" get="$(at "^exec $c sh -c tar -cf .*out")"
release="$(at "^rm -f $c\$")"
if [ "$info" -le 0 ] || [ "$info" -ge "$acquire" ]; then fail "docker: docker info not checked before the dry run: $(cat "$S/calls")"; fi
if ! { [ "$acquire" -gt 0 ] && [ "$acquire" -lt "$put" ] && [ "$put" -lt "$runs" ] && [ "$runs" -lt "$get" ] && [ "$get" -lt "$release" ]; }; then
  fail "docker: dry run not acquire, put, run, get, release in order ($acquire $put $runs $get $release): $(cat "$S/calls")"
fi
[ ! -e "$S/containers/$c" ] || fail "docker: dry run container not removed"
grep -q "^ok" "$SANDBOX/out" || fail "docker: does not end with ok: $(out)"
grep -q "^pull" "$S/calls" && fail "docker: pulled an image already present"
[ "$(sort -u "$S/claude-called" 2>/dev/null)" = "claude --version" ] \
  || fail "docker: the dry run called claude other than for its version: $(cat "$S/claude-called" 2>/dev/null)"
grep -q "claude in the sandbox: .*claude (9.9.9 (Claude Code))" "$SANDBOX/out" || fail "docker: claude version not shown: $(out)"
grep -q "node in the sandbox: v99.0.0" "$SANDBOX/out" || fail "docker: node version not shown: $(out)"
# claude or node present but broken: the run step fails with the tool's own error, and the sandbox is still released.
for t in node claude; do
  : >"$S/$t-broken"
  code=0; check || code=$?
  rm -f "$S/$t-broken"
  [ "$code" != 0 ] || fail "broken $t: exited 0"
  grep -q "dry run failed at run: $t does not run in the sandbox: .*error while loading shared libraries" "$SANDBOX/out" \
    || fail "broken $t: not named with its error: $(out)"
  grep -q "^rm -f agent-check-" "$S/calls" || fail "broken $t: sandbox not released: $(cat "$S/calls")"
done
grep -qi "note" "$SANDBOX/out" && fail "docker: an architecture note though the image matches: $(out)"
no_token docker

# The sandbox's tar padding arrives after the local tar stopped reading at the end-of-archive marker (as bsdtar does
# with kubectl): the get still passes, since the file came back whole.
: >"$S/padding"
check || fail "padding: exited non-zero: $(out)"
grep -q "^ok" "$SANDBOX/out" || fail "padding: does not end with ok: $(out)"
rm -f "$S/padding"

# The daemon is down: named on one line, non-zero, and no dry run.
: >"$S/daemon-down"
code=0; check || code=$?
[ "$code" != 0 ] || fail "daemon down: exited 0"
[ "$(grep -c "Docker daemon" "$SANDBOX/out")" = 1 ] || fail "daemon down: Docker daemon not named once: $(out)"
grep -q "^run -d" "$S/calls" && fail "daemon down: the dry run ran anyway"
rm -f "$S/daemon-down"
no_token "daemon down"

# The image is missing: pulled when it can be, named when it cannot.
: >"$S/image-missing"
check || fail "image pull: exited non-zero: $(out)"
grep -q "^pull $IMAGE" "$S/calls" || fail "image pull: not pulled: $(cat "$S/calls")"
: >"$S/image-missing"
: >"$S/no-pull"
code=0; check || code=$?
[ "$code" != 0 ] || fail "image not pullable: exited 0"
grep -q "image $IMAGE" "$SANDBOX/out" || fail "image not pullable: image not named: $(out)"
rm -f "$S/image-missing" "$S/no-pull"

# remote.images: each agent's image is checked too, pulled when missing and named when it cannot be; a bad entry is
# named, with no dry run.
config REMOTE_BACKEND=docker REMOTE_IMAGES="epic-implementer=local/runner-py:1"
check || fail "agent image: exited non-zero: $(out)"
grep -q "^image inspect .*local/runner-py:1" "$S/calls" || fail "agent image: not checked: $(cat "$S/calls")"
grep -q "epic-implementer's image local/runner-py:1" "$SANDBOX/out" || fail "agent image: not named: $(out)"
: >"$S/image-missing"
: >"$S/no-pull"
code=0; check || code=$?
[ "$code" != 0 ] || fail "agent image not pullable: exited 0"
grep -q "image local/runner-py:1" "$SANDBOX/out" || fail "agent image not pullable: not named: $(out)"
rm -f "$S/image-missing" "$S/no-pull"
config REMOTE_BACKEND=docker REMOTE_IMAGES="epic-implementer"
code=0; check || code=$?
[ "$code" != 0 ] || fail "bad images entry: exited 0"
grep -q "remote.images entry is not agent=value: epic-implementer" "$SANDBOX/out" || fail "bad images entry: not named: $(out)"
grep -q "^run -d" "$S/calls" && fail "bad images entry: the dry run ran anyway"
config REMOTE_BACKEND=docker

# An image of another architecture: a note, but still a pass.
echo arm64 >"$S/image-arch"
check || fail "arch: exited non-zero: $(out)"
grep -qi "note.*arm64.*amd64" "$SANDBOX/out" || fail "arch: no note naming both architectures: $(out)"
rm -f "$S/image-arch"

# No claude in the sandbox: the run step fails, it is named, and the sandbox is still released.
code=0; CHECK_PATH="$SANDBOX/bin$base_path" check || code=$?
[ "$code" != 0 ] || fail "no claude: exited 0"
grep -q "claude" "$SANDBOX/out" || fail "no claude: claude not named: $(out)"
c="$(sed -n 's/^run .*--name \([^ ]*\) .*/\1/p' "$S/calls" | head -1)"
grep -q "^rm -f $c\$" "$S/calls" || fail "no claude: sandbox not released: $(cat "$S/calls")"
grep -q "^exec $c sh -c tar -cf" "$S/calls" && fail "no claude: went on to get after a failed run"

# Codex: its key is what remote.env must hold, and the dry run probes codex, not claude; a credential it does not take
# is refused before anything else.
config REMOTE_BACKEND=docker REMOTE_HARNESS=codex
code=0; check || code=$?
[ "$code" != 0 ] || fail "codex no key: exited 0"
grep -q "CODEX_API_KEY is not set" "$SANDBOX/out" || fail "codex no key: not said: $(out)"
grep -q "^run -d " "$S/calls" 2>/dev/null && fail "codex no key: a dry run started"
echo "CODEX_API_KEY=sk-codex-secret" >>"$SANDBOX/config/dotagents/remote.env"
code=0; check || code=$?
[ "$code" != 0 ] || fail "codex missing: exited 0"
grep -q "dry run failed at run: codex not found in the sandbox" "$SANDBOX/out" || fail "codex missing: not named: $(out)"
printf '#!/bin/sh\necho "codex $*" >>"%s/codex-called"\necho "codex-cli 9.9.9"\n' "$S" >"$CB/codex"
chmod +x "$CB/codex"
rm -f "$S/claude-called"
check || fail "codex: exited non-zero: $(out)"
grep -q "codex in the sandbox: .*codex (codex-cli 9.9.9)" "$SANDBOX/out" || fail "codex: version not shown: $(out)"
[ ! -e "$S/claude-called" ] || fail "codex: claude was called: $(cat "$S/claude-called")"
grep -q "sk-codex-secret" "$SANDBOX/out" "$S/calls" && fail "codex: the key showed"
rm -f "$CB/codex"
config REMOTE_BACKEND=docker REMOTE_HARNESS=codex REMOTE_CREDENTIAL=ANTHROPIC_API_KEY
code=0; check || code=$?
# shellcheck disable=SC2015 # fail only reports
[ "$code" = 1 ] && grep -q "not one codex logs in with" "$SANDBOX/out" || fail "codex wrong credential: $code $(out)"
# OpenCode: its default credential is ANTHROPIC_API_KEY, and the dry run probes opencode.
config REMOTE_BACKEND=docker REMOTE_HARNESS=opencode
code=0; check || code=$?
[ "$code" != 0 ] || fail "opencode no key: exited 0"
grep -q "ANTHROPIC_API_KEY is not set" "$SANDBOX/out" || fail "opencode no key: not said: $(out)"
echo "ANTHROPIC_API_KEY=sk-anthropic-secret" >>"$SANDBOX/config/dotagents/remote.env"
printf '#!/bin/sh\necho "opencode $*" >>"%s/opencode-called"\necho "2.3.2"\n' "$S" >"$CB/opencode"
chmod +x "$CB/opencode"
rm -f "$S/claude-called"
check || fail "opencode: exited non-zero: $(out)"
grep -q "opencode in the sandbox: .*opencode (2.3.2)" "$SANDBOX/out" || fail "opencode: version not shown: $(out)"
[ ! -e "$S/claude-called" ] || fail "opencode: claude was called: $(cat "$S/claude-called")"
grep -q "sk-anthropic-secret" "$SANDBOX/out" "$S/calls" && fail "opencode: the key showed"
rm -f "$CB/opencode"
config REMOTE_BACKEND=docker REMOTE_HARNESS=opencode REMOTE_BASE_URL=https://gw.example.com REMOTE_GATEWAY_API=openai-chat \
  REMOTE_CREDENTIAL=GATEWAY_API_KEY
echo "GATEWAY_API_KEY=sk-gw" >>"$SANDBOX/config/dotagents/remote.env"
code=0; check || code=$?
grep -q "remote.gateway_api\|remote.credential\|remote.base_url" "$SANDBOX/out" && fail "opencode gateway: a valid gateway refused: $(out)"
config REMOTE_BACKEND=docker REMOTE_BASE_URL=nope
code=0; check || code=$?
# shellcheck disable=SC2015 # fail only reports
[ "$code" = 1 ] && grep -q "remote.base_url is not an http or https URL" "$SANDBOX/out" || fail "bad base url: $code $(out)"
config REMOTE_BACKEND=docker

# What comes back differs from what was sent: named, and the sandbox is still released.
: >"$S/corrupt"
code=0; check || code=$?
rm -f "$S/corrupt"
[ "$code" != 0 ] || fail "corrupt: exited 0"
grep -q "dry run failed at get" "$SANDBOX/out" || fail "corrupt: the get step not named: $(out)"
grep -q "^rm -f agent-check-" "$S/calls" || fail "corrupt: sandbox not released: $(cat "$S/calls")"

# --- cluster: kubectl's answers decide; a missing permission or context is named and stops before the dry run.
cat >"$SANDBOX/bin/kubectl" <<EOF
#!/bin/sh
echo "\$*" >>"$S/calls"
case "\$*" in
  "config get-contexts -o name") cat "$S/contexts" ;;
  *"auth can-i create pods/exec"*) cat "$S/can-exec"; [ "\$(cat "$S/can-exec")" = yes ] ;;
  *"auth can-i create sandboxclaims"*) echo yes ;;
  *) echo "stub kubectl: unexpected \$*" >&2; exit 1 ;;
esac
EOF
chmod +x "$SANDBOX/bin/kubectl"
printf 'other\ntest-ctx\n' >"$S/contexts"
echo no >"$S/can-exec"
config REMOTE_BACKEND=cluster REMOTE_CONTEXT=test-ctx REMOTE_POOL=test-pool
code=0; check || code=$?
[ "$code" != 0 ] || fail "cluster can-i: exited 0"
grep -q "pods/exec" "$SANDBOX/out" || fail "cluster can-i: pods/exec permission not named: $(out)"
grep -q "sandboxclaims" "$SANDBOX/out" && fail "cluster can-i: named a permission it has: $(out)"
grep -q -- "--context test-ctx -n agents auth can-i create sandboxclaims" "$S/calls" \
  || fail "cluster can-i: sandboxclaims permission not asked in the namespace: $(cat "$S/calls")"
grep -q "apply" "$S/calls" && fail "cluster can-i: the dry run ran anyway"
config REMOTE_BACKEND=cluster REMOTE_CONTEXT=missing-ctx REMOTE_POOL=test-pool
code=0; check || code=$?
[ "$code" != 0 ] || fail "cluster context: exited 0"
grep -q "context missing-ctx not found" "$SANDBOX/out" || fail "cluster context: context not named as missing: $(out)"
grep -q "auth can-i" "$S/calls" && fail "cluster context: asked for permissions in a missing context"
# No default kube context or warm pool: an unset one is named, and kubectl is never asked.
for missing in CONTEXT POOL; do
  if [ "$missing" = CONTEXT ]; then config REMOTE_BACKEND=cluster REMOTE_POOL=test-pool
  else config REMOTE_BACKEND=cluster REMOTE_CONTEXT=test-ctx; fi
  code=0; check || code=$?
  [ "$code" != 0 ] || fail "cluster no $missing: exited 0"
  grep -q "REMOTE_${missing} not set" "$SANDBOX/out" || fail "cluster no $missing: not named: $(out)"
  [ ! -e "$S/calls" ] || fail "cluster no $missing: kubectl was called: $(cat "$S/calls")"
done
no_token cluster

# --- no token file, no backend, no token, settings left in remote.env, unknown backend.
config REMOTE_BACKEND=docker
rm -f "$CFG/remote.env"
code=0; check || code=$?
[ "$code" != 0 ] || fail "no config: exited 0"
grep -q "remote.env" "$SANDBOX/out" || fail "no config: does not name remote.env: $(out)"
grep -q "remote-agent setup" "$SANDBOX/out" || fail "no config: does not point to remote-agent setup: $(out)"
[ ! -e "$S/calls" ] || fail "no config: a backend was called: $(cat "$S/calls")"
config
code=0; check || code=$?
[ "$code" != 0 ] || fail "no backend: exited 0"
grep -q "remote.backend.*remote-agent setup" "$SANDBOX/out" || fail "no backend: does not point to remote-agent setup: $(out)"
[ ! -e "$S/calls" ] || fail "no backend: a backend was called: $(cat "$S/calls")"
config REMOTE_BACKEND=docker
: >"$CFG/remote.env"
code=0; check || code=$?
[ "$code" != 0 ] || fail "no token: exited 0"
grep -q "CLAUDE_CODE_OAUTH_TOKEN" "$SANDBOX/out" || fail "no token: token not named: $(out)"
config REMOTE_BACKEND=docker
echo "REMOTE_POOL=old-pool" >>"$CFG/remote.env"
code=0; check || code=$?
[ "$code" != 0 ] || fail "leftover: exited 0"
grep -q "REMOTE_POOL.*remote-agent setup" "$SANDBOX/out" || fail "leftover: not named with how to move it: $(out)"
config
echo '{"remote": {"backend": "nope"}}' >"$CFG/settings.json"
code=0; check || code=$?
[ "$code" != 0 ] || fail "unknown backend: exited 0"
grep -q "backend is \"nope\".*cluster, docker, ssh" "$SANDBOX/out" || fail "unknown backend: message: $(out)"

# --- ssh, container mode: the docker checks sent to the host over ssh.
config REMOTE_BACKEND=ssh REMOTE_SSH_HOST=me@build-box
check || fail "ssh: exited non-zero: $(out)"
grep -q "^ssh -T -o BatchMode=yes me@build-box 'docker' 'info'" "$S/calls" || fail "ssh: docker info not run on the host: $(cat "$S/calls")"
grep -q "^ok" "$SANDBOX/out" || fail "ssh: does not end with ok: $(out)"
no_token ssh
: >"$S/daemon-down"
code=0; check || code=$?
grep -q "Docker daemon.*me@build-box" "$SANDBOX/out" || fail "ssh daemon down: not named with the host: $(out)"
rm -f "$S/daemon-down"
# An SSH user outside the docker group, with passwordless sudo: check names both fixes; with ssh_sudo it passes.
: >"$S/root-only"
code=0; check || code=$?
[ "$code" != 0 ] || fail "ssh docker group: exited 0"
grep -q "works only through sudo" "$SANDBOX/out" || fail "ssh docker group: sudo case not named: $(out)"
grep -q "settings set remote ssh_sudo true --machine" "$SANDBOX/out" || fail "ssh docker group: ssh_sudo fix not given: $(out)"
grep -q "usermod -aG docker" "$SANDBOX/out" || fail "ssh docker group: docker group fix not given: $(out)"
config REMOTE_BACKEND=ssh REMOTE_SSH_HOST=me@build-box REMOTE_SSH_SUDO=true
check || fail "ssh sudo: exited non-zero: $(out)"
grep -q "^ssh -T -o BatchMode=yes me@build-box 'sudo' '-n' 'docker' 'info'" "$S/calls" \
  || fail "ssh sudo: docker info not run through sudo -n: $(grep '^ssh' "$S/calls" | head -2)"
grep -q "^ok" "$SANDBOX/out" || fail "ssh sudo: does not end with ok: $(out)"
: >"$S/image-missing"; : >"$S/no-pull"
code=0; check || code=$?
grep -q "sudo docker login" "$SANDBOX/out" || fail "ssh sudo no image: root's login not named: $(out)"
rm -f "$S/image-missing" "$S/no-pull"
# sudo wants a password: said so, not taken for a down daemon.
: >"$S/sudo-password"
code=0; check || code=$?
[ "$code" != 0 ] || fail "ssh sudo password: exited 0"
grep -q "sudo without a password" "$SANDBOX/out" || fail "ssh sudo password: not named: $(out)"
rm -f "$S/root-only" "$S/sudo-password"
config REMOTE_BACKEND=ssh REMOTE_SSH_HOST=me@build-box
# The host does not answer.
mkdir -p "$SANDBOX/downbin"
printf '#!/bin/sh\necho "ssh: connect to host build-box: Connection refused" >&2\nexit 255\n' >"$SANDBOX/downbin/ssh"
chmod +x "$SANDBOX/downbin/ssh"
code=0; CHECK_PATH="$SANDBOX/downbin:$CLIENT_PATH" check || code=$?
[ "$code" != 0 ] || fail "ssh unreachable: exited 0"
grep -q "cannot reach me@build-box" "$SANDBOX/out" || fail "ssh unreachable: host not named as unreachable: $(out)"
grep -q "Docker daemon" "$SANDBOX/out" && fail "ssh unreachable: went on to check Docker: $(out)"
grep -q "keys from the ssh agent" "$SANDBOX/out" || fail "ssh unreachable: key login not named: $(out)"
# With a password in remote.env and a key file, the message names both. A relative key file: Git Bash would turn
# /k/box into K:/box on its way through jq.exe.
config REMOTE_BACKEND=ssh REMOTE_SSH_HOST=me@build-box REMOTE_SSH_KEY=keys/box
echo "SSH_PASSWORD=pw-check" >>"$SANDBOX/config/dotagents/remote.env"
code=0; CHECK_PATH="$SANDBOX/downbin:$CLIENT_PATH" check || code=$?
[ "$code" != 0 ] || fail "ssh password unreachable: exited 0"
grep -q "cannot reach me@build-box with the key file keys/box, then the password" "$SANDBOX/out" \
  || fail "ssh password unreachable: login not named: $(out)"
grep -q "pw-check" "$SANDBOX/out" && fail "ssh password unreachable: the password showed"
config REMOTE_BACKEND=ssh REMOTE_SSH_HOST=me@build-box

# --- ssh, bare mode: the host's tools, then a dry run in a run folder.
HH="$SANDBOX/host home"
export STUB_SSH_HOME="$HH" STUB_SSH_PATH="$CB$base_path"
mkdir -p "$HH"
config REMOTE_BACKEND=ssh REMOTE_SSH_HOST=me@trusted-box REMOTE_SSH_MODE=bare
check || fail "bare: exited non-zero: $(out)"
grep -q "^ok" "$SANDBOX/out" || fail "bare: does not end with ok: $(out)"
[ -z "$(ls "$HH/.remote-agent/runs" 2>/dev/null)" ] || fail "bare: run folder left: $(ls "$HH/.remote-agent/runs")"
# A host with bash, tar, git and jq but no node or claude: fakes in a folder of their own, since the machine's own
# PATH may hold node (Homebrew, /usr/bin).
HB="$SANDBOX/hostbin"
mkdir -p "$HB"
for t in bash tar git jq; do printf '#!/bin/sh\nexit 0\n' >"$HB/$t"; chmod +x "$HB/$t"; done
STUB_SSH_PATH="$HB"
code=0; check || code=$?
STUB_SSH_PATH="$CB$base_path"
[ "$code" != 0 ] || fail "bare no tools: exited 0"
[ "$(grep -c "missing on the host" "$SANDBOX/out")" = 2 ] || fail "bare no tools: not one line each for node and claude: $(out)"
grep -q "missing on the host: claude" "$SANDBOX/out" || fail "bare no tools: claude not named: $(out)"
grep -q "mkdir" "$S/calls" && fail "bare no tools: the dry run ran anyway"
unset STUB_SSH_HOME STUB_SSH_PATH

[ "$fails" -eq 0 ] || { echo "remote-agent-check.test.sh: $fails failure(s)"; exit 1; }
echo "remote-agent-check.test.sh: ok"
