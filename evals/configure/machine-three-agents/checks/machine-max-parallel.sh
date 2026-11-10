#!/usr/bin/env bash
# Passes when the machine Level file sets the shared max_parallel to 3.
set -euo pipefail
machine="$EVAL_HOME/.config/dotagents/settings.json"
[ "$(jq -r '.max_parallel' "$machine")" = 3 ]
