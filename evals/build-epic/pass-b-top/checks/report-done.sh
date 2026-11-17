#!/usr/bin/env bash
# Passes when the planner's output, plan-B.json, is its role's JSON (skills/_shared/protocol) with status done.
set -euo pipefail
out="$(python3 -I "$EVAL_CHECKOUT/skills/_shared/protocol/check.py" planner output "$EVAL_SCRATCH/reports/plan-B.json")"
[ "$out" = "OK planner done" ]
