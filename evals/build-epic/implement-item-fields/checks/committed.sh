#!/usr/bin/env bash
# Passes when the work ends as commits, as the brief says: HEAD is past the fixture commit and nothing is left
# uncommitted (changed, staged, or untracked and not ignored).
set -euo pipefail
root="$(git -C "$EVAL_WORK" rev-list --max-parents=0 HEAD | tail -n 1)"
[ "$(git -C "$EVAL_WORK" rev-parse HEAD)" != "$root" ]
[ -z "$(git -C "$EVAL_WORK" status --porcelain --untracked-files=all)" ]
