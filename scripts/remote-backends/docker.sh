#!/usr/bin/env bash
# Docker backend for scripts/remote-agent.sh: a fresh container of the runner image on this machine per run,
# driven with `docker exec`. Only Docker and the image are needed.
# Usage: docker.sh acquire <run> | put <run> <container> <dir> | run <run> <container> <argv>... | get <run> <container> <dir> | release <run>
# acquire starts container <run> and prints its name; its command is `sleep <REMOTE_HOURS*3600>` under --rm, so it
# removes itself after REMOTE_HOURS if release never comes.
# put extracts the tar on stdin under the container's $HOME/<dir>; get writes a tar of $HOME/<dir> on stdout.
# release removes the container (safe when acquire failed halfway), or says it kept it under REMOTE_KEEP.
# Settings: REMOTE_IMAGE (ghcr.io/xgeekshq/agent-runner:main), REMOTE_PLATFORM (passed as --platform when set;
# otherwise Docker picks the native architecture), REMOTE_HOURS (2), REMOTE_KEEP.
# ssh.sh runs this script with DOCKER_SSH_HOST set: every docker command then runs on that host, one ssh call each,
# its words quoted for the remote shell and its streams through the connection.
set -euo pipefail

# shellcheck source=lib.sh disable=SC1091
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

image="${REMOTE_IMAGE:-ghcr.io/xgeekshq/agent-runner:main}"
hours="${REMOTE_HOURS:-2}"

# Sets cmd to the command line for `docker <args>`: here, or on DOCKER_SSH_HOST.
docker_cmd() {
  if [ -n "${DOCKER_SSH_HOST:-}" ]; then
    cmd=(ssh -T -o BatchMode=yes "$DOCKER_SSH_HOST" "$(sq_argv docker "$@")")
  else
    cmd=(docker "$@")
  fi
}
dk() { docker_cmd "$@"; "${cmd[@]}"; }
where="${DOCKER_SSH_HOST:+ on $DOCKER_SSH_HOST}"

# GNU tar in the image would warn about the xattr headers macOS tar adds.
untar='tar -x --no-same-owner --warning=no-unknown-keyword -f -'

op="${1:-}"
case "$op" in
  acquire)
    name="$2"
    args=(run -d --rm --name "$name" --label dotagents.remote-agent=1)
    [ -z "${REMOTE_PLATFORM:-}" ] || args+=(--platform "$REMOTE_PLATFORM")
    dk "${args[@]}" "$image" sleep $((hours * 3600)) </dev/null >/dev/null
    say "started container $name$where ($image)"
    printf '%s\n' "$name"
    ;;
  put)
    dir="\"\$HOME\"/$(sq "$4")"
    dk exec -i "$3" sh -c "mkdir -p $dir && $untar -C $dir"
    ;;
  run)
    container="$3"
    shift 3
    # exec: when remote-agent stops this run, the docker (or ssh) client goes with it.
    docker_cmd exec -i "$container" "$@"
    exec "${cmd[@]}"
    ;;
  get)
    dk exec "$3" sh -c "tar -cf - -C \"\$HOME\"/$(sq "$4") ." </dev/null
    ;;
  release)
    if [ -n "${REMOTE_KEEP:-}" ]; then
      say "kept container $2$where (it removes itself after REMOTE_HOURS; docker rm -f $2 to remove it now)"
    else
      dk rm -f "$2" </dev/null >/dev/null 2>&1 || true
    fi
    ;;
  *)
    echo "usage: docker.sh acquire|put|run|get|release <run> [<container> ...]" >&2
    exit 1
    ;;
esac
