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
# folders left by clients that died, and creates the run folder $REMOTE_SSH_DIR/<run> (mode 700), recording in its
# .expires the host's clock + REMOTE_HOURS, and prints its absolute path as the handle. A folder goes once the host's
# clock is past its .expires and its .heartbeat (touched every 60 s while run's command lives) is missing or older
# than 3 minutes, or once the clock is more than 10 minutes past its .expires whatever the heartbeat (a hard limit:
# what still runs there is killed first, as release does); one with no .expires (an older client's) once its mtime is
# older than REMOTE_HOURS. run uses that folder as HOME, with the XDG folders and Claude Code's config inside it, so
# runs stay apart from the host user's own setup and from each other. put and get use plain tar flags (the host's tar
# may be GNU or BSD). run records the command's pid (and process group, under setsid where the host has it) in the
# folder's .pid, since killing the ssh client does not stop it; release kills what that names (TERM, then KILL), then
# removes the folder, or keeps it under REMOTE_KEEP. A pid counts as the run's, for the heartbeat and for any signal,
# only while its working folder is inside the run folder (read from /proc, else lsof): a pid reused by another
# process is never signalled.
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
# Host shell functions. ours <pid> <run folder, physical path>: the process's working folder is inside the run folder
# (from /proc where the host has it, else lsof), so a pid reused by another process is not taken for the run's.
# shellcheck disable=SC2016 # expanded on the host
ours_fn='ours() {
  c=$(readlink "/proc/$1/cwd" 2>/dev/null) || c=$(lsof -a -p "$1" -d cwd -Fn 2>/dev/null | sed -n "s/^n//p")
  case "$c" in "") return 1 ;; "$2"|"$2"/*) return 0 ;; esac
  return 1
}
'
# stop <run folder>: kills what each line of its .pid names (see run) and is still the run's: TERM, up to 3 s for it to
# go, then KILL. A group goes whole (its id stays taken while any member lives, so it is checked once, up front); a
# plain pid takes its children with it through pkill -P, where the host has pkill.
# shellcheck disable=SC2016 # expanded on the host
stop_fn="$ours_fn"'signal() {
  for x in $ok; do
    p=${x%%:*}
    if [ "${x#*:}" = group ]; then
      kill -s "$1" -- "-$p" 2>/dev/null && [ "$1" = 0 ] && return 0
    else
      [ "$1" = 0 ] || pkill -"$1" -P "$p" 2>/dev/null
      kill -s "$1" -- "$p" 2>/dev/null && [ "$1" = 0 ] && return 0
    fi
  done
  [ "$1" != 0 ]
}
stop() {
  [ -f "$1/.pid" ] || return 0
  r=$(cd "$1" && pwd -P) || return 0
  ok=
  while read -r p g; do
    case "$p" in ""|*[!0-9]*) continue ;; esac
    if ours "$p" "$r"; then ok="$ok $p:$g"; fi
  done <"$1/.pid"
  [ -n "$ok" ] || return 0
  signal TERM
  i=0
  while [ "$i" -lt 3 ] && signal 0; do sleep 1; i=$((i + 1)); done
  signal KILL
}
'

# Runs one POSIX shell command line on the host, its streams through the connection.
on_host() { ssh -T -o BatchMode=yes "$host" "$1"; }

case "$op" in
  acquire)
    run="$2"
    # shellcheck disable=SC2016 # expanded on the host
    on_host "$tool_check
$stop_fn"'
root='"$(sq "$root")"'
mkdir -p "$root" || exit 1
now=$(date +%s)
for d in "$root"/agent-*; do
  [ -d "$d" ] || continue
  e=$(cat "$d/.expires" 2>/dev/null)
  case "$e" in
    ""|*[!0-9]*) [ -z "$(find "$d" -maxdepth 0 -mmin +'"$((hours * 60))"')" ] || rm -rf "$d" ;;
    *)
      [ "$now" -gt "$e" ] || continue
      if [ "$now" -le $((e + 600)) ] && [ -e "$d/.heartbeat" ] && [ -z "$(find "$d/.heartbeat" -mmin +3)" ]; then
        continue
      fi
      stop "$d" >/dev/null 2>&1
      rm -rf "$d"
      ;;
  esac
done
(umask 077 && mkdir "$root"/'"$(sq "$run")"' &&
  echo $((now + '"$((hours * 3600))"')) >"$root"/'"$(sq "$run")"'/.expires) &&
cd "$root"/'"$(sq "$run")"' && pwd' </dev/null
    say "created run folder $run on $host (bare mode)"
    ;;
  put)
    dir="$(sq "$3")/$(sq "$4")"
    x="$(sq "$3")/.expires"
    # Extracting into . would give the run folder the mode of the tar's root: back to 700. The run's .expires (see
    # acquire) is put back as it was, whatever the tar held.
    on_host "e=\$(cat $x 2>/dev/null); mkdir -p $dir && tar -xf - -C $dir && chmod 700 $(sq "$3") &&
if [ -n \"\$e\" ]; then rm -f $x && echo \"\$e\" >$x; fi"
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
    # Before the exec, a loop touches .heartbeat every 60 s while that pid is the run's (see ours), so acquire's
    # cleanup spares the run past its .expires; it holds none of the connection's streams, so the call returns when
    # the run ends. It is a child of the run (in its group under setsid), so release kills it with the run.
    # exec: remote-agent stops this run by signalling its process group, so the ssh client goes with it.
    # shellcheck disable=SC2016 # expanded on the host
    beat="$ours_fn"'(r=$(pwd -P); while ours $$ "$r"; do touch .heartbeat; sleep 60; done) </dev/null >/dev/null 2>&1 &'
    # shellcheck disable=SC2016 # expanded by the sh setsid starts
    exec ssh -T -o BatchMode=yes "$host" "cd $(sq "$home") || exit 1
if command -v setsid >/dev/null 2>&1; then
  setsid sh -c $(sq 'echo "$$ group" >>.pid || exit 1; '"$beat"' exec "$@"') sh $argv
  exit \$?
fi
g=
[ \"\$(ps -o pgid= -p \$\$ 2>/dev/null | tr -d ' ')\" != \"\$\$\" ] || g=group
echo \"\$\$ \$g\" >>.pid || exit 1
$beat
exec $argv"
    ;;
  get)
    on_host "tar -cf - -C $(sq "$3")/$(sq "$4") ." </dev/null
    ;;
  release)
    # Kills what still runs for the run (stop, above), then removes its folder.
    # shellcheck disable=SC2016 # expanded on the host
    remove='rm -rf "$d"'
    [ -z "${REMOTE_KEEP:-}" ] || remove=:
    # shellcheck disable=SC2016 # expanded on the host
    on_host "$stop_fn"'d='"$(sq "$root")/$(sq "$2")"'
stop "$d"
'"$remove" </dev/null >/dev/null 2>&1 || true
    if [ -n "${REMOTE_KEEP:-}" ]; then
      say "kept run folder $root/$2 on $host (an acquire removes it once the host's clock is past its .expires and its" \
        ".heartbeat is 3 minutes old)"
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
