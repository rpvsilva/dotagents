#!/usr/bin/env bash
# Runs scripts/remote-agent.sh end to end on each non-cluster backend against stubs (tests/lib/remote-stubs.sh):
# the patch comes back, the exit codes match the cluster's, the token stays off the command line, and the
# sandbox goes away after success, failure and TERM. The ssh backend runs against a stub ssh that hands the remote
# command to a local `sh -c`, with the docker stub behind it; in bare mode, the stub ssh plays a host whose home is a
# throwaway folder, and the real sandbox script runs there with a fake claude.
# Opt-in real run: REMOTE_DOCKER_IMAGE_TEST=<image> runs one task in a real container of <image> (needs Docker).
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
W="$SANDBOX/work"
mkdir -p "$H" "$SANDBOX/config/dotagents"
stub_docker "$SANDBOX/bin" "$S"
stub_ssh "$SANDBOX/bin" "$S"
make_worktree "$W"
# This machine's Claude config, where `remote-agent resume` places a run's session.
CC="$SANDBOX/claude-config"

# Runs remote-agent.sh in $W with the remote.env lines given after the fake run's exit code $1 and patch $2; with
# NAME set, as --name NAME.
run() {
  echo "$1" >"$S/code"
  printf '%s' "$2" >"$S/patch"
  shift 2
  { echo "CLAUDE_CODE_OAUTH_TOKEN=sk-claude-secret"; printf '%s\n' "$@"; } >"$SANDBOX/config/dotagents/remote.env"
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
grep -q "^run -d --rm --name $c .*ghcr.io/rpvsilva/agent-runner:main sleep 7200\$" "$S/calls" \
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

# Settings: image, platform and hours.
run 0 "$HELLO_PATCH" REMOTE_BACKEND=docker REMOTE_IMAGE=local/runner:dev REMOTE_PLATFORM=linux/arm64 REMOTE_HOURS=1 \
  || fail "docker settings: exited non-zero: $(cat "$SANDBOX/out")"
grep -q "^run -d --rm .*--platform linux/arm64 local/runner:dev sleep 3600\$" "$S/calls" \
  || fail "docker settings: image, platform or hours not used: $(grep '^run' "$S/calls")"
rm -f "$W/hello.txt"

# --name labels the container with the task, under the cluster's key.
NAME="09-Review fixes" run 0 "" REMOTE_BACKEND=docker || true
grep -q "^run -d .*--label sandbox.users.io/remote-agent-task=09-review-fixes " "$S/calls" \
  || fail "docker name: container not labelled with the task: $(grep '^run' "$S/calls")"

# No changes: exit 3.
code=0; run 0 "" REMOTE_BACKEND=docker || code=$?
[ "$code" = 3 ] || fail "docker no changes: wanted exit 3, got $code"
gone "$(container)" || fail "docker no changes: container not removed"

# A failed run keeps its exit code, applies nothing, and removes the container.
code=0; run 5 "$HELLO_PATCH" REMOTE_BACKEND=docker || code=$?
[ "$code" = 5 ] || fail "docker failed run: wanted exit 5, got $code"
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
printf 'CLAUDE_CODE_OAUTH_TOKEN=sk-claude-secret\nREMOTE_BACKEND=docker\n' >"$SANDBOX/config/dotagents/remote.env"
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
grep -q "^run -d --rm --name $c .*ghcr.io/rpvsilva/agent-runner:main sleep 7200\$" "$S/calls" \
  || fail "ssh: run -d lacks --rm, the default image or sleep 7200: $(grep '^run' "$S/calls")"
grep -q "^run -d .*--label sandbox.users.io/remote-agent-run=$c " "$S/calls" \
  || fail "ssh: container not labelled with its run: $(grep '^run' "$S/calls")"
# Compared without CRs: a Windows checkout may turn the file's LFs into CRLFs, and the shell drops the CRs in transit.
want="$(cat "$REPO/scripts/remote-agent-sandbox.sh")"
want="${want//$'\r'/}"
got="$(cat "$S/script" 2>/dev/null)"
got="${got//$'\r'/}"
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
code=0; run 5 "$HELLO_PATCH" REMOTE_BACKEND=ssh REMOTE_SSH_HOST=me@build-box || code=$?
[ "$code" = 5 ] || fail "ssh failed run: wanted exit 5, got $code"
gone "$(container)" || fail "ssh failed run: container not removed"

# Unknown mode or no host: the run stops naming what is wrong, and nothing reaches the host.
code=0; run 0 "$HELLO_PATCH" REMOTE_BACKEND=ssh REMOTE_SSH_HOST=me@build-box REMOTE_SSH_MODE=pod || code=$?
[ "$code" != 0 ] || fail "ssh unknown mode: exited 0"
grep -q "REMOTE_SSH_MODE=pod.*container.*bare" "$SANDBOX/out" || fail "ssh unknown mode: message: $(cat "$SANDBOX/out")"
[ "$(grep -c 'REMOTE_SSH_MODE' "$SANDBOX/out")" = 1 ] || fail "ssh unknown mode: said more than once: $(cat "$SANDBOX/out")"
[ ! -e "$S/calls" ] || fail "ssh unknown mode: something reached the host: $(cat "$S/calls")"
code=0; run 0 "$HELLO_PATCH" REMOTE_BACKEND=ssh || code=$?
[ "$code" != 0 ] || fail "ssh no host: exited 0"
grep -q "set REMOTE_SSH_HOST" "$SANDBOX/out" || fail "ssh no host: message: $(cat "$SANDBOX/out")"
[ ! -e "$S/calls" ] || fail "ssh no host: something reached the host: $(cat "$S/calls")"

# Two runs at once on one host: each gets its own container, both finish, both containers go.
W2="$SANDBOX/work2"
make_worktree "$W2"
: >"$S/pair"
echo 0 >"$S/code"
printf '%s' "$HELLO_PATCH" >"$S/patch"
printf 'CLAUDE_CODE_OAUTH_TOKEN=sk-claude-secret\nREMOTE_BACKEND=ssh\nREMOTE_SSH_HOST=me@build-box\n' \
  >"$SANDBOX/config/dotagents/remote.env"
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
printf 'CLAUDE_CODE_OAUTH_TOKEN=sk-claude-secret\n' >"$SANDBOX/config/dotagents/remote.env"
printf '%s\n' "${bare[@]}" >>"$SANDBOX/config/dotagents/remote.env"
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

# The next acquire removes run folders older than REMOTE_HOURS and keeps fresh ones.
[ -z "$f" ] || touch -t 200001010000 "$f"
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

# Opt-in: one real task in a real container of the image named, with the real token from the environment.
if [ -n "${REMOTE_DOCKER_IMAGE_TEST:-}" ]; then
  if ! docker info >/dev/null 2>&1 || [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
    fail "real docker: REMOTE_DOCKER_IMAGE_TEST needs Docker running and CLAUDE_CODE_OAUTH_TOKEN set"
  else
    R="$SANDBOX/real"
    make_worktree "$R"
    printf 'REMOTE_BACKEND=docker\nREMOTE_IMAGE=%s\n' "$REMOTE_DOCKER_IMAGE_TEST" >"$SANDBOX/config/dotagents/remote.env"
    (cd "$R" && HOME="$H" XDG_CONFIG_HOME="$SANDBOX/config" \
      "$REPO/scripts/remote-agent.sh" "Create hello.txt containing the word hello.") >"$SANDBOX/out" 2>&1 \
      || fail "real docker: exited non-zero: $(tail -5 "$SANDBOX/out")"
    [ -f "$R/hello.txt" ] || fail "real docker: hello.txt not created"
    left="$(docker ps -aq --filter label=sandbox.users.io/remote-agent-run)"
    [ -z "$left" ] || fail "real docker: containers left behind: $left"
  fi
fi

[ "$fails" -eq 0 ] || { echo "remote-backends.test.sh: $fails failure(s)"; exit 1; }
echo "remote-backends.test.sh: ok"
