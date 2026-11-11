#!/usr/bin/env bash
# Passes when plan_check.py prints `plan ok` and the plan holds at least one ticket and one task.
set -euo pipefail
ls "$EVAL_SCRATCH"/tickets/*.md >/dev/null 2>&1
ls "$EVAL_SCRATCH"/tasks/*.md >/dev/null 2>&1
out="$(python3 "$EVAL_CHECKOUT/skills/build-epic/plan_check.py" "$EVAL_SCRATCH")"
case "$out" in "plan ok"*) ;; *) exit 1 ;; esac
