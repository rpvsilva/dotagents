#!/usr/bin/env bash
# Runs one subagent task on a remote sandbox for compute only, and applies its changes to a local worktree. The
# sandbox comes from a backend (scripts/remote-backends/<name>.sh; the cluster by default) and never touches a
# repo: it gets this machine's setup and a snapshot of the worktree, and only a patch comes back.
# Usage: remote-agent [--attach PATH]... <brief | @brief-file | -> [worktree]   (default worktree: the current one)
# install.sh links it as ~/.agents/bin/remote-agent. --attach copies a file or folder the brief needs from outside
# the worktree (a ticket, a run folder) to ~/attached/<its name> in the sandbox, read-only: only the worktree's
# changes come back. Secret-looking files inside it are left out.
# Steps: stage the setup (scripts/remote-agent-pack.sh); list the worktree's tracked and unignored files, minus
# secret-looking ones; acquire a sandbox from the backend; put both in and the Claude token through stdin;
# run scripts/remote-agent-sandbox.sh there; get the patch back and apply it with `git apply`, uncommitted.
# The sandbox is released on exit, also on INT or TERM; the backend's time limit (REMOTE_HOURS) removes it anyway
# if this script dies first.
# Prints Claude's progress and summary. Keeps the patch and summary in <git dir>/remote-agent/<run>/.
# Exit 3 when Claude changed nothing; exit 4 when the patch does not apply (it is kept for a manual look).
# Config: CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`) from the environment or ~/.config/dotagents/remote.env.
# Overrides: REMOTE_BACKEND (cluster), REMOTE_HOURS (2), REMOTE_MAX_MB (200, snapshot size cap), REMOTE_KEEP=1
# (keep the sandbox), and the backend's own settings (see its script; cluster: REMOTE_CONTEXT, REMOTE_NAMESPACE,
# REMOTE_POOL). Every REMOTE_* variable is exported to the backend; the Claude token is not.
set -euo pipefail

# Follow the link from ~/.agents/bin to this repo, where the sibling scripts live.
src="${BASH_SOURCE[0]}"
while [ -L "$src" ]; do
  dir="$(cd "$(dirname "$src")" && pwd)"
  src="$(readlink "$src")"
  [[ "$src" == /* ]] || src="$dir/$src"
done
REPO="$(cd "$(dirname "$src")/.." && pwd)"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/dotagents"
# shellcheck source=hooks/lib.sh disable=SC1091
. "$REPO/scripts/hooks/lib.sh"
# shellcheck disable=SC1091
[ -f "$CONFIG/remote.env" ] && . "$CONFIG/remote.env"

usage() { echo "usage: remote-agent [--attach PATH]... <brief | @brief-file | -> [worktree]" >&2; exit 1; }
attach=()
while [ $# -gt 0 ] && [ "$1" = --attach ]; do
  if [ $# -lt 2 ] || [ ! -e "$2" ]; then echo "remote-agent: --attach needs an existing path: ${2:-}" >&2; exit 1; fi
  attach+=("$2")
  shift 2
done
if [ $# -lt 1 ] || [ $# -gt 2 ]; then usage; fi
case "$1" in
  -) brief="$(cat)" ;;
  @*) brief="$(cat "${1#@}")" ;;
  *) brief="$1" ;;
esac
[ -n "$brief" ] || { echo "remote-agent: empty brief" >&2; exit 1; }
root="$(git -C "${2:-$PWD}" rev-parse --show-toplevel 2>/dev/null)" || { echo "remote-agent: not in a git worktree: ${2:-$PWD}" >&2; exit 1; }
claude_token="${CLAUDE_CODE_OAUTH_TOKEN:?set CLAUDE_CODE_OAUTH_TOKEN (in $CONFIG/remote.env)}"

backend_name="${REMOTE_BACKEND:-cluster}"
known=()
for f in "$REPO"/scripts/remote-backends/*.sh; do
  [ "$(basename "$f")" = lib.sh ] || known+=("$(basename "$f" .sh)")
done
backend="$REPO/scripts/remote-backends/$backend_name.sh"
if [ "$backend_name" = lib ] || [[ "$backend_name" == */* ]] || [ ! -f "$backend" ]; then
  echo "remote-agent: unknown REMOTE_BACKEND=$backend_name (known: ${known[*]})" >&2
  exit 1
fi
# The backend reads its settings from REMOTE_* variables, sourced or not.
while IFS= read -r v; do export "${v?}"; done < <(compgen -v REMOTE_)
max_mb="${REMOTE_MAX_MB:-200}"

say() { echo "remote-agent: $*" >&2; }

stage="$(mktemp -d)"
run="agent-$(date +%Y%m%d-%H%M%S)-$RANDOM"
acquired=""
cleanup() {
  [ -z "$acquired" ] || "$backend" release "$run" || true
  rm -rf "$stage"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# The setup, the attachments and the snapshot are built before anything reaches the backend.
"$REPO/scripts/remote-agent-pack.sh" "$stage/home"
for a in "${attach[@]+"${attach[@]}"}"; do
  name="$(basename "$a")"
  [ ! -e "$stage/home/attached/$name" ] || { say "two attachments named $name"; exit 1; }
  mkdir -p "$stage/home/attached"
  cp -RL "$a" "$stage/home/attached/$name"
  while IFS= read -r -d '' f; do
    if is_secret "${f#"$stage/home/attached/"}"; then rm -f "$f"; say "left out attached/${f#"$stage/home/attached/"} (looks like a secret)"; fi
  done < <(find "$stage/home/attached/$name" -type f -print0)
  say "attached $a as ~/attached/$name"
done
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

acquired=1
handle="$("$backend" acquire "$run")"

# macOS tar would add AppleDouble files and xattr headers.
export COPYFILE_DISABLE=1
tar -C "$stage/home" -chf - . | "$backend" put "$run" "$handle" .
(cd "$root" && tar -cf - --null -T "$stage/files") | "$backend" put "$run" "$handle" work
say "sent setup and snapshot"

status=0
{ printf '%s\n' "$claude_token"; printf '%s' "$brief"; } \
  | "$backend" run "$run" "$handle" bash -c "$(cat "$REPO/scripts/remote-agent-sandbox.sh")" remote-agent-sandbox || status=$?

keep="$(git -C "$root" rev-parse --absolute-git-dir)/remote-agent/$run"
mkdir -p "$keep"
"$backend" get "$run" "$handle" out | tar -xf - -C "$keep" 2>/dev/null || true
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
