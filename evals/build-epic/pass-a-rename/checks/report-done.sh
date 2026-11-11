#!/usr/bin/env bash
# Passes when the pass A report ends with the block whose status is done.
set -euo pipefail
report="$EVAL_SCRATCH/reports/plan-A.md"
[ -f "$report" ]
[ "$(tail -n 8 "$report" | grep -E '^status:' | tail -n 1 | tr -d '[:space:]`')" = "status:done" ]
