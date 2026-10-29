#!/usr/bin/env bash
# Cluster backend for scripts/remote-agent.sh: a warm sandbox from agent-sandbox on Kubernetes, claimed with a
# SandboxClaim and driven with `kubectl exec`.
# Usage: cluster.sh acquire <run> | put <run> <pod> <dir> | run <run> <pod> <argv>... | get <run> <pod> <dir> | release <run>
# acquire claims a sandbox and prints its pod; its shutdownTime deletes it after REMOTE_HOURS if release never comes.
# put extracts the tar on stdin under the sandbox's $HOME/<dir>; get writes a tar of $HOME/<dir> on stdout.
# release deletes the claim (safe when acquire failed halfway), or says it kept it under REMOTE_KEEP.
# Settings: REMOTE_CONTEXT (kube context, default maxibombo), REMOTE_NAMESPACE (agents), REMOTE_POOL (claude-runner),
# REMOTE_HOURS (2), REMOTE_KEEP, REMOTE_CLAIM_TIMEOUT (seconds acquire waits for the claim to be Ready, 180);
# REMOTE_TASK (set by remote-agent.sh from --name, a valid label value).
# The claim and its pod are labelled sandbox.users.io/remote-agent-run=<run> and, with REMOTE_TASK,
# sandbox.users.io/remote-agent-task=<task>, so `kubectl get pods -L sandbox.users.io/remote-agent-task` tells the
# runs apart. agent-sandbox rejects pod labels outside its allowed domains (default: sandbox.users.io).
set -euo pipefail

# shellcheck source=lib.sh disable=SC1091
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

context="${REMOTE_CONTEXT:-maxibombo}"
namespace="${REMOTE_NAMESPACE:-agents}"
pool="${REMOTE_POOL:-claude-runner}"
hours="${REMOTE_HOURS:-2}"
claim_timeout="${REMOTE_CLAIM_TIMEOUT:-180}"

kc() { kubectl --context "$context" -n "$namespace" "$@"; }

# GNU tar in the pod would warn about the xattr headers macOS tar adds.
untar='tar -x --no-same-owner --warning=no-unknown-keyword -f -'

op="${1:-}"
case "$op" in
  acquire)
    claim="$2"
    labels="sandbox.users.io/remote-agent-run: $claim"
    [ -z "${REMOTE_TASK:-}" ] || labels="$labels, sandbox.users.io/remote-agent-task: $REMOTE_TASK"
    end=$(( $(date +%s) + hours * 3600 ))
    # GNU date takes -d @epoch, BSD date -r epoch.
    shutdown="$(date -u -d "@$end" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -r "$end" +%Y-%m-%dT%H:%M:%SZ)"
    kc apply -f - >/dev/null <<YAML
apiVersion: extensions.agents.x-k8s.io/v1beta1
kind: SandboxClaim
metadata:
  name: $claim
  labels: {$labels}
spec:
  additionalPodMetadata:
    labels: {$labels}
  warmPoolRef:
    name: $pool
  lifecycle:
    shutdownTime: "$shutdown"
    shutdownPolicy: Delete
YAML
    # Wait for Ready, but stop at once on a failure the controller does not get past by itself (e.g.
    # InvalidMetadata); the reasons skipped below are the ones agent-sandbox reports while it is still working.
    give_up=$((SECONDS + claim_timeout))
    while :; do
      conditions="$(kc get sandboxclaim "$claim" \
        -o jsonpath='{range .status.conditions[*]}{.type}{"\t"}{.status}{"\t"}{.reason}{"\t"}{.message}{"\n"}{end}' || true)"
      [ "$(printf '%s\n' "$conditions" | awk -F '\t' '$1 == "Ready" { print $2 }')" != True ] || break
      failed="$(printf '%s\n' "$conditions" | awk -F '\t' '$2 == "False" && ($3 != "" || $4 != "") &&
        $3 !~ /^(SandboxNotReady|SandboxCreatePending|SandboxMissing|AdoptionConflict|DependenciesNotReady|ReconcilerError)$/ {
          print $1 " " $3 ": " $4 }')"
      if [ -n "$failed" ]; then
        say "sandboxclaim/$claim failed:"
        printf '%s\n' "$failed" | sed 's/^/  /' >&2
        exit 1
      fi
      if [ "$SECONDS" -ge "$give_up" ]; then
        say "sandboxclaim/$claim not Ready after ${claim_timeout}s; its conditions:"
        if [ -z "$conditions" ]; then echo "  (none)" >&2; fi
        printf '%s\n' "$conditions" | awk -F '\t' 'NF { print "  " $1 "=" $2 " " $3 ": " $4 }' >&2
        exit 1
      fi
      sleep 2
    done
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
