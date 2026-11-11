#!/usr/bin/env bash
# Passes when the reviewer left the repo untouched: no change, staged or not, and no new file.
set -euo pipefail
status="$(git -C "$EVAL_WORK" status --porcelain --untracked-files=all)"
[ -z "$status" ]
