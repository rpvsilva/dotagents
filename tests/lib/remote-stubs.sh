#!/usr/bin/env bash
# Stubs and fixtures for the remote-agent backend tests; sourced, not a test itself.
# State lives in a folder $STATE: every stub call is a line in $STATE/calls, and each container is a folder
# $STATE/containers/<name> that is its $HOME. The sandbox script (argv holding `remote-agent-sandbox`) is faked:
# it saves its stdin to $STATE/stdin, writes out/changes.patch from $STATE/patch (empty = no changes) and
# out/summary.md, and exits $STATE/code. With $STATE/hang present it touches $STATE/started and sleeps instead.
# It also saves the script it was given (`bash -c <script>`) to $STATE/script, and with $STATE/pair present it
# first waits for a second container to exist and appends how many it saw to $STATE/seen.

# Writes a stub `docker` into <bin> keeping its state in <state>: run -d, exec [-i], rm -f, and for `remote-agent
# check` info, image inspect and pull. The daemon is down with <state>/daemon-down; its architecture is
# <state>/daemon-arch (x86_64). Images are present, of architecture <state>/image-arch (amd64), except with
# <state>/image-missing, which a pull removes unless <state>/no-pull exists. With <state>/corrupt present, exec
# changes out/ping before a command that writes a tar, so what comes back differs from what went in. With
# <state>/padding present, such a command's tar is followed, a moment later, by 200 KB of zeros: the remote tar's
# trailing padding arriving after the reading tar (GNU or bsdtar) stopped at the end-of-archive marker.
stub_docker() {
  local bin="$1" state="$2"
  mkdir -p "$bin" "$state/containers"
  cat >"$bin/docker" <<EOF
#!/usr/bin/env bash
state="$state"
EOF
  cat >>"$bin/docker" <<'EOF'
# One line per call. A multi-line argument (a script) goes through tr: bash 3.2's ${x//$'\n'/ } takes seconds on one.
args="$*"
case "$args" in
  *$'\n'*) { printf '%s' "$args" | tr '\n' ' '; echo; } >>"$state/calls" ;;
  *) printf '%s\n' "$args" >>"$state/calls" ;;
esac
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
    [ ! -e "$state/corrupt" ] || [[ " $* " != *"tar -cf"* ]] || echo corrupted >"$home/out/ping"
    # bsdtar (macOS) has no --warning.
    argv=()
    for a in "$@"; do argv+=("${a//--warning=no-unknown-keyword/}"); done
    cd "$home" || exit 1
    if [ -e "$state/padding" ] && [[ " $* " == *"tar -cf"* ]]; then
      HOME="$home" "${argv[@]}" || exit
      sleep 0.3
      exec head -c 200000 /dev/zero
    fi
    HOME="$home" exec "${argv[@]}"
    ;;
  info)
    [ ! -e "$state/daemon-down" ] || { echo "Cannot connect to the Docker daemon. Is the docker daemon running?" >&2; exit 1; }
    cat "$state/daemon-arch" 2>/dev/null || echo x86_64
    ;;
  image)
    [ "$1" = inspect ] || { echo "stub docker: unexpected image $1" >&2; exit 1; }
    [ ! -e "$state/image-missing" ] || { echo "Error: No such image: ${*: -1}" >&2; exit 1; }
    cat "$state/image-arch" 2>/dev/null || echo amd64
    ;;
  pull)
    [ ! -e "$state/no-pull" ] || { echo "Error response from daemon: denied" >&2; exit 1; }
    rm -f "$state/image-missing"
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
# HOME, PATH is STUB_SSH_PATH when set (sh itself is found on the caller's PATH), and the host's main-process stdout
# (/proc/1/fd/1) is the writable file <state>/host-main-stdout, as for root or on a host that is itself a container.
stub_ssh() {
  local bin="$1" state="$2"
  mkdir -p "$bin" "$state"
  cat >"$bin/ssh" <<EOF
#!/usr/bin/env bash
state="$state"
EOF
  cat >>"$bin/ssh" <<'EOF'
# As the docker stub: tr for a multi-line command, which bash 3.2's ${x//$'\n'/ } would take seconds on.
args="ssh $*"
case "$args" in
  *$'\n'*) { printf '%s' "$args" | tr '\n' ' '; echo; } >>"$state/calls" ;;
  *) printf '%s\n' "$args" >>"$state/calls" ;;
esac
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
sh="$(command -v sh)"
if [ -n "${STUB_SSH_HOME:-}" ]; then
  cd "$STUB_SSH_HOME"
  export HOME="$STUB_SSH_HOME"
  [ -z "${STUB_SSH_PATH:-}" ] || export PATH="$STUB_SSH_PATH"
  : >>"$state/host-main-stdout"
  # awk, not ${cmd//...}: the command can hold the whole sandbox script, and bash 3.2 takes seconds on that.
  case "$cmd" in
    */proc/1/fd/1*)
      cmd="$(printf '%s\n' "$cmd" | to="$state/host-main-stdout" awk '{
        s = $0; out = ""
        while ((i = index(s, "/proc/1/fd/1")) > 0) { out = out substr(s, 1, i - 1) ENVIRON["to"]; s = substr(s, i + 12) }
        print out s
      }')"
      ;;
  esac
fi
exec "$sh" -c "$cmd"
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

# Succeeds when process <pid> is still running <tenths> tenths of a second on (default 30: a stopped process may take
# a moment to go); fails at once for an empty pid. A process nothing may signal needs only a short look (5), checked
# once the call that could have signalled it has returned.
alive() {
  [ -n "$1" ] || return 1
  for _ in $(seq "${2:-30}"); do kill -0 "$1" 2>/dev/null || return 1; sleep 0.1; done
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

# Writes <config>/dotagents/remote.env holding only the test token, and <config>/dotagents/settings.json holding the
# remote Settings given as REMOTE_<KEY>=<value> assignments (shell-quoted, as remote.env once held them), each set
# through scripts/settings.sh so it is validated. Needs $REPO.
remote_config() {
  local cfg="$1" a name value
  shift
  mkdir -p "$cfg/dotagents"
  echo "CLAUDE_CODE_OAUTH_TOKEN=sk-claude-secret" >"$cfg/dotagents/remote.env"
  rm -f "$cfg/dotagents/settings.json"
  for a in "$@"; do
    name="${a%%=*}"
    value="$(eval "$a"; printf '%s' "${!name}")"
    XDG_CONFIG_HOME="$cfg" "$REPO/scripts/settings.sh" set remote "$(printf '%s' "${name#REMOTE_}" | tr '[:upper:]' '[:lower:]')" \
      "$value" --machine >/dev/null || return 1
  done
}
