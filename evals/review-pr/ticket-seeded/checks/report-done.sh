#!/usr/bin/env bash
# Passes when the report file ends with the block whose status is done.
set -euo pipefail
report="$EVAL_SCRATCH/report.md"
[ -f "$report" ]
[ "$(tail -n 8 "$report" | grep -E '^status:' | tail -n 1 | tr -d '[:space:]`')" = "status:done" ]
