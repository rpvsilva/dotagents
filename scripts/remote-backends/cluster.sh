#!/usr/bin/env bash
# Cluster backend for scripts/remote-agent.sh: a warm sandbox from agent-sandbox on Kubernetes, claimed with a
# SandboxClaim and driven with `kubectl exec`.
# Usage: cluster.sh acquire <run> | put <run> <pod> <dir> | run <run> <pod> <argv>... | get <run> <pod> <dir> | release <run>
# acquire claims a sandbox and prints its pod; its shutdownTime deletes it after REMOTE_HOURS if release never comes.
# put extracts the tar on stdin under the sandbox's $HOME/<dir>; get writes a tar of $HOME/<dir> on stdout.
# release deletes the claim (safe when acquire failed halfway), or says it kept it under REMOTE_KEEP.
# Settings: REMOTE_CONTEXT (kube context, default maxibombo), REMOTE_NAMESPACE (agents), REMOTE_POOL (claude-runner),
# REMOTE_HOURS (2), REMOTE_KEEP.
set -euo pipefail

# shellcheck source=lib.sh disable=SC1091
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

context="${REMOTE_CONTEXT:-maxibombo}"
namespace="${REMOTE_NAMESPACE:-agents}"
pool="${REMOTE_POOL:-claude-runner}"
hours="${REMOTE_HOURS:-2}"

kc() { kubectl --context "$context" -n "$namespace" "$@"; }

# GNU tar in the pod would warn about the xattr headers macOS tar adds.
untar='tar -x --no-same-owner --warning=no-unknown-keyword -f -'

op="${1:-}"
case "$op" in
  acquire)
    claim="$2"
    end=$(( $(date +%s) + hours * 3600 ))
    # GNU date takes -d @epoch, BSD date -r epoch.
    shutdown="$(date -u -d "@$end" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -r "$end" +%Y-%m-%dT%H:%M:%SZ)"
    kc apply -f - >/dev/null <<YAML
apiVersion: extensions.agents.x-k8s.io/v1beta1
kind: SandboxClaim
metadata:
  name: $claim
spec:
  warmPoolRef:
    name: $pool
  lifecycle:
    shutdownTime: "$shutdown"
    shutdownPolicy: Delete
YAML
    kc wait --for=condition=Ready "sandboxclaim/$claim" --timeout=180s >/dev/null
    sandbox="$(kc get sandboxclaim "$claim" -o jsonpath='{.status.sandbox.name}')"
    pod="$(kc get sandbox "$sandbox" -o jsonpath='{.metadata.annotations.agents\.x-k8s\.io/pod-name}')"
    pod="${pod:-$sandbox}"
    say "claimed $claim ($pod)"
    printf '%s\n' "$pod"
    ;;
  put)
    dir="\"\$HOME\"/$(sq "$4")"
    kc exec -i "$3" -- sh -c "mkdir -p $dir && $untar -C $dir"
    ;;
  run)
    pod="$3"
    shift 3
    kc exec -i "$pod" -- "$@"
    ;;
  get)
    kc exec "$3" -- sh -c "tar -cf - -C \"\$HOME\"/$(sq "$4") ."
    ;;
  release)
    if [ -n "${REMOTE_KEEP:-}" ]; then
      say "kept sandboxclaim/$2"
    else
      kc delete sandboxclaim "$2" --wait=false --ignore-not-found >/dev/null || true
    fi
    ;;
  *)
    echo "usage: cluster.sh acquire|put|run|get|release <run> [<pod> ...]" >&2
    exit 1
    ;;
esac
