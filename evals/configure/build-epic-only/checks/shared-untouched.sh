#!/usr/bin/env bash
# Passes when the shared max_parallel the case seeds at the repo Level is still 3.
set -euo pipefail
repo="$EVAL_WORK/.agents/settings.json"
[ "$(jq -r '.max_parallel' "$repo")" = 3 ]
