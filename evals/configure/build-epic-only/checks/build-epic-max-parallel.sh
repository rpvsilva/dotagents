#!/usr/bin/env bash
# Passes when the repo Level file sets build-epic's own max_parallel to 1.
set -euo pipefail
repo="$EVAL_WORK/.agents/settings.json"
[ "$(jq -r '."build-epic".max_parallel' "$repo")" = 1 ]
