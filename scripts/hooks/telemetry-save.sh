#!/usr/bin/env bash
# Saves a Claude Code session's whole run as ~/.agents/telemetry/<session id>.json when the session ends (SessionEnd
# hook), unless the telemetry Setting auto_save is false. A session that ends again overwrites its file.
# Input: the hook's JSON on stdin, with .transcript_path (the session's .jsonl), .session_id and .cwd.
# The file is named after .session_id when it is a safe name (only A-Z a-z 0-9 . _ -, no ".."), else after the
# transcript's name: a native Windows path (C:\...\<id>.jsonl) may not split on "/". Neither safe: nothing is saved.
# The save runs detached in the background, so the hook returns at once: session-end hooks get little time.
# Each save writes its own temp file beside the target (two ends of one session never share one) and moves it into
# place only when the extractor exits 0 with output; otherwise it removes it.
# Always exits 0 and prints nothing: any failure (no jq or python3, no extractor, as in a remote sandbox where this
# script is copied without the repo, nothing to measure, an unwritable folder) saves nothing, silently.
# TELEMETRY_EXTRACT overrides the extractor's path (for tests).

input="$(cat)"
transcript="$(jq -r '.transcript_path // empty' <<<"$input" 2>/dev/null)" || exit 0
session_id="$(jq -r '.session_id | strings' <<<"$input" 2>/dev/null)" || exit 0
cwd="$(jq -r '.cwd // empty' <<<"$input" 2>/dev/null)" || exit 0
[ -f "$transcript" ] || exit 0
cwd="${cwd:-$PWD}"

# On unless the Setting says false; a missing settings command or a bad settings file keeps the default.
[ "$("$HOME/.agents/bin/settings" --dir "$cwd" get telemetry auto_save 2>/dev/null)" != false ] || exit 0

command -v python3 >/dev/null 2>&1 || exit 0
# install.sh links this file into ~/.agents/hooks (older installs linked the whole folder); the extractor sits in the
# repo beside scripts/hooks. One readlink finds the repo file (readlink -f is missing on older macOS); pwd -P then
# resolves a folder link.
self="$0"
if [ -L "$self" ]; then
  link="$(readlink "$self")" || exit 0
  case "$link" in /*) self="$link" ;; *) self="$(dirname "$self")/$link" ;; esac
fi
here="$(CDPATH='' cd "$(dirname "$self")" 2>/dev/null && pwd -P)" || exit 0
extract="${TELEMETRY_EXTRACT:-$here/../../adapters/claude/telemetry/extract.py}"
[ -f "$extract" ] || exit 0

dir="$HOME/.agents/telemetry"
# A name that stays inside $dir: only A-Z a-z 0-9 . _ -, not empty, no "..".
safe() { case "$1" in '' | *..* | *[!A-Za-z0-9._-]*) return 1 ;; esac; }
id="$session_id"
safe "$id" || id="$(basename "$transcript" .jsonl)"
safe "$id" || exit 0
# shellcheck disable=SC2016 # the script is run by the inner bash, with its values as arguments
nohup bash -c '
  dir="$1" id="$2" extract="$3" transcript="$4" cwd="$5"
  mkdir -p "$dir"
  tmp="$(mktemp "$dir/.$id.XXXXXX")" || exit 0
  if python3 "$extract" --whole --session "$transcript" --cwd "$cwd" >"$tmp" && [ -s "$tmp" ]; then
    mv -f "$tmp" "$dir/$id.json"
  else
    rm -f "$tmp"
  fi
' telemetry-save "$dir" "$id" "$extract" "$transcript" "$cwd" </dev/null >/dev/null 2>&1 &
exit 0
