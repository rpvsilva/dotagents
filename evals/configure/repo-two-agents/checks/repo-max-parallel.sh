#!/usr/bin/env bash
# Passes when the repo Level file sets the shared max_parallel to 2.
set -euo pipefail
repo="$EVAL_WORK/.agents/settings.json"
[ "$(jq -r '.max_parallel' "$repo")" = 2 ]
