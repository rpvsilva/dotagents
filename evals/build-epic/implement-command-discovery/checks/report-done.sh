#!/usr/bin/env bash
# Passes when the report file the prompt names ends with the brief's block, status done.
set -euo pipefail
report="$EVAL_SCRATCH/reports/01-01.md"
[ -f "$report" ]
[ "$(tail -n 8 "$report" | grep -E '^status:' | tail -n 1 | tr -d '[:space:]`')" = "status:done" ]
