#!/usr/bin/env bash
# Passes when the report file is a valid reviewer output, by the protocol's own check, with status done.
set -euo pipefail
[ "$(python3 -I "$EVAL_CHECKOUT/skills/_shared/protocol/check.py" reviewer output "$EVAL_SCRATCH/report.json")" = "OK reviewer done" ]
