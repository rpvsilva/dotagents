#!/usr/bin/env bash
# Runs scripts/remote-agent.sh end to end on each non-cluster backend against stubs (tests/lib/remote-stubs.sh):
# the patch comes back, the exit codes match the cluster's, the token stays off the command line, and the
# sandbox goes away after success, failure and TERM. The ssh backend runs against a stub ssh that hands the remote
# command to a local `sh -c`, with the docker stub behind it; in bare mode, the stub ssh plays a host whose home is a
# throwaway folder, and the real sandbox script runs there with a fake claude.
# Real runs on each backend: tests/remote-smoke.test.sh (opt-in).
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
W="$SANDBOX/work"
mkdir -p "$H" "$SANDBOX/config/dotagents"
stub_docker "$SANDBOX/bin" "$S"
stub_ssh "$SANDBOX/bin" "$S"
make_worktree "$W"
# This machine's Claude config, where `remote-agent resume` places a run's session.
CC="$SANDBOX/claude-config"

# Runs remote-agent.sh in $W with the remote Settings given (REMOTE_<KEY>=<value>, see remote_config) after the fake
# run's exit code $1 and patch $2; with none given, the config stays as it is. With NAME set, as --name NAME.
run() {
  echo "$1" >"$S/code"
  printf '%s' "$2" >"$S/patch"
  shift 2
  [ $# -eq 0 ] || remote_config "$SANDBOX/config" "$@" || return 1
  rm -rf "$S/calls" "$S/stdin"
  (cd "$W" && env -u CLAUDE_CODE_OAUTH_TOKEN HOME="$H" PATH="$SANDBOX/bin:$PATH" XDG_CONFIG_HOME="$SANDBOX/config" \
    CLAUDE_CONFIG_DIR="$CC" "$REPO/scripts/remote-agent.sh" ${NAME:+--name "$NAME"} "${BRIEF:-add a hello file}") >"$SANDBOX/out" 2>&1
}
# The container a run used: the name given to `docker run`.
container() { sed -n 's/^run .*--name \([^ ]*\) .*/\1/p' "$S/calls" 2>/dev/null | head -1 || true; }
gone() { [ -n "$1" ] && [ ! -e "$S/containers/$1" ] && grep -q "^rm -f $1\$" "$S/calls"; }

# --- docker
run 0 "$HELLO_PATCH" REMOTE_BACKEND=docker || fail "docker: exited non-zero: $(cat "$SANDBOX/out")"
c="$(container)"
[ "$(cat "$W/hello.txt" 2>/dev/null)" = hello ] || fail "docker: patch not applied: $(cat "$SANDBOX/out")"
git -C "$W" diff --quiet HEAD -- tracked.txt || fail "docker: worktree changed beyond the patch"
[ -z "$(git -C "$W" log --oneline -1 -- hello.txt)" ] || fail "docker: patch committed"
grep -q "did the thing" "$SANDBOX/out" || fail "docker: summary not printed"
[ -f "$W/.git/remote-agent/$c/changes.patch" ] || fail "docker: patch not kept under .git/remote-agent/$c"
grep -q "^run -d --rm --name $c .*$IMAGE sleep 7200\$" "$S/calls" \
  || fail "docker: run -d lacks --rm, the default image or sleep 7200: $(grep '^run' "$S/calls")"
grep -q -- "--platform" "$S/calls" && fail "docker: --platform passed though REMOTE_PLATFORM is unset"
grep -q "^run -d .*--label sandbox.users.io/remote-agent-run=$c " "$S/calls" \
  || fail "docker: container not labelled with its run: $(grep '^run' "$S/calls")"
grep -q -- "remote-agent-task" "$S/calls" && fail "docker: task label though no --name"
grep -q "^exec -i $c .*remote-agent-sandbox" "$S/calls" || fail "docker: sandbox script not run with exec -i"
[ "$(sed -n 1p "$S/stdin")" = sk-claude-secret ] || fail "docker: stdin line 1 is not the Claude token"
[ "$(sed -n '2,$p' "$S/stdin")" = "add a hello file" ] || fail "docker: brief does not follow the token"
grep -q "sk-claude-secret" "$S/calls" "$SANDBOX/out" && fail "docker: the token leaked into docker arguments or the output"
gone "$c" || fail "docker: container $c not removed after success"
rm -f "$W/hello.txt"

# Each run gets its share of the host: its memory (no swap past it) and CPUs over max_sandboxes (4): 16 GiB and 8 CPUs.
grep -q "^run -d .*--memory 4096m --memory-swap 4096m --cpus 2 $IMAGE " "$S/calls" \
  || fail "docker share: not the host over max_sandboxes: $(grep '^run' "$S/calls")"
grep -q "started container $c ($IMAGE; 4096 MB, 2 CPUs)" "$SANDBOX/out" || fail "docker share: not said: $(cat "$SANDBOX/out")"
rm -f "$W/hello.txt"
# The Settings set it instead; a share under 2048 MB is said, and CPUs that are not a number start nothing.
run 0 "$HELLO_PATCH" REMOTE_BACKEND=docker REMOTE_SANDBOX_MB=1500 REMOTE_SANDBOX_CPUS=1.5 \
  || fail "docker share settings: exited non-zero: $(cat "$SANDBOX/out")"
grep -q "^run -d .*--memory 1500m --memory-swap 1500m --cpus 1.5 $IMAGE " "$S/calls" \
  || fail "docker share settings: not used: $(grep '^run' "$S/calls")"
grep -q "each run gets 1500 MB, under 2048 MB" "$SANDBOX/out" || fail "docker small share: not said: $(cat "$SANDBOX/out")"
rm -f "$W/hello.txt"
echo 2 >"$S/daemon-cpus"
run 0 "$HELLO_PATCH" REMOTE_BACKEND=docker REMOTE_MAX_SANDBOXES=8 \
  || fail "docker share of 8: exited non-zero: $(cat "$SANDBOX/out")"
grep -q "^run -d .*--memory 2048m --memory-swap 2048m --cpus 0.25 $IMAGE " "$S/calls" \
  || fail "docker share of 8: not the host over 8: $(grep '^run' "$S/calls")"
rm -f "$W/hello.txt" "$S/daemon-cpus"
code=0; run 0 "$HELLO_PATCH" REMOTE_BACKEND=docker REMOTE_SANDBOX_CPUS=two || code=$?
[ "$code" != 0 ] || fail "docker bad cpus: not refused"
grep -q "REMOTE_SANDBOX_CPUS=two is not a number" "$SANDBOX/out" || fail "docker bad cpus: not said: $(cat "$SANDBOX/out")"
grep -q "^run -d" "$S/calls" 2>/dev/null && fail "docker bad cpus: a container started"
rm -f "$W/hello.txt"
remote_config "$SANDBOX/config" REMOTE_BACKEND=docker

# Settings: image, platform and hours. The brief comes from a file (@file).
printf 'line one\nline two\n' >"$SANDBOX/brief.md"
BRIEF="@$SANDBOX/brief.md" \
  run 0 "$HELLO_PATCH" REMOTE_BACKEND=docker REMOTE_IMAGE=local/runner:dev REMOTE_PLATFORM=linux/arm64 REMOTE_HOURS=1 \
  || fail "docker settings: exited non-zero: $(cat "$SANDBOX/out")"
grep -q "^run -d --rm .*--platform linux/arm64 local/runner:dev sleep 3600\$" "$S/calls" \
  || fail "docker settings: image, platform or hours not used: $(grep '^run' "$S/calls")"
[ "$(sed -n '2,$p' "$S/stdin")" = "line one
line two" ] || fail "docker @file: the brief is not the file's text: $(sed -n '2,$p' "$S/stdin")"
rm -f "$W/hello.txt"

# No changes: exit 3. --name labels the container with the task, under the cluster's key.
code=0; NAME="09-Review fixes" run 0 "" REMOTE_BACKEND=docker || code=$?
[ "$code" = 3 ] || fail "docker no changes: wanted exit 3, got $code"
gone "$(container)" || fail "docker no changes: container not removed"
grep -q "^run -d .*--label sandbox.users.io/remote-agent-task=09-review-fixes " "$S/calls" \
  || fail "docker name: container not labelled with the task: $(grep '^run' "$S/calls")"

# A failed run keeps its exit code, applies nothing, and removes the container. The brief comes from stdin (-).
code=0; BRIEF=- run 7 "$HELLO_PATCH" REMOTE_BACKEND=docker <<<"brief from stdin" || code=$?
[ "$(sed -n '2,$p' "$S/stdin")" = "brief from stdin" ] || fail "docker -: the brief is not stdin: $(sed -n '2,$p' "$S/stdin")"
[ "$code" = 7 ] || fail "docker failed run: wanted exit 7, got $code"
[ ! -e "$W/hello.txt" ] || fail "docker failed run: patch applied anyway"
gone "$(container)" || fail "docker failed run: container not removed"

# A patch that does not apply: exit 4.
code=0; run 0 "$STALE_PATCH" REMOTE_BACKEND=docker || code=$?
[ "$code" = 4 ] || fail "docker stale patch: wanted exit 4, got $code"
[ "$(cat "$W/tracked.txt")" = one ] || fail "docker stale patch: worktree changed"

# REMOTE_KEEP keeps the container and says so.
code=0; run 0 "" REMOTE_BACKEND=docker REMOTE_KEEP=1 || code=$?
c="$(container)"
[ -d "$S/containers/$c" ] || fail "docker keep: container removed"
grep -q "kept container $c" "$SANDBOX/out" || fail "docker keep: not reported: $(cat "$SANDBOX/out")"
rm -rf "${S:?}/containers/$c"

# TERM mid-run removes the container at once, without waiting for the sandbox run to end.
: >"$S/hang"
echo 0 >"$S/code"
: >"$S/patch"
remote_config "$SANDBOX/config" REMOTE_BACKEND=docker
rm -rf "$S/calls" "$S/started"
(cd "$W" && exec env -u CLAUDE_CODE_OAUTH_TOKEN HOME="$H" PATH="$SANDBOX/bin:$PATH" XDG_CONFIG_HOME="$SANDBOX/config" \
  "$REPO/scripts/remote-agent.sh" "add a hello file") >"$SANDBOX/out" 2>&1 &
pid=$!
for _ in $(seq 100); do [ -e "$S/started" ] && break; sleep 0.1; done
start=$SECONDS
kill -TERM "$pid"
code=0; wait "$pid" || code=$?
[ "$code" = 143 ] || fail "docker TERM: wanted exit 143, got $code: $(cat "$SANDBOX/out")"
[ $((SECONDS - start)) -lt 10 ] || fail "docker TERM: waited for the sandbox run to end"
gone "$(container)" || fail "docker TERM: container not removed"
rm -f "$S/hang"

# `remote-agent ready` probes the backend: the backend's name when it answers, else one line naming it and the error.
ready() {
  rm -rf "$S/calls"
  (cd "$W" && env -u CLAUDE_CODE_OAUTH_TOKEN HOME="$H" PATH="${READY_PATH:-$SANDBOX/bin:$PATH}" \
    XDG_CONFIG_HOME="$SANDBOX/config" "$REPO/scripts/remote-agent.sh" ready) >"$SANDBOX/out" 2>&1
}
remote_config "$SANDBOX/config" REMOTE_BACKEND=docker
ready && [ "$(cat "$SANDBOX/out")" = docker ] || fail "docker ready: $(cat "$SANDBOX/out")"
grep -q "^info " "$S/calls" || fail "docker ready: docker info not run: $(cat "$S/calls" 2>/dev/null)"
: >"$S/daemon-down"
code=0; ready || code=$?
[ "$code" = 1 ] || fail "docker ready, daemon down: wanted exit 1, got $code"
[ "$(cat "$SANDBOX/out")" = "docker backend not reachable: Docker daemon not reachable: Cannot connect to the Docker daemon. Is the docker daemon running?" ] \
  || fail "docker ready, daemon down: not one line naming the backend and the error: $(cat "$SANDBOX/out")"
rm -f "$S/daemon-down"

# --- ssh, container mode: the docker backend's commands, each one an ssh call to the host.
weird="it's a \$HOME file.txt"
printf 'odd\n' >"$W/$weird"
BRIEF="don't add \$HOME or \`x\`;  add a hello file" \
  run 0 "$HELLO_PATCH" REMOTE_BACKEND=ssh REMOTE_SSH_HOST=me@build-box || fail "ssh: exited non-zero: $(cat "$SANDBOX/out")"
c="$(container)"
[ "$(cat "$W/hello.txt" 2>/dev/null)" = hello ] || fail "ssh: patch not applied: $(cat "$SANDBOX/out")"
grep -q "did the thing" "$SANDBOX/out" || fail "ssh: summary not printed"
grep -q "^ssh -T -o BatchMode=yes me@build-box 'docker' 'run' '-d' '--rm' " "$S/calls" \
  || fail "ssh: docker run not sent to the host as quoted words: $(grep '^ssh' "$S/calls" | head -1)"
if ! grep -q "^ssh " "$S/calls" || grep "^ssh " "$S/calls" | grep -qv "^ssh -T -o BatchMode=yes me@build-box "; then
  fail "ssh: an ssh call without -T, BatchMode or the host: $(grep '^ssh' "$S/calls")"
fi
grep -q "^run -d --rm --name $c .*$IMAGE sleep 7200\$" "$S/calls" \
  || fail "ssh: run -d lacks --rm, the default image or sleep 7200: $(grep '^run' "$S/calls")"
grep -q "^run -d .*--label sandbox.users.io/remote-agent-run=$c " "$S/calls" \
  || fail "ssh: container not labelled with its run: $(grep '^run' "$S/calls")"
# Compared without CRs: a Windows checkout may turn the file's LFs into CRLFs, and the shell drops the CRs in transit.
# tr, not ${x//$'\r'/}: bash 3.2 takes seconds on a string this long.
want="$(tr -d '\r' <"$REPO/scripts/remote-agent-sandbox.sh")"
got="$(tr -d '\r' 2>/dev/null <"$S/script" || true)"
if [ "$got" != "$want" ]; then
  printf '%s' "$want" >"$SANDBOX/script.want"
  printf '%s' "$got" >"$SANDBOX/script.got"
  fail "ssh: the sandbox script did not survive the remote shell's quoting: $(wc -c <"$SANDBOX/script.want") bytes sent, $(wc -c <"$SANDBOX/script.got") back; $(cmp "$SANDBOX/script.want" "$SANDBOX/script.got" 2>&1 | head -2)"
fi
[ "$(sed -n 1p "$S/stdin")" = sk-claude-secret ] || fail "ssh: stdin line 1 is not the Claude token"
[ "$(sed -n '2,$p' "$S/stdin")" = "don't add \$HOME or \`x\`;  add a hello file" ] \
  || fail "ssh: brief changed on the way: $(sed -n '2,$p' "$S/stdin")"
grep -q "sk-claude-secret" "$S/calls" "$SANDBOX/out" && fail "ssh: the token leaked into ssh arguments or the output"
[ ! -e "$S/scp" ] || fail "ssh: scp called: $(cat "$S/scp")"
gone "$c" || fail "ssh: container $c not removed"
rm -f "$W/hello.txt"

# The snapshot arrives intact: keep the container to look inside it.
run 0 "" REMOTE_BACKEND=ssh REMOTE_SSH_HOST=me@build-box REMOTE_KEEP=1 || true
c="$(container)"
[ "$(cat "$S/containers/$c/work/$weird" 2>/dev/null)" = odd ] \
  || fail "ssh: file name with a quote, spaces and \$ not sent: $(ls "$S/containers/$c/work" 2>&1)"
grep -q "kept container $c" "$SANDBOX/out" || fail "ssh keep: not reported: $(cat "$SANDBOX/out")"
rm -rf "${S:?}/containers/$c" "${W:?}/$weird"

# A failed run keeps its exit code and removes the container.
code=0; run 7 "$HELLO_PATCH" REMOTE_BACKEND=ssh REMOTE_SSH_HOST=me@build-box || code=$?
[ "$code" = 7 ] || fail "ssh failed run: wanted exit 7, got $code"
gone "$(container)" || fail "ssh failed run: container not removed"

# An unknown mode (hand-edited into settings.json) or no host: the run stops naming what is wrong, and nothing
# reaches the host.
remote_config "$SANDBOX/config"
echo '{"remote": {"backend": "ssh", "ssh_host": "me@build-box", "ssh_mode": "pod"}}' >"$SANDBOX/config/dotagents/settings.json"
code=0; run 0 "$HELLO_PATCH" || code=$?
[ "$code" != 0 ] || fail "ssh unknown mode: exited 0"
grep -q "ssh_mode is \"pod\".*container, bare" "$SANDBOX/out" || fail "ssh unknown mode: message: $(cat "$SANDBOX/out")"
[ "$(grep -c 'ssh_mode' "$SANDBOX/out")" = 1 ] || fail "ssh unknown mode: said more than once: $(cat "$SANDBOX/out")"
[ ! -e "$S/calls" ] || fail "ssh unknown mode: something reached the host: $(cat "$S/calls")"
code=0; run 0 "$HELLO_PATCH" REMOTE_BACKEND=ssh || code=$?
[ "$code" != 0 ] || fail "ssh no host: exited 0"
grep -q "set REMOTE_SSH_HOST" "$SANDBOX/out" || fail "ssh no host: message: $(cat "$SANDBOX/out")"
[ ! -e "$S/calls" ] || fail "ssh no host: something reached the host: $(cat "$S/calls")"

# No runner image (an empty `image` hand-edited into settings.json): the docker backend, and ssh's container mode
# through it, stop naming it, and no container starts.
for b in docker ssh; do
  remote_config "$SANDBOX/config"
  echo "{\"remote\": {\"backend\": \"$b\", \"ssh_host\": \"me@build-box\", \"image\": \"\"}}" >"$SANDBOX/config/dotagents/settings.json"
  code=0; run 0 "$HELLO_PATCH" || code=$?
  [ "$code" != 0 ] || fail "$b no image: exited 0"
  grep -q "REMOTE_IMAGE not set" "$SANDBOX/out" || fail "$b no image: message: $(cat "$SANDBOX/out")"
  grep -q "^run -d" "$S/calls" 2>/dev/null && fail "$b no image: a container started: $(cat "$S/calls")"
done
# ready says the same on the docker backend, without calling docker.
echo '{"remote": {"backend": "docker", "image": ""}}' >"$SANDBOX/config/dotagents/settings.json"
code=0; ready || code=$?
[ "$code" != 0 ] || fail "docker ready, no image: exited 0"
grep -q "REMOTE_IMAGE not set" "$SANDBOX/out" || fail "docker ready, no image: message: $(cat "$SANDBOX/out")"
[ ! -e "$S/calls" ] || fail "docker ready, no image: docker was called: $(cat "$S/calls")"

# ready over ssh: a login with a 5-second connect timeout, then docker info on the host; the host's or the daemon's
# error on one line when either fails.
remote_config "$SANDBOX/config" REMOTE_BACKEND=ssh REMOTE_SSH_HOST=me@build-box
ready && [ "$(cat "$SANDBOX/out")" = ssh ] || fail "ssh ready: $(cat "$SANDBOX/out")"
grep -q "^ssh -T -o BatchMode=yes -o ConnectTimeout=5 me@build-box true$" "$S/calls" \
  || fail "ssh ready: no login probe with a connect timeout: $(cat "$S/calls" 2>/dev/null)"
grep -q "^ssh -T -o BatchMode=yes -o ConnectTimeout=5 me@build-box 'docker' 'info'" "$S/calls" \
  || fail "ssh ready: docker info not run on the host: $(cat "$S/calls" 2>/dev/null)"
: >"$S/daemon-down"
code=0; ready || code=$?
[ "$code" = 1 ] || fail "ssh ready, daemon down: wanted exit 1, got $code"
grep -qx "ssh backend not reachable: Docker daemon not reachable on me@build-box: Cannot connect to the Docker daemon.*" "$SANDBOX/out" \
  || fail "ssh ready, daemon down: not one line naming the backend, the host and the error: $(cat "$SANDBOX/out")"
rm -f "$S/daemon-down"
mkdir -p "$SANDBOX/downbin"
printf '#!/bin/sh\necho "ssh: connect to host build-box port 22: No route to host" >&2\nexit 255\n' >"$SANDBOX/downbin/ssh"
chmod +x "$SANDBOX/downbin/ssh"
code=0; READY_PATH="$SANDBOX/downbin:$SANDBOX/bin:$PATH" ready || code=$?
[ "$code" = 1 ] || fail "ssh ready, unreachable: wanted exit 1, got $code"
[ "$(cat "$SANDBOX/out")" = "ssh backend not reachable: cannot reach me@build-box: ssh: connect to host build-box port 22: No route to host" ] \
  || fail "ssh ready, unreachable: not one line naming the backend and the error: $(cat "$SANDBOX/out")"

# --- ssh login. A key file: every call uses it alone, still without a prompt.
rm -f "$W/hello.txt" "$S/askpass"
run 0 "$HELLO_PATCH" REMOTE_BACKEND=ssh REMOTE_SSH_HOST=me@build-box "REMOTE_SSH_KEY='~/keys/box key'" \
  || fail "ssh key: exited non-zero: $(cat "$SANDBOX/out")"
[ "$(cat "$W/hello.txt" 2>/dev/null)" = hello ] || fail "ssh key: patch not applied: $(cat "$SANDBOX/out")"
if ! grep -q "^ssh " "$S/calls" \
  || grep "^ssh " "$S/calls" | grep -qv "^ssh -T -o BatchMode=yes -i $H/keys/box key -o IdentitiesOnly=yes me@build-box "; then
  fail "ssh key: a call without BatchMode or the key file alone (~ as this machine's home): $(grep '^ssh' "$S/calls" | head -2)"
fi
[ ! -e "$S/askpass" ] || fail "ssh key: askpass used with no password set"
# A password in remote.env: keys first, then the password, answered by askpass and never in ssh's arguments.
rm -f "$W/hello.txt"
remote_config "$SANDBOX/config" REMOTE_BACKEND=ssh REMOTE_SSH_HOST=me@build-box
echo "SSH_PASSWORD='pw-s3cret \$x'" >>"$SANDBOX/config/dotagents/remote.env"
run 0 "$HELLO_PATCH" || fail "ssh password: exited non-zero: $(cat "$SANDBOX/out")"
[ "$(cat "$W/hello.txt" 2>/dev/null)" = hello ] || fail "ssh password: patch not applied: $(cat "$SANDBOX/out")"
if ! grep -q "^ssh " "$S/calls" \
  || grep "^ssh " "$S/calls" | grep -qv "^ssh -T -o BatchMode=no -o NumberOfPasswordPrompts=1 me@build-box "; then
  fail "ssh password: a call not allowing one password try: $(grep '^ssh' "$S/calls" | head -2)"
fi
# shellcheck disable=SC2016 # a literal $ in the password
[ "$(sort -u "$S/askpass" 2>/dev/null)" = 'pw-s3cret $x' ] || fail "ssh password: askpass answers: $(cat "$S/askpass" 2>/dev/null)"
grep -q "pw-s3cret" "$S/calls" "$SANDBOX/out" && fail "ssh password: the password leaked into ssh arguments or the output"
# An unknown host key's question gets no answer: the login fails, as with BatchMode.
rm -f "$W/hello.txt" "$S/askpass"
code=0; STUB_SSH_PROMPT="Are you sure you want to continue connecting (yes/no/[fingerprint])? " run 0 "$HELLO_PATCH" || code=$?
[ "$code" != 0 ] || fail "ssh host key: exited 0"
[ ! -s "$S/askpass" ] || fail "ssh host key: askpass answered: $(cat "$S/askpass")"
# askpass itself: a passphrase prompt takes SSH_KEY_PASSPHRASE only; --any says whether either Secret is set.
askpass() { XDG_CONFIG_HOME="$SANDBOX/config" env -u SSH_PASSWORD -u SSH_KEY_PASSPHRASE "$REPO/scripts/remote-ssh-askpass.sh" "$@"; }
askpass "Enter passphrase for key '/k': " >/dev/null && fail "askpass: answered a passphrase prompt with the password"
echo "SSH_KEY_PASSPHRASE=phrase" >>"$SANDBOX/config/dotagents/remote.env"
[ "$(askpass "Enter passphrase for key '/k': ")" = phrase ] || fail "askpass: passphrase not answered"
# shellcheck disable=SC2016 # a literal $ in the password
[ "$(askpass "(me@box) Password: ")" = 'pw-s3cret $x' ] || fail "askpass: keyboard-interactive password not answered"
remote_config "$SANDBOX/config" REMOTE_BACKEND=ssh REMOTE_SSH_HOST=me@build-box
askpass --any && fail "askpass --any: true with neither Secret set"
rm -f "$W/hello.txt" "$S/askpass"

# ssh_sudo: a host whose docker only root may use; every docker command goes through sudo -n.
: >"$S/root-only"
run 0 "$HELLO_PATCH" REMOTE_BACKEND=ssh REMOTE_SSH_HOST=me@build-box REMOTE_SSH_SUDO=true \
  || fail "ssh sudo: exited non-zero: $(cat "$SANDBOX/out")"
[ "$(cat "$W/hello.txt" 2>/dev/null)" = hello ] || fail "ssh sudo: patch not applied: $(cat "$SANDBOX/out")"
if grep "^ssh " "$S/calls" | grep -qv "^ssh -T -o BatchMode=yes me@build-box 'sudo' '-n' 'docker' "; then
  fail "ssh sudo: a docker call not through sudo -n: $(grep '^ssh' "$S/calls" | grep -v "'sudo'" | head -2)"
fi
gone "$(container)" || fail "ssh sudo: container not removed"
rm -f "$S/root-only" "$W/hello.txt"

# Two runs at once on one host: each gets its own container, both finish, both containers go.
W2="$SANDBOX/work2"
make_worktree "$W2"
: >"$S/pair"
echo 0 >"$S/code"
printf '%s' "$HELLO_PATCH" >"$S/patch"
remote_config "$SANDBOX/config" REMOTE_BACKEND=ssh REMOTE_SSH_HOST=me@build-box
rm -rf "$S/calls" "$S/seen"
pids=()
for w in "$W" "$W2"; do
  (cd "$w" && exec env -u CLAUDE_CODE_OAUTH_TOKEN HOME="$H" PATH="$SANDBOX/bin:$PATH" XDG_CONFIG_HOME="$SANDBOX/config" \
    "$REPO/scripts/remote-agent.sh" "add a hello file") >"$w.out" 2>&1 &
  pids+=($!)
done
for p in "${pids[@]}"; do wait "$p" || fail "ssh pair: a run exited non-zero: $(cat "$W.out" "$W2.out")"; done
for w in "$W" "$W2"; do [ "$(cat "$w/hello.txt" 2>/dev/null)" = hello ] || fail "ssh pair: patch not applied in $w"; done
names="$(sed -n 's/^run .*--name \([^ ]*\) .*/\1/p' "$S/calls" | sort -u)"
[ "$(printf '%s\n' "$names" | grep -c .)" = 2 ] || fail "ssh pair: wanted 2 containers, got: $names"
[ "$(sort -u "$S/seen" 2>/dev/null)" = 2 ] || fail "ssh pair: the runs did not overlap: $(cat "$S/seen" 2>/dev/null)"
for n in $names; do gone "$n" || fail "ssh pair: container $n not removed"; done
rm -f "$S/pair" "$W/hello.txt"

# --- ssh, bare mode: straight on the host, in a run folder used as HOME. The "host" is the folder $HH, its PATH the
# client's minus any real claude, plus $HB: a fake claude (recording its environment, applying $S/patch) and node.
HH="$SANDBOX/host home"
HB="$SANDBOX/hostbin"
mkdir -p "$HH" "$HB"
host_path=""
IFS=: read -ra dirs <<<"$PATH"
for d in "${dirs[@]}"; do [ -x "$d/claude" ] || host_path="$host_path:$d"; done
printf '#!/bin/sh\nexit 0\n' >"$HB/node"
cat >"$HB/claude" <<CLAUDE
#!/usr/bin/env bash
[ "\$1" = -p ] || exit 0
if [ -e "$S/hang" ]; then echo \$\$ >"$S/claude.pid"; : >"$S/started"; exec sleep 30; fi
printf '%s\n' "\$HOME" "\$XDG_CONFIG_HOME" "\$XDG_DATA_HOME" "\$XDG_CACHE_HOME" "\$CLAUDE_CONFIG_DIR" "\$PWD" >"$S/claude-env"
[ "\$CLAUDE_CODE_OAUTH_TOKEN" = sk-claude-secret ] || echo "no token" >>"$S/claude-env"
# A session as Claude starts one, in its config folder, holding the token and a cwd line.
id=5e551011-ba7e-4000-8000-000000000001
echo "{\"type\":\"system\",\"subtype\":\"init\",\"session_id\":\"\$id\"}"
d="\${CLAUDE_CONFIG_DIR:-\$HOME/.claude}/projects/\$(printf '%s' "\$PWD" | sed 's/[^A-Za-z0-9]/-/g')"
mkdir -p "\$d"
printf '{"type":"user","cwd":"%s","sessionId":"%s","message":"%s"}\n' "\$PWD" "\$id" "\$CLAUDE_CODE_OAUTH_TOKEN" >"\$d/\$id.jsonl"
echo '{"type":"assistant","message":{"content":[{"type":"text","text":"writing the hello file"}]}}'
[ ! -s "$S/patch" ] || git apply "$S/patch"
echo '{"type":"assistant","message":{"content":[{"type":"text","text":"did the thing"}]}}'
echo '{"type":"result","result":"did the thing","is_error":false}'
CLAUDE
chmod +x "$HB/node" "$HB/claude"
export STUB_SSH_HOME="$HH" STUB_SSH_PATH="$HB$host_path"
runs="$HH/.remote-agent/runs"
bare=(REMOTE_BACKEND=ssh REMOTE_SSH_HOST=me@trusted-box REMOTE_SSH_MODE=bare)
# The run's folder on the host: the HOME the fake claude saw.
run_home() { sed -n 1p "$S/claude-env" 2>/dev/null || true; }

rm -f "$S/claude-env"
run 0 "$HELLO_PATCH" "${bare[@]}" || fail "bare: exited non-zero: $(cat "$SANDBOX/out")"
f="$(run_home)"
[ "$(cat "$W/hello.txt" 2>/dev/null)" = hello ] || fail "bare: patch not applied: $(cat "$SANDBOX/out")"
grep -q "did the thing" "$SANDBOX/out" || fail "bare: summary not printed"
case "$f" in
  "$runs"/agent-*) ;;
  *) fail "bare: HOME in the sandbox is not a run folder under $runs: $f" ;;
esac
[ -f "$W/.git/remote-agent/${f##*/}/changes.patch" ] || fail "bare: patch not kept under .git/remote-agent/${f##*/}"
grep -q '"text":"writing the hello file"' "$W/.git/remote-agent/${f##*/}/transcript.jsonl" 2>/dev/null \
  || fail "bare: Claude's events not in transcript.jsonl"
grep -q "remote-agent: transcript: .*/remote-agent/${f##*/}/transcript.jsonl$" "$SANDBOX/out" \
  || fail "bare: transcript path not printed: $(cat "$SANDBOX/out")"
[ "$(sed -n '2,6p' "$S/claude-env")" = "$f/.config
$f/.local/share
$f/.cache
$f/.claude
$f/work" ] || fail "bare: XDG folders, CLAUDE_CONFIG_DIR or the working folder not in the run folder: $(cat "$S/claude-env")"
grep -q "no token" "$S/claude-env" && fail "bare: Claude did not get the token"
# The session comes back from the run folder's .claude, kept with the run without the token, and placed for /resume
# in the worktree only by `remote-agent resume`.
k="$W/.git/remote-agent/${f##*/}"
[ -f "$k/session.jsonl" ] || fail "bare: session not kept with the run: $(cat "$SANDBOX/out")"
[ ! -e "$CC/projects" ] || fail "bare: session placed without being asked: $(find "$CC" -type f)"
grep -rl "sk-claude-secret" "$k" && fail "bare: the token is in a kept file"
(cd "$W" && env HOME="$H" CLAUDE_CONFIG_DIR="$CC" "$REPO/scripts/remote-agent.sh" resume "${f##*/}") >/dev/null 2>&1 \
  || fail "bare: resume exited non-zero"
real_w="$(cd "$W" && pwd -P)"
if command -v cygpath >/dev/null 2>&1; then real_w="$(cygpath -w "$real_w")"; fi
s="$CC/projects/$(printf '%s' "$real_w" | sed 's/[^A-Za-z0-9]/-/g')/5e551011-ba7e-4000-8000-000000000001.jsonl"
[ "$(sed 's/\r$//' "$s" 2>/dev/null | jq -r .cwd | tr -d '\r')" = "$real_w" ] || fail "bare: session not placed at $s with the worktree's cwd: $(find "$CC" -type f 2>/dev/null)"
if [ -z "$f" ] || [ -e "$f" ]; then fail "bare: run folder $f not removed after release"; fi
grep -q -- "--warning\|--no-same-owner" "$S/calls" && fail "bare: tar flags beyond plain GNU/BSD ones: $(grep tar "$S/calls")"
if grep "^ssh " "$S/calls" | grep -qv "^ssh -T -o BatchMode=yes me@trusted-box "; then
  fail "bare: an ssh call without -T, BatchMode or the host: $(grep '^ssh' "$S/calls")"
fi
grep -q "sk-claude-secret" "$S/calls" "$SANDBOX/out" && fail "bare: the token leaked into ssh arguments or the output"
grep -q "'docker'" "$S/calls" && fail "bare: docker used: $(grep "'docker'" "$S/calls")"
[ ! -e "$S/scp" ] || fail "bare: scp called: $(cat "$S/scp")"
rm -f "$W/hello.txt"

# REMOTE_KEEP keeps the folder: mode 700, the snapshot in it, and the progress in progress.log there.
rm -f "$S/claude-env"
run 0 "" "${bare[@]}" REMOTE_KEEP=1 || true
f="$(run_home)"
if [ -z "$f" ] || [ ! -d "$f" ]; then fail "bare keep: run folder removed"; fi
grep -q "kept run folder" "$SANDBOX/out" || fail "bare keep: not reported: $(cat "$SANDBOX/out")"
grep -q "kept run folder.*once the host's clock is past its .expires and its .heartbeat is 3 minutes old" \
  "$SANDBOX/out" || fail "bare keep: the message does not say when the folder goes: $(cat "$SANDBOX/out")"
# Git Bash on Windows has no Unix modes.
[ -n "${MSYSTEM:-}" ] || [ -n "$(find "$f" -maxdepth 0 -perm 700)" ] || fail "bare keep: run folder mode is not 700"
[ "$(cat "$f/work/tracked.txt" 2>/dev/null)" = one ] || fail "bare keep: snapshot not in the run folder"
grep -q "writing the hello file" "$f/progress.log" 2>/dev/null || fail "bare keep: Claude's progress not in progress.log"
grep -q "sent setup and snapshot" "$f/progress.log" 2>/dev/null || fail "bare keep: client progress not in progress.log"
grep -q "sk-claude-secret" "$f/progress.log" 2>/dev/null && fail "bare keep: the token reached progress.log"
# Even where the host's main-process stdout is writable (as root, or a host that is itself a container).
[ ! -s "$S/host-main-stdout" ] || fail "bare keep: progress written to the host's /proc/1/fd/1: $(head -3 "$S/host-main-stdout")"

# TERM mid-run stops the run on the host too: no claude (no ssh client, here) outlives remote-agent.
: >"$S/hang"
rm -f "$S/started" "$S/claude.pid"
remote_config "$SANDBOX/config" "${bare[@]}"
(cd "$W" && exec env -u CLAUDE_CODE_OAUTH_TOKEN HOME="$H" PATH="$SANDBOX/bin:$PATH" XDG_CONFIG_HOME="$SANDBOX/config" \
  "$REPO/scripts/remote-agent.sh" "add a hello file") >"$SANDBOX/out" 2>&1 &
pid=$!
for _ in $(seq 100); do [ -e "$S/started" ] && break; sleep 0.1; done
start=$SECONDS
kill -TERM "$pid"
code=0; wait "$pid" || code=$?
[ "$code" = 143 ] || fail "bare TERM: wanted exit 143, got $code: $(cat "$SANDBOX/out")"
[ $((SECONDS - start)) -lt 10 ] || fail "bare TERM: waited for the sandbox run to end"
if alive "$(cat "$S/claude.pid" 2>/dev/null)"; then
  fail "bare TERM: claude on the host outlived remote-agent"
  kill "$(cat "$S/claude.pid")"
fi
rm -f "$S/hang"

# release kills what still runs for the run before removing its folder, or keeping it under REMOTE_KEEP: here a
# claude (and its child) ignoring TERM and its closed stdout, started by ssh.sh run outside remote-agent's process
# group, so only release can stop it. Its child goes too where the host can kill it: setsid (group) or pkill -P.
cat >"$HB/stubborn" <<STUBBORN
#!/bin/sh
trap '' TERM HUP PIPE
sleep 60 &
echo \$! >"$S/stubborn\$1-child.pid"
echo \$\$ >"$S/stubborn\$1.pid"
while :; do echo tick 2>/dev/null || : >"$S/stdout-closed\$1"; sleep 0.2; done
STUBBORN
chmod +x "$HB/stubborn"
bare_op() {
  env PATH="$SANDBOX/bin:$PATH" REMOTE_SSH_HOST=me@trusted-box REMOTE_SSH_MODE=bare \
    "$REPO/scripts/remote-backends/ssh.sh" "$@"
}
# Run i (agent-stubborn<i>) is released without REMOTE_KEEP for i 0, with it for i 1; both releases go at once, as
# each waits out the TERM its run ignores.
keeps=("" 1)
hs=()
pids=()
for i in 0 1; do
  t="bare release${keeps[i]:+ keep}"
  rm -f "$S/stubborn$i.pid" "$S/stubborn$i-child.pid" "$S/stdout-closed$i"
  hs[i]="$(bare_op acquire "agent-stubborn$i" 2>/dev/null)" || fail "$t: acquire failed"
  (bare_op run "agent-stubborn$i" "${hs[i]}" "$HB/stubborn" "$i" </dev/null 2>/dev/null | head -1 >/dev/null) &
  pids[i]=$!
done
rels=()
for i in 0 1; do
  t="bare release${keeps[i]:+ keep}"
  for _ in $(seq 100); do [ -e "$S/stdout-closed$i" ] && break; sleep 0.1; done
  [ -e "$S/stdout-closed$i" ] || fail "$t: the fake claude did not start or its stdout did not close"
  (REMOTE_KEEP="${keeps[i]}" bare_op release "agent-stubborn$i" 2>/dev/null) &
  rels[i]=$!
done
for i in 0 1; do
  keep="${keeps[i]}" h="${hs[i]}" pid="${pids[i]}"
  t="bare release${keep:+ keep}"
  wait "${rels[i]}" || fail "$t: release failed"
  # Once the run ends on the host, the client's call returns; a run left going fails the checks below.
  for _ in $(seq 50); do kill -0 "$pid" 2>/dev/null || break; sleep 0.1; done
  kill -KILL "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
  for p in stubborn stubborn-child; do
    [ "$p" = stubborn ] || command -v setsid >/dev/null || command -v pkill >/dev/null || continue
    pf="$S/${p/stubborn/stubborn$i}.pid"
    if alive "$(cat "$pf" 2>/dev/null)"; then
      fail "$t: $p on the host outlived release"
      kill -KILL "$(cat "$pf")"
    fi
  done
  if [ -n "$keep" ]; then
    [ -d "$h" ] || fail "$t: run folder removed"
  elif [ -z "$h" ] || [ -e "$h" ]; then
    fail "$t: run folder $h not removed"
  fi
  rm -rf "$h"
done
# A run folder with no .pid (nothing was run in it) is removed all the same.
h="$(bare_op acquire agent-nopid 2>/dev/null)" || fail "bare release no pid: acquire failed"
bare_op release agent-nopid 2>/dev/null || fail "bare release no pid: release failed"
if [ -z "$h" ] || [ -e "$h" ]; then fail "bare release no pid: run folder $h not removed"; fi

# acquire records the run's expiry, host clock + REMOTE_HOURS, in its folder's .expires; put cannot overwrite it.
before="$(date +%s)"
h="$(REMOTE_HOURS=1 bare_op acquire agent-expiry 2>/dev/null)" || fail "bare expiry: acquire failed"
e="$(tr -d '\r' <"$h/.expires" 2>/dev/null || true)"
case "$e" in
  ""|*[!0-9]*) fail "bare expiry: no .expires in the run folder: $e" ;;
  *)
    if [ "$e" -lt $((before + 3600)) ] || [ "$e" -gt $(($(date +%s) + 3600)) ]; then
      fail "bare expiry: .expires is not now + REMOTE_HOURS: $e"
    fi
    ;;
esac
mkdir -p "$SANDBOX/stage" && printf '0\n' >"$SANDBOX/stage/.expires"
tar -cf - -C "$SANDBOX/stage" . | gzip | bare_op put agent-expiry "$h" . 2>/dev/null || fail "bare expiry: put failed"
[ "$(tr -d '\r' <"$h/.expires" 2>/dev/null)" = "$e" ] || fail "bare expiry: put overwrote .expires: $(cat "$h/.expires")"

# run keeps a heartbeat, .heartbeat touched while the run lives, from a loop holding none of the connection's
# streams: the call returns when the run ends, though the loop sleeps on.
start=$SECONDS
out="$(bare_op run agent-expiry "$h" sh -c 'sleep 1; echo ran' </dev/null 2>/dev/null)" || fail "bare heartbeat: run failed"
[ "$out" = ran ] || fail "bare heartbeat: run output: $out"
[ $((SECONDS - start)) -lt 15 ] || fail "bare heartbeat: run waited for the heartbeat loop"
[ -e "$h/.heartbeat" ] || fail "bare heartbeat: no .heartbeat in the run folder"
bare_op release agent-expiry 2>/dev/null || true

# The next acquire removes a run folder past its own .expires with no heartbeat in 3 minutes, whatever its mtime and
# the acquiring client's REMOTE_HOURS; a folder with no .expires (an older client's) when older than REMOTE_HOURS.
now="$(date +%s)"
for d in future live dead silent; do mkdir -p "$runs/agent-$d"; done
echo $((now + 7200)) >"$runs/agent-future/.expires"
for d in live dead silent; do echo $((now - 10)) >"$runs/agent-$d/.expires"; done
touch "$runs/agent-live/.heartbeat"
touch -t 200001010000 "$runs/agent-dead/.heartbeat"
for d in future live dead silent; do touch -t 200001010000 "$runs/agent-$d"; done
h="$(REMOTE_HOURS=1 bare_op acquire agent-sweep 2>/dev/null)" || fail "bare sweep: acquire failed"
bare_op release agent-sweep 2>/dev/null || true
[ -d "$runs/agent-future" ] || fail "bare sweep: a folder before its .expires was removed"
[ -d "$runs/agent-live" ] || fail "bare sweep: an expired folder with a live heartbeat was removed"
[ ! -e "$runs/agent-dead" ] || fail "bare sweep: an expired folder with a stale heartbeat was kept"
[ ! -e "$runs/agent-silent" ] || fail "bare sweep: an expired folder with no heartbeat was kept"
rm -rf "$runs"/agent-future "$runs"/agent-live

# A hard limit: once the host's clock is more than 10 minutes past a folder's .expires, the next acquire kills what
# still runs there (through .pid, as release does) and removes it, though its heartbeat is fresh. The run here goes
# on TERM: the KILL that follows a TERM ignored is the same stop as release's, checked with the stubborn run above.
printf '#!/bin/sh
echo $$ >"%s"
exec sleep 60
' "$S/polite.pid" >"$HB/polite"
chmod +x "$HB/polite"
rm -f "$S/polite.pid"
h="$(bare_op acquire agent-overdue 2>/dev/null)" || fail "bare hard limit: acquire failed"
(bare_op run agent-overdue "$h" "$HB/polite" </dev/null >/dev/null 2>&1) &
pid=$!
for _ in $(seq 100); do [ -e "$S/polite.pid" ] && break; sleep 0.1; done
echo $(($(date +%s) - 86400)) >"$h/.expires"
touch "$h/.heartbeat"
h2="$(bare_op acquire agent-sweep 2>/dev/null)" || fail "bare hard limit: the next acquire failed"
bare_op release agent-sweep 2>/dev/null || true
[ ! -e "$h" ] || fail "bare hard limit: a folder long past its .expires, with a fresh heartbeat, was kept"
if alive "$(cat "$S/polite.pid" 2>/dev/null)"; then
  fail "bare hard limit: the run long past its .expires outlived the next acquire"
  kill -KILL "$(cat "$S/polite.pid")"
fi
kill -KILL "$pid" 2>/dev/null || true
wait "$pid" 2>/dev/null || true
[ -z "$h2" ] || rm -rf "$h2"

# Signals reach only the run's own processes: a .pid naming a live process working outside the run folder (a pid
# reused since) is left alone by release and by acquire's cleanup.
(cd "$SANDBOX" && exec sleep 60) &
other=$!
h="$(bare_op acquire agent-reused 2>/dev/null)" || fail "bare reused pid: acquire failed"
printf '%s group\n%s\n' "$other" "$other" >"$h/.pid"
bare_op release agent-reused 2>/dev/null || true
alive "$other" 5 || fail "bare reused pid: release signalled a process outside the run folder"
mkdir -p "$runs/agent-reused"
echo $(($(date +%s) - 86400)) >"$runs/agent-reused/.expires"
printf '%s group\n%s\n' "$other" "$other" >"$runs/agent-reused/.pid"
h="$(bare_op acquire agent-sweep 2>/dev/null)" || fail "bare reused pid: the next acquire failed"
bare_op release agent-sweep 2>/dev/null || true
[ ! -e "$runs/agent-reused" ] || fail "bare reused pid: the expired folder was kept"
alive "$other" 5 || fail "bare reused pid: acquire's cleanup signalled a process outside the run folder"
kill "$other" 2>/dev/null || true
wait "$other" 2>/dev/null || true

# The next acquire removes run folders older than REMOTE_HOURS and keeps fresh ones (folders with no .expires).
[ -z "$f" ] || { rm -f "$f/.expires" && touch -t 200001010000 "$f"; }
mkdir -p "$runs/agent-fresh"
run 0 "" "${bare[@]}" || true
if [ -z "$f" ] || [ -e "$f" ]; then fail "bare stale: folder older than REMOTE_HOURS not removed"; fi
[ -d "$runs/agent-fresh" ] || fail "bare stale: a fresh folder was removed"
rm -rf "$runs/agent-fresh"

# REMOTE_SSH_DIR moves the run folders.
rm -f "$S/claude-env"
run 0 "" "${bare[@]}" "REMOTE_SSH_DIR='$SANDBOX/other runs'" REMOTE_KEEP=1 || true
case "$(run_home)" in
  "$SANDBOX/other runs"/agent-*) ;;
  *) fail "bare REMOTE_SSH_DIR: run folder not under it: $(run_home)" ;;
esac

# Without claude on the host the run stops before anything is sent, naming it, and creates no run folder.
rm -rf "$runs"
STUB_SSH_PATH="$host_path" run 0 "$HELLO_PATCH" "${bare[@]}" && fail "bare no claude: exited 0"
grep -q "missing on the host:.*claude" "$SANDBOX/out" || fail "bare no claude: claude not named: $(cat "$SANDBOX/out")"
grep -q "tar -x" "$S/calls" && fail "bare no claude: files sent anyway"
[ -z "$(ls "$runs" 2>/dev/null)" ] || fail "bare no claude: run folder created: $(ls "$runs")"
[ ! -e "$W/hello.txt" ] || fail "bare no claude: patch applied"
unset STUB_SSH_HOME STUB_SSH_PATH

[ "$fails" -eq 0 ] || { echo "remote-backends.test.sh: $fails failure(s)"; exit 1; }
echo "remote-backends.test.sh: ok"
