#!/usr/bin/env bash
# Docker backend for scripts/remote-agent.sh: a fresh container of the runner image on this machine per run,
# driven with `docker exec`. Only Docker and the image are needed.
# Usage: docker.sh acquire <run> | put <run> <container> <dir> | run <run> <container> <argv>... | get <run> <container> <dir> | release <run> | check | ping
# acquire starts container <run> and prints its name; its command is `sleep <REMOTE_HOURS*3600>` under --rm, so it
# removes itself after REMOTE_HOURS if release never comes. The container gets the run's share of the host, as
# --memory (with --memory-swap the same: no swap past it) and --cpus: REMOTE_SANDBOX_MB and REMOTE_SANDBOX_CPUS, else
# the host's memory and CPUs (docker info) over REMOTE_MAX_SANDBOXES (4), since other runs, and other people's, share
# it. A share under 2048 MB is said: Claude and the tests may not fit. The sandbox derives its commands' memory cap
# from that limit (scripts/remote-agent-sandbox.sh).
# put extracts the tar on stdin under the container's $HOME/<dir>; get writes a tar of $HOME/<dir> on stdout.
# release removes the container (safe when acquire failed halfway), or says it kept it under REMOTE_KEEP.
# The container is labelled sandbox.users.io/remote-agent-run=<run> and, with REMOTE_TASK (set by remote-agent.sh
# from --name), sandbox.users.io/remote-agent-task=<task>: the cluster's keys, so
# `docker ps --filter label=sandbox.users.io/remote-agent-task` lists the named runs.
# check names what is missing: docker, the Docker daemon, the image (pulled when absent); and notes on stderr when the
# image's architecture differs from the machine's (it would run emulated).
# ping runs `docker info` (`remote-agent ready`): exit 0 when the daemon answers, else one line on stdout with its error
# and exit 1.
# Settings: REMOTE_IMAGE (the runner image, required; the `image` Setting supplies its default), REMOTE_PLATFORM
# (passed as --platform when set; otherwise Docker picks the native architecture), REMOTE_HOURS (2), REMOTE_KEEP,
# REMOTE_SANDBOX_MB, REMOTE_SANDBOX_CPUS, REMOTE_MAX_SANDBOXES.
# ssh.sh runs this script with DOCKER_SSH_HOST set: every docker command then runs on that host, one ssh call each,
# its words quoted for the remote shell and its streams through the connection; as `sudo -n docker` under
# REMOTE_SSH_SUDO (an SSH user outside the docker group, with passwordless sudo). check names that fix when plain
# docker is refused there but sudo's works.
set -euo pipefail

# shellcheck source=lib.sh disable=SC1091
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

image="${REMOTE_IMAGE:-}"
hours="${REMOTE_HOURS:-2}"

# Sets cmd to the command line for `docker <args>`: here, or on DOCKER_SSH_HOST.
docker_cmd() {
  if [ -n "${DOCKER_SSH_HOST:-}" ]; then
    ssh_client
    local docker=(docker)
    [ -z "${REMOTE_SSH_SUDO:-}" ] || docker=(sudo -n docker)
    # shellcheck disable=SC2154 # set by ssh_client
    cmd=("${ssh_cmd[@]}" "$DOCKER_SSH_HOST" "$(sq_argv "${docker[@]}" "$@")")
  else
    cmd=(docker "$@")
  fi
}
dk() { docker_cmd "$@"; "${cmd[@]}"; }
where="${DOCKER_SSH_HOST:+ on $DOCKER_SSH_HOST}"

# Docker's name for an architecture: amd64 for x86_64, arm64 for aarch64.
arch_name() {
  case "$1" in
    x86_64|amd64) echo amd64 ;;
    aarch64|arm64) echo arm64 ;;
    *) echo "$1" ;;
  esac
}

# Sets limits to the docker run options giving one run its share of the host, and share to how it reads
# (`4096 MB, 2 CPUs`); fails, saying why, on a REMOTE_SANDBOX_CPUS that is not a number.
run_share() {
  local mb="${REMOTE_SANDBOX_MB:-}" cpus="${REMOTE_SANDBOX_CPUS:-}" n="${REMOTE_MAX_SANDBOXES:-4}" total="" ncpu=""
  if [ -n "$cpus" ] && [[ ! "$cpus" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
    say "REMOTE_SANDBOX_CPUS=$cpus is not a number of CPUs (remote.sandbox_cpus)"
    return 1
  fi
  [[ "$n" =~ ^[1-9][0-9]*$ ]] || n=4
  if [ -z "$mb" ] || [ -z "$cpus" ]; then
    # shellcheck disable=SC2016 # a Go template for docker
    read -r total ncpu < <(dk info --format '{{.MemTotal}} {{.NCPU}}' </dev/null 2>/dev/null || true) || true
    [[ "${total:-}" =~ ^[0-9]+$ ]] && [ -z "$mb" ] && mb=$((total / 1048576 / n))
    [[ "${ncpu:-}" =~ ^[0-9]+$ ]] && [ -z "$cpus" ] && cpus="$(awk -v c="$ncpu" -v n="$n" 'BEGIN { x = c / n; if (x < 0.01) x = 0.01; printf "%g", x }')"
  fi
  limits=()
  share=""
  if [ -n "$mb" ] && [ "$mb" -gt 0 ]; then
    limits+=(--memory "${mb}m" --memory-swap "${mb}m")
    share="$mb MB"
    [ "$mb" -ge 2048 ] || say "note: each run gets $mb MB$where, under 2048 MB: Claude and the tests may not fit (remote.sandbox_mb, or fewer remote.max_sandboxes)"
  fi
  if [ -n "$cpus" ]; then
    limits+=(--cpus "$cpus")
    share="${share:+$share, }$cpus CPUs"
  fi
  return 0
}

# GNU tar in the image would warn about the xattr headers macOS tar adds.
untar='tar -x --no-same-owner --warning=no-unknown-keyword -f -'

op="${1:-}"
case "$op" in
  acquire|check|ping)
    if [ -z "$image" ]; then
      problem="REMOTE_IMAGE not set: the runner image (remote.image; \`remote-agent setup\`)"
      if [ "$op" = acquire ]; then say "$problem"; else echo "$problem"; fi
      exit 1
    fi
    ;;
esac
case "$op" in
  acquire)
    name="$2"
    args=(run -d --rm --name "$name" --label "sandbox.users.io/remote-agent-run=$name")
    [ -z "${REMOTE_TASK:-}" ] || args+=(--label "sandbox.users.io/remote-agent-task=$REMOTE_TASK")
    run_share || exit 1
    args+=(${limits[@]+"${limits[@]}"})
    [ -z "${REMOTE_PLATFORM:-}" ] || args+=(--platform "$REMOTE_PLATFORM")
    dk "${args[@]}" "$image" sleep $((hours * 3600)) </dev/null >/dev/null
    say "started container $name$where ($image${share:+; $share})"
    printf '%s\n' "$name"
    ;;
  put)
    dir="\"\$HOME\"/$(sq "$4")"
    dk exec -i "$3" sh -c "mkdir -p $dir && $untar -C $dir"
    ;;
  run)
    container="$3"
    shift 3
    # exec: remote-agent stops this run by signalling its process group, so the docker (or ssh) client goes with it.
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
  check)
    if [ -z "${DOCKER_SSH_HOST:-}" ] && ! command -v docker >/dev/null 2>&1; then echo "docker not found on PATH"; exit 1; fi
    # shellcheck disable=SC2016 # a Go template for docker
    if ! machine="$(dk info --format '{{.Architecture}}' </dev/null 2>/dev/null)"; then
      if [ -n "${DOCKER_SSH_HOST:-}" ] && [ -n "${REMOTE_SSH_SUDO:-}" ]; then
        echo "Docker daemon not reachable$where (sudo -n docker info failed: is it running, and may this user sudo without a password?)"
      elif [ -n "${DOCKER_SSH_HOST:-}" ] && REMOTE_SSH_SUDO=1 dk info </dev/null >/dev/null 2>&1; then
        echo "Docker$where works only through sudo (this user is not in the docker group). Either:"
        echo "  run docker with sudo: ~/.agents/bin/settings set remote ssh_sudo true --machine"
        echo "  or add the user to the group, once: ssh $DOCKER_SSH_HOST 'sudo usermod -aG docker \$USER' (takes effect on the next login)"
      else
        echo "Docker daemon not reachable$where (docker info failed: is it running, and may this user use it?)"
      fi
      exit 1
    fi
    # shellcheck disable=SC2016
    inspect() { dk image inspect --format '{{.Architecture}}' "$image" </dev/null 2>/dev/null; }
    if ! arch="$(inspect)"; then
      say "pulling $image$where"
      if ! dk pull "$image" </dev/null >/dev/null 2>&1 || ! arch="$(inspect)"; then
        login="docker login"
        [ -z "${DOCKER_SSH_HOST:-}" ] || [ -z "${REMOTE_SSH_SUDO:-}" ] || login="sudo docker login, as root pulls under ssh_sudo,"
        echo "image $image$where: not present and not pullable ($login to its registry, or build it and set REMOTE_IMAGE)"
        exit 1
      fi
    fi
    if [ "$(arch_name "$arch")" != "$(arch_name "$machine")" ]; then
      say "note: image $image is $(arch_name "$arch"), the machine$where is $(arch_name "$machine"): it runs emulated, slowly"
    fi
    ;;
  ping)
    if [ -z "${DOCKER_SSH_HOST:-}" ] && ! command -v docker >/dev/null 2>&1; then echo "docker not found on PATH"; exit 1; fi
    # shellcheck disable=SC2016 # a Go template for docker
    if ! err="$(dk info --format '{{.ServerVersion}}' </dev/null 2>&1 >/dev/null)"; then
      echo "Docker daemon not reachable$where: $(printf '%s\n' "$err" | grep . | tail -1 || true)"
      exit 1
    fi
    ;;
  *)
    echo "usage: docker.sh acquire|put|run|get|release|check|ping <run> [<container> ...]" >&2
    exit 1
    ;;
esac
