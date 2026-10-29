#!/usr/bin/env bash
# SSH backend for scripts/remote-agent.sh: a VM or PC reached with `ssh`.
# Usage: ssh.sh acquire <run> | put <run> <handle> <dir> | run <run> <handle> <argv>... | get <run> <handle> <dir> | release <run> | check
# Every command runs on the host through `ssh -T -o BatchMode=yes <host> '<quoted words>'`, its words single-quoted
# for any POSIX login shell. Files travel as tar streams over the connection: no scp, no temp files.
# Container mode (the default) needs only SSH access and Docker on the host: the docker backend (docker.sh) with each
# docker command sent over ssh. Each run's container is named after it, so runs share the host; `--rm` and its
# `sleep` remove it after REMOTE_HOURS if release never comes.
# Bare mode runs directly on a trusted host as the SSH user (weaker isolation: no container, no network limits).
# acquire checks the host has bash tar git jq node claude (naming each missing one, creating nothing), removes run
# folders older than REMOTE_HOURS left by clients that died, and creates the run folder $REMOTE_SSH_DIR/<run> (mode
# 700), printing its absolute path as the handle. run uses that folder as HOME, with the XDG folders and Claude
# Code's config inside it, so runs stay apart from the host user's own setup and from each other. put and get use
# plain tar flags (the host's tar may be GNU or BSD). run records the command's pid (and process group, under setsid
# where the host has it) in the folder's .pid, since killing the ssh client does not stop it; release kills what that
# names (TERM, then KILL), then removes the folder, or keeps it under REMOTE_KEEP.
# check names what is missing: the host with key-based ssh, then docker.sh's check on the host (container mode) or
# the tools acquire checks (bare mode).
# Settings: REMOTE_SSH_HOST (ssh destination: user@host or a ~/.ssh/config alias), REMOTE_SSH_MODE (container or
# bare), REMOTE_HOURS (2), REMOTE_KEEP; container mode: docker.sh's REMOTE_IMAGE and REMOTE_PLATFORM; bare mode:
# REMOTE_SSH_DIR (run folders' root on the host: absolute, or relative to the SSH user's home; .remote-agent/runs).
set -euo pipefail

here="$(dirname "${BASH_SOURCE[0]}")"
# shellcheck source=lib.sh disable=SC1091
. "$here/lib.sh"

op="${1:-}"
mode="${REMOTE_SSH_MODE:-container}"
problem=""
case "$mode" in
  container|bare) ;;
  *) problem="unknown REMOTE_SSH_MODE=$mode (known: container bare)" ;;
esac
[ -n "$problem" ] || [ -n "${REMOTE_SSH_HOST:-}" ] || problem="set REMOTE_SSH_HOST (user@host or an ~/.ssh/config alias)"
if [ -n "$problem" ]; then
  # acquire already said it; release has nothing to remove.
  [ "$op" = release ] && exit 0
  if [ "$op" = check ]; then echo "$problem"; else say "$problem"; fi
  exit 1
fi
if [ "$op" = check ] && ! ssh -T -o BatchMode=yes "$REMOTE_SSH_HOST" true </dev/null >/dev/null 2>&1; then
  echo "cannot reach $REMOTE_SSH_HOST with key-based ssh (ssh -o BatchMode=yes $REMOTE_SSH_HOST true fails)"
  exit 1
fi

[ "$mode" = bare ] || DOCKER_SSH_HOST="$REMOTE_SSH_HOST" exec "$here/docker.sh" "$@"

host="$REMOTE_SSH_HOST"
hours="${REMOTE_HOURS:-2}"
# Relative to the SSH user's home, where the remote shell starts; ~/ means the same.
root="${REMOTE_SSH_DIR:-.remote-agent/runs}"
root="${root#\~/}"
tools="bash tar git jq node claude"
# A host command naming each missing tool on its own line on stderr, failing when any is; acquire and check run it.
# shellcheck disable=SC2016 # expanded on the host
tool_check='missing=0
for t in '"$tools"'; do
  command -v "$t" >/dev/null 2>&1 || { echo "missing on the host: $t (bare mode needs '"$tools"')" >&2; missing=1; }
done
[ "$missing" = 0 ] || exit 1'

# Runs one POSIX shell command line on the host, its streams through the connection.
on_host() { ssh -T -o BatchMode=yes "$host" "$1"; }

case "$op" in
  acquire)
    run="$2"
    # shellcheck disable=SC2016 # expanded on the host
    on_host "$tool_check"'
root='"$(sq "$root")"'
mkdir -p "$root" &&
find "$root" -mindepth 1 -maxdepth 1 -name "agent-*" -mmin +'"$((hours * 60))"' -exec rm -rf {} + &&
(umask 077 && mkdir "$root"/'"$(sq "$run")"') &&
cd "$root"/'"$(sq "$run")"' && pwd' </dev/null
    say "created run folder $run on $host (bare mode)"
    ;;
  put)
    dir="$(sq "$3")/$(sq "$4")"
    # Extracting into . would give the run folder the mode of the tar's root: back to 700.
    on_host "mkdir -p $dir && tar -xf - -C $dir && chmod 700 $(sq "$3")"
    ;;
  run)
    home="$3"
    shift 3
    # Claude Code reads CLAUDE_CONFIG_DIR before HOME: set it, so a value from the host user's profile is not used.
    # REMOTE_AGENT_LOG: the sandbox log is progress.log here, never the host's /proc/1/fd/1 (writable as root, or on
    # a host that is itself a container, where it is another process's stdout).
    argv="$(sq_argv env "HOME=$home" "XDG_CONFIG_HOME=$home/.config" "XDG_DATA_HOME=$home/.local/share" \
      "XDG_CACHE_HOME=$home/.cache" "CLAUDE_CONFIG_DIR=$home/.claude" "REMOTE_AGENT_LOG=$home/progress.log" "$@")"
    # Killing the ssh client does not stop the command on the host, so release kills it by the line each run adds to
    # .pid: "<pid> group" when the command leads its own process group (started under setsid, from a child shell so
    # setsid needs no fork; else when the login shell already leads one), "<pid>" when it does not.
    # exec: remote-agent stops this run by signalling its process group, so the ssh client goes with it.
    # shellcheck disable=SC2016 # expanded by the sh setsid starts
    exec ssh -T -o BatchMode=yes "$host" "cd $(sq "$home") || exit 1
if command -v setsid >/dev/null 2>&1; then
  setsid sh -c $(sq 'echo "$$ group" >>.pid && exec "$@"') sh $argv
  exit \$?
fi
g=
[ \"\$(ps -o pgid= -p \$\$ 2>/dev/null | tr -d ' ')\" != \"\$\$\" ] || g=group
echo \"\$\$ \$g\" >>.pid && exec $argv"
    ;;
  get)
    on_host "tar -cf - -C $(sq "$3")/$(sq "$4") ." </dev/null
    ;;
  release)
    # Kills what each line of .pid names (see run): TERM, up to 3 s for it to go, then KILL. A group goes whole; a
    # plain pid takes its children with it through pkill -P, where the host has pkill.
    # shellcheck disable=SC2016 # expanded on the host
    remove='rm -rf "$d"'
    [ -z "${REMOTE_KEEP:-}" ] || remove=:
    # shellcheck disable=SC2016 # expanded on the host
    on_host 'd='"$(sq "$root")/$(sq "$2")"'
signal() {
  while read -r p g; do
    case "$p" in ""|*[!0-9]*) continue ;; esac
    if [ "$g" = group ]; then
      kill -s "$1" -- "-$p" 2>/dev/null && [ "$1" = 0 ] && return 0
    else
      [ "$1" = 0 ] || pkill -"$1" -P "$p" 2>/dev/null
      kill -s "$1" -- "$p" 2>/dev/null && [ "$1" = 0 ] && return 0
    fi
  done <"$d/.pid"
  [ "$1" != 0 ]
}
if [ -f "$d/.pid" ]; then
  signal TERM
  i=0
  while [ "$i" -lt 3 ] && signal 0; do sleep 1; i=$((i + 1)); done
  signal KILL
fi
'"$remove" </dev/null >/dev/null 2>&1 || true
    if [ -n "${REMOTE_KEEP:-}" ]; then
      say "kept run folder $root/$2 on $host (the next run's acquire removes it after REMOTE_HOURS)"
    fi
    ;;
  check)
    on_host "$tool_check" </dev/null 2>&1
    ;;
  *)
    echo "usage: ssh.sh acquire|put|run|get|release|check <run> [<handle> ...]" >&2
    exit 1
    ;;
esac
