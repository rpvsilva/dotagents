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
# plain tar flags (the host's tar may be GNU or BSD). release removes the folder, or keeps it under REMOTE_KEEP.
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
mkdir -m 700 "$root"/'"$(sq "$run")"' &&
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
    # exec: when remote-agent stops this run, the ssh client goes with it.
    exec ssh -T -o BatchMode=yes "$host" "cd $(sq "$home") && exec $(sq_argv env "HOME=$home" \
      "XDG_CONFIG_HOME=$home/.config" "XDG_DATA_HOME=$home/.local/share" "XDG_CACHE_HOME=$home/.cache" \
      "CLAUDE_CONFIG_DIR=$home/.claude" "$@")"
    ;;
  get)
    on_host "tar -cf - -C $(sq "$3")/$(sq "$4") ." </dev/null
    ;;
  release)
    if [ -n "${REMOTE_KEEP:-}" ]; then
      say "kept run folder $root/$2 on $host (the next run's acquire removes it after REMOTE_HOURS)"
    else
      on_host "rm -rf $(sq "$root")/$(sq "$2")" </dev/null >/dev/null 2>&1 || true
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
