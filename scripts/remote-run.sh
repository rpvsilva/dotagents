#!/usr/bin/env bash
# Runs one coding task on a remote sandbox (agent-sandbox on Kubernetes) and opens a PR as the GitHub App.
# Usage: scripts/remote-run.sh <owner/repo> <prompt | @prompt-file | ->   ("-" reads the prompt from stdin)
# Claims a warm runner, sends the tokens through stdin on `kubectl exec` (never as arguments or claim env),
# then scripts/remote-run-sandbox.sh installs this setup there, lets Claude work on a branch and opens the PR.
# The claim is deleted on exit; its shutdownTime deletes it anyway if this script dies first.
# Config: CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`) from the environment or ~/.config/dotagents/remote.env;
# the GitHub App as in scripts/gh-app-token.sh, installed on both the target repo and the setup repo.
# Overrides: REMOTE_CONTEXT (kube context, default maxibombo), REMOTE_NAMESPACE (agents), REMOTE_POOL (claude-runner),
# REMOTE_SETUP_REPO (rpvsilva/dotagents), REMOTE_APP_SLUG (rpvsilva-agent), REMOTE_HOURS (2), REMOTE_KEEP=1 (keep the claim).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/dotagents"
# shellcheck disable=SC1091
[ -f "$CONFIG/remote.env" ] && . "$CONFIG/remote.env"

usage() { echo "usage: $0 <owner/repo> <prompt | @prompt-file | ->" >&2; exit 1; }
[ $# -eq 2 ] || usage
target="$1"
[[ "$target" == */* ]] || usage
case "$2" in
  -) prompt="$(cat)" ;;
  @*) prompt="$(cat "${2#@}")" ;;
  *) prompt="$2" ;;
esac
[ -n "$prompt" ] || { echo "remote-run: empty prompt" >&2; exit 1; }
claude_token="${CLAUDE_CODE_OAUTH_TOKEN:?set CLAUDE_CODE_OAUTH_TOKEN (in $CONFIG/remote.env)}"

context="${REMOTE_CONTEXT:-maxibombo}"
namespace="${REMOTE_NAMESPACE:-agents}"
pool="${REMOTE_POOL:-claude-runner}"
setup="${REMOTE_SETUP_REPO:-rpvsilva/dotagents}"
app="${REMOTE_APP_SLUG:-rpvsilva-agent}"
hours="${REMOTE_HOURS:-2}"
token_cmd="${REMOTE_TOKEN_CMD:-$REPO/scripts/gh-app-token.sh}"

# One token per repo: gh-app-token.sh narrows each to a single repo.
target_token="$("$token_cmd" "$target")"
setup_token="$("$token_cmd" "$setup")"

kc() { kubectl --context "$context" -n "$namespace" "$@"; }

claim="run-$(date +%Y%m%d-%H%M%S)-$RANDOM"
branch="remote/$claim"
end=$(( $(date +%s) + hours * 3600 ))
# GNU date takes -d @epoch, BSD date -r epoch.
shutdown="$(date -u -d "@$end" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -r "$end" +%Y-%m-%dT%H:%M:%SZ)"

cleanup() {
  if [ -n "${REMOTE_KEEP:-}" ]; then
    echo "remote-run: kept sandboxclaim/$claim (deleted by $shutdown)" >&2
  else
    kc delete sandboxclaim "$claim" --wait=false --ignore-not-found >/dev/null || true
  fi
}
trap cleanup EXIT

kc apply -f - >/dev/null <<EOF
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
EOF
echo "remote-run: claimed $claim, waiting for its sandbox" >&2
kc wait --for=condition=Ready "sandboxclaim/$claim" --timeout=180s >/dev/null

sandbox="$(kc get sandboxclaim "$claim" -o jsonpath='{.status.sandbox.name}')"
pod="$(kc get sandbox "$sandbox" -o jsonpath='{.metadata.annotations.agents\.x-k8s\.io/pod-name}')"
pod="${pod:-$sandbox}"
echo "remote-run: running in $pod on branch $branch of $target" >&2

{ printf '%s\n%s\n%s\n' "$claude_token" "$target_token" "$setup_token"; printf '%s' "$prompt"; } \
  | kc exec -i "$pod" -- bash -c "$(cat "$REPO/scripts/remote-run-sandbox.sh")" remote-run-sandbox \
      "$target" "$setup" "$app" "$branch"
