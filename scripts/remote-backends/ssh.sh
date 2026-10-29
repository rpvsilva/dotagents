#!/usr/bin/env bash
# SSH backend for scripts/remote-agent.sh: a VM or PC reached with `ssh`, needing only SSH access and Docker there.
# Usage: ssh.sh acquire <run> | put <run> <handle> <dir> | run <run> <handle> <argv>... | get <run> <handle> <dir> | release <run>
# Container mode (the default): the docker backend (docker.sh) with each docker command run on the host through
# `ssh -T -o BatchMode=yes <host> '<quoted words>'`. Files travel as tar streams over the connection: no scp, no
# temp files. Each run's container is named after it, so runs share the host; `--rm` and its `sleep` remove it
# after REMOTE_HOURS if release never comes.
# Settings: REMOTE_SSH_HOST (ssh destination: user@host or a ~/.ssh/config alias), REMOTE_SSH_MODE (container),
# and docker.sh's: REMOTE_IMAGE, REMOTE_PLATFORM, REMOTE_HOURS, REMOTE_KEEP.
set -euo pipefail

here="$(dirname "${BASH_SOURCE[0]}")"
# shellcheck source=lib.sh disable=SC1091
. "$here/lib.sh"

op="${1:-}"
mode="${REMOTE_SSH_MODE:-container}"
problem=""
case "$mode" in
  container) ;;
  bare) problem="REMOTE_SSH_MODE=bare is not available yet (use container)" ;;
  *) problem="unknown REMOTE_SSH_MODE=$mode (known: container bare)" ;;
esac
[ -n "$problem" ] || [ -n "${REMOTE_SSH_HOST:-}" ] || problem="set REMOTE_SSH_HOST (user@host or an ~/.ssh/config alias)"
if [ -n "$problem" ]; then
  # acquire already said it; release has nothing to remove.
  [ "$op" = release ] && exit 0
  say "$problem"
  exit 1
fi

DOCKER_SSH_HOST="$REMOTE_SSH_HOST" exec "$here/docker.sh" "$@"
