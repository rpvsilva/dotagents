#!/usr/bin/env bash
# Runs `scripts/remote-agent.sh check` against the stubs in tests/lib/remote-stubs.sh (and a small kubectl stub): each
# missing requirement is named on its own line, the dry run drives acquire, put, run, get, release in that order and
# releases when a step fails, a missing config or token is named, and the token never shows.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
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
printf '#!/bin/sh\necho "claude called" >>"%s/claude-called"\n' "$S" >"$CB/claude"
printf '#!/bin/sh\nexit 0\n' >"$CB/node"
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
runs="$(at "^exec -i $c sh -c .*ping.*command -v claude")" get="$(at "^exec $c sh -c tar -cf .*out")"
release="$(at "^rm -f $c\$")"
if [ "$info" -le 0 ] || [ "$info" -ge "$acquire" ]; then fail "docker: docker info not checked before the dry run: $(cat "$S/calls")"; fi
if ! { [ "$acquire" -gt 0 ] && [ "$acquire" -lt "$put" ] && [ "$put" -lt "$runs" ] && [ "$runs" -lt "$get" ] && [ "$get" -lt "$release" ]; }; then
  fail "docker: dry run not acquire, put, run, get, release in order ($acquire $put $runs $get $release): $(cat "$S/calls")"
fi
[ ! -e "$S/containers/$c" ] || fail "docker: dry run container not removed"
grep -q "^ok" "$SANDBOX/out" || fail "docker: does not end with ok: $(out)"
grep -q "^pull" "$S/calls" && fail "docker: pulled an image already present"
[ ! -e "$S/claude-called" ] || fail "docker: the dry run called claude"
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
grep -q "^pull ghcr.io/rpvsilva/agent-runner:main" "$S/calls" || fail "image pull: not pulled: $(cat "$S/calls")"
: >"$S/image-missing"
: >"$S/no-pull"
code=0; check || code=$?
[ "$code" != 0 ] || fail "image not pullable: exited 0"
grep -q "image ghcr.io/rpvsilva/agent-runner:main" "$SANDBOX/out" || fail "image not pullable: image not named: $(out)"
rm -f "$S/image-missing" "$S/no-pull"

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
printf 'other\nmy-cluster\n' >"$S/contexts"
echo no >"$S/can-exec"
config REMOTE_BACKEND=cluster
code=0; check || code=$?
[ "$code" != 0 ] || fail "cluster can-i: exited 0"
grep -q "pods/exec" "$SANDBOX/out" || fail "cluster can-i: pods/exec permission not named: $(out)"
grep -q "sandboxclaims" "$SANDBOX/out" && fail "cluster can-i: named a permission it has: $(out)"
grep -q -- "--context my-cluster -n agents auth can-i create sandboxclaims" "$S/calls" \
  || fail "cluster can-i: sandboxclaims permission not asked in the namespace: $(cat "$S/calls")"
grep -q "apply" "$S/calls" && fail "cluster can-i: the dry run ran anyway"
config REMOTE_BACKEND=cluster REMOTE_CONTEXT=missing-ctx
code=0; check || code=$?
[ "$code" != 0 ] || fail "cluster context: exited 0"
grep -q "context missing-ctx not found" "$SANDBOX/out" || fail "cluster context: context not named as missing: $(out)"
grep -q "auth can-i" "$S/calls" && fail "cluster context: asked for permissions in a missing context"
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
# The host does not answer.
mkdir -p "$SANDBOX/downbin"
printf '#!/bin/sh\necho "ssh: connect to host build-box: Connection refused" >&2\nexit 255\n' >"$SANDBOX/downbin/ssh"
chmod +x "$SANDBOX/downbin/ssh"
code=0; CHECK_PATH="$SANDBOX/downbin:$CLIENT_PATH" check || code=$?
[ "$code" != 0 ] || fail "ssh unreachable: exited 0"
grep -q "cannot reach me@build-box" "$SANDBOX/out" || fail "ssh unreachable: host not named as unreachable: $(out)"
grep -q "Docker daemon" "$SANDBOX/out" && fail "ssh unreachable: went on to check Docker: $(out)"

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
