#!/usr/bin/env bash
# Runs one subagent task on a remote sandbox (agent-sandbox on Kubernetes) for compute only, and applies its
# changes to a local worktree. The cluster never touches a repo: it gets this machine's setup and a snapshot of
# the worktree, and only a patch comes back.
# Usage: scripts/remote-agent.sh <brief | @brief-file | -> [worktree]   (default worktree: the current one)
# Steps: stage the setup (scripts/remote-agent-pack.sh); list the worktree's tracked and unignored files, minus
# secret-looking ones; claim a warm sandbox; stream both in with `kubectl exec` and the Claude token through stdin;
# run scripts/remote-agent-sandbox.sh there; bring back the patch and apply it with `git apply`, uncommitted.
# The claim is deleted on exit; its shutdownTime deletes it anyway if this script dies first.
# Prints Claude's progress and summary. Keeps the patch and summary in <git dir>/remote-agent/<claim>/.
# Exit 3 when Claude changed nothing; exit 4 when the patch does not apply (it is kept for a manual look).
# Config: CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`) from the environment or ~/.config/dotagents/remote.env.
# Overrides: REMOTE_CONTEXT (kube context, default my-cluster), REMOTE_NAMESPACE (agents), REMOTE_POOL (claude-runner),
# REMOTE_HOURS (2), REMOTE_MAX_MB (200, snapshot size cap), REMOTE_KEEP=1 (keep the claim).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/dotagents"
# shellcheck source=hooks/lib.sh disable=SC1091
. "$REPO/scripts/hooks/lib.sh"
# shellcheck disable=SC1091
[ -f "$CONFIG/remote.env" ] && . "$CONFIG/remote.env"

usage() { echo "usage: $0 <brief | @brief-file | -> [worktree]" >&2; exit 1; }
if [ $# -lt 1 ] || [ $# -gt 2 ]; then usage; fi
case "$1" in
  -) brief="$(cat)" ;;
  @*) brief="$(cat "${1#@}")" ;;
  *) brief="$1" ;;
esac
[ -n "$brief" ] || { echo "remote-agent: empty brief" >&2; exit 1; }
root="$(git -C "${2:-$PWD}" rev-parse --show-toplevel 2>/dev/null)" || { echo "remote-agent: not in a git worktree: ${2:-$PWD}" >&2; exit 1; }
claude_token="${CLAUDE_CODE_OAUTH_TOKEN:?set CLAUDE_CODE_OAUTH_TOKEN (in $CONFIG/remote.env)}"

context="${REMOTE_CONTEXT:-my-cluster}"
namespace="${REMOTE_NAMESPACE:-agents}"
pool="${REMOTE_POOL:-claude-runner}"
hours="${REMOTE_HOURS:-2}"
max_mb="${REMOTE_MAX_MB:-200}"

kc() { kubectl --context "$context" -n "$namespace" "$@"; }
say() { echo "remote-agent: $*" >&2; }

stage="$(mktemp -d)"
claim="agent-$(date +%Y%m%d-%H%M%S)-$RANDOM"
claimed=""
cleanup() {
  if [ -n "$claimed" ]; then
    if [ -n "${REMOTE_KEEP:-}" ]; then
      say "kept sandboxclaim/$claim"
    else
      kc delete sandboxclaim "$claim" --wait=false --ignore-not-found >/dev/null || true
    fi
  fi
  rm -rf "$stage"
}
trap cleanup EXIT

# The setup and the snapshot are built before anything reaches the cluster.
"$REPO/scripts/remote-agent-pack.sh" "$stage/home"
: >"$stage/files"
while IFS= read -r -d '' f; do
  [ -f "$root/$f" ] || continue # tracked but deleted locally
  if is_secret "$f"; then say "left out $f (looks like a secret)"; continue; fi
  printf '%s\0' "$f" >>"$stage/files"
done < <(git -C "$root" ls-files -co --exclude-standard -z)
count="$(tr -cd '\0' <"$stage/files" | wc -c | tr -d ' ')"
kb=0
# Guarded: with no input, GNU xargs would still run `du` and measure the whole directory.
[ "$count" -eq 0 ] || kb="$(cd "$root" && xargs -0 du -k <"$stage/files" 2>/dev/null | awk '{s += $1} END {print s + 0}')"
[ "$kb" -le $((max_mb * 1024)) ] || { say "snapshot is $((kb / 1024)) MB, over REMOTE_MAX_MB=$max_mb"; exit 1; }
say "snapshot: $count files, $((kb / 1024)) MB from $root"

end=$(( $(date +%s) + hours * 3600 ))
# GNU date takes -d @epoch, BSD date -r epoch.
shutdown="$(date -u -d "@$end" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -r "$end" +%Y-%m-%dT%H:%M:%SZ)"
claimed=1
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
kc wait --for=condition=Ready "sandboxclaim/$claim" --timeout=180s >/dev/null
sandbox="$(kc get sandboxclaim "$claim" -o jsonpath='{.status.sandbox.name}')"
pod="$(kc get sandbox "$sandbox" -o jsonpath='{.metadata.annotations.agents\.x-k8s\.io/pod-name}')"
pod="${pod:-$sandbox}"
say "claimed $claim ($pod)"

# macOS tar would add AppleDouble files and xattr headers; GNU tar in the pod would warn about them.
export COPYFILE_DISABLE=1
untar='tar -x --no-same-owner --warning=no-unknown-keyword -f -'
tar -C "$stage/home" -chf - . | kc exec -i "$pod" -- sh -c "$untar -C \$HOME"
(cd "$root" && tar -cf - --null -T "$stage/files") | kc exec -i "$pod" -- sh -c "mkdir -p \$HOME/work && $untar -C \$HOME/work"
say "sent setup and snapshot"

status=0
{ printf '%s\n' "$claude_token"; printf '%s' "$brief"; } \
  | kc exec -i "$pod" -- bash -c "$(cat "$REPO/scripts/remote-agent-sandbox.sh")" remote-agent-sandbox || status=$?

keep="$(git -C "$root" rev-parse --absolute-git-dir)/remote-agent/$claim"
mkdir -p "$keep"
kc exec "$pod" -- sh -c "tar -cf - -C \$HOME/out ." | tar -xf - -C "$keep" 2>/dev/null || true
[ "$status" -eq 0 ] || { say "the sandbox run failed (exit $status); what it left is in $keep"; exit "$status"; }

[ -f "$keep/summary.md" ] && cat "$keep/summary.md"
if [ ! -s "$keep/changes.patch" ]; then
  say "no changes"
  exit 3
fi
if ! git -C "$root" apply --whitespace=nowarn "$keep/changes.patch"; then
  say "the patch does not apply to $root (did the worktree change meanwhile?); it is in $keep/changes.patch"
  exit 4
fi
say "applied to $root (uncommitted): $(git -C "$root" apply --stat "$keep/changes.patch" | tail -1)"
