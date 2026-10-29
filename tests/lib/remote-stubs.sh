#!/usr/bin/env bash
# Stubs and fixtures for the remote-agent backend tests; sourced, not a test itself.
# State lives in a folder $STATE: every stub call is a line in $STATE/calls, and each container is a folder
# $STATE/containers/<name> that is its $HOME. The sandbox script (argv holding `remote-agent-sandbox`) is faked:
# it saves its stdin to $STATE/stdin, writes out/changes.patch from $STATE/patch (empty = no changes) and
# out/summary.md, and exits $STATE/code. With $STATE/hang present it touches $STATE/started and sleeps instead.
# It also saves the script it was given (`bash -c <script>`) to $STATE/script, and with $STATE/pair present it
# first waits for a second container to exist and appends how many it saw to $STATE/seen.

# Writes a stub `docker` into <bin> keeping its state in <state>: run -d, exec [-i], rm -f.
stub_docker() {
  local bin="$1" state="$2"
  mkdir -p "$bin" "$state/containers"
  cat >"$bin/docker" <<EOF
#!/usr/bin/env bash
state="$state"
EOF
  cat >>"$bin/docker" <<'EOF'
args="$*"
printf '%s\n' "${args//$'\n'/ }" >>"$state/calls"
op="$1"
shift
case "$op" in
  run)
    name=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --name) name="$2"; shift 2 ;;
        --label|--platform) shift 2 ;;
        -*) shift ;;
        *) break ;;
      esac
    done
    [ ! -e "$state/containers/$name" ] || { echo "docker: container name $name in use" >&2; exit 125; }
    mkdir -p "$state/containers/$name"
    echo "0123456789abcdef"
    ;;
  exec)
    while [ "${1:-}" = -i ]; do shift; done
    home="$state/containers/$1"
    [ -d "$home" ] || { echo "Error response from daemon: No such container: $1" >&2; exit 1; }
    shift
    case " $* " in
      *" remote-agent-sandbox "*)
        cat >"$state/stdin"
        [ "$1" != bash ] || printf '%s' "$3" >"$state/script"
        if [ -e "$state/pair" ]; then
          for _ in $(seq 100); do [ "$(ls "$state/containers" | wc -l)" -ge 2 ] && break; sleep 0.1; done
          ls "$state/containers" | wc -l | tr -d ' ' >>"$state/seen"
        fi
        if [ -e "$state/hang" ]; then : >"$state/started"; exec sleep 30; fi
        mkdir -p "$home/out"; cp "$state/patch" "$home/out/changes.patch"; echo "did the thing" >"$home/out/summary.md"
        exit "$(cat "$state/code")" ;;
    esac
    # bsdtar (macOS) has no --warning.
    argv=()
    for a in "$@"; do argv+=("${a//--warning=no-unknown-keyword/}"); done
    cd "$home" && HOME="$home" exec "${argv[@]}"
    ;;
  rm)
    [ "${1:-}" != -f ] || shift
    [ -d "$state/containers/$1" ] || { echo "Error response from daemon: No such container: $1" >&2; exit 1; }
    rm -rf "${state:?}/containers/$1"
    echo "$1"
    ;;
  *) echo "stub docker: unexpected $op" >&2; exit 1 ;;
esac
EOF
  chmod +x "$bin/docker"
}

# Writes a stub `ssh` into <bin> logging to <state>/calls: it drops its options and destination and runs the one
# remote command string with `sh -c` here, so a real POSIX shell parses the quoting. Also a stub `scp` that records
# any call in <state>/scp and fails. With STUB_SSH_HOME set, it plays a bare host: the command starts there with it as
# HOME, PATH is STUB_SSH_PATH when set, and the host has no writable main-process stdout (/proc/1/fd/1).
stub_ssh() {
  local bin="$1" state="$2"
  mkdir -p "$bin" "$state"
  cat >"$bin/ssh" <<EOF
#!/usr/bin/env bash
state="$state"
EOF
  cat >>"$bin/ssh" <<'EOF'
args="ssh $*"
printf '%s\n' "${args//$'\n'/ }" >>"$state/calls"
while [ $# -gt 0 ]; do
  case "$1" in
    -o|-p|-i|-l|-F) shift 2 ;;
    -*) shift ;;
    *) break ;;
  esac
done
shift # the destination
[ $# -eq 1 ] || { echo "stub ssh: wanted one remote command string, got $#" >&2; exit 255; }
cmd="$1"
if [ -n "${STUB_SSH_HOME:-}" ]; then
  cd "$STUB_SSH_HOME"
  export HOME="$STUB_SSH_HOME"
  [ -z "${STUB_SSH_PATH:-}" ] || export PATH="$STUB_SSH_PATH"
  cmd="${cmd//\/proc\/1\/fd\/1/\/nonexistent\/main-stdout}"
fi
exec sh -c "$cmd"
EOF
  cat >"$bin/scp" <<EOF
#!/bin/sh
echo "scp \$*" >>"$state/scp"
exit 1
EOF
  chmod +x "$bin/ssh" "$bin/scp"
}

# Makes a git worktree at <dir> with a tracked file (tracked.txt: one), an untracked one, an ignored one and a
# secret-looking one.
make_worktree() {
  local w="$1"
  git init -q "$w"
  git -C "$w" config user.email t@example.com
  git -C "$w" config user.name t
  printf 'ignored.txt\n' >"$w/.gitignore"
  printf 'one\n' >"$w/tracked.txt"
  git -C "$w" add -A
  git -C "$w" commit -qm base
  printf 'local\n' >"$w/untracked.txt"
  printf 'ignored\n' >"$w/ignored.txt"
  printf 'KEY=1\n' >"$w/.env"
}

# A patch adding hello.txt, and one that does not apply to make_worktree's tracked.txt.
HELLO_PATCH='diff --git a/hello.txt b/hello.txt
new file mode 100644
index 0000000..ce01362
--- /dev/null
+++ b/hello.txt
@@ -0,0 +1 @@
+hello
'
STALE_PATCH='diff --git a/tracked.txt b/tracked.txt
--- a/tracked.txt
+++ b/tracked.txt
@@ -1 +1 @@
-not what the file says
+two
'
export HELLO_PATCH STALE_PATCH
