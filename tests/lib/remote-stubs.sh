#!/usr/bin/env bash
# Stubs and fixtures for the remote-agent backend tests; sourced, not a test itself.
# State lives in a folder $STATE: every stub call is a line in $STATE/calls, and each container is a folder
# $STATE/containers/<name> that is its $HOME. The sandbox script (argv holding `remote-agent-sandbox`) is faked:
# it saves its stdin to $STATE/stdin, writes out/changes.patch from $STATE/patch (empty = no changes) and
# out/summary.md, and exits $STATE/code. With $STATE/hang present it touches $STATE/started and sleeps instead.

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
