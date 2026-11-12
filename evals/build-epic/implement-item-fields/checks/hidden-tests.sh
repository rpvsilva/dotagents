#!/usr/bin/env bash
# Passes when the hidden acceptance tests (hidden/accept_*.py, never shown to the run) pass against the work repo:
# they drive its `todo` CLI, and the public functions the task names, from outside.
set -euo pipefail
hidden="$(cd "$(dirname "$0")" && pwd)/hidden"
cd "$EVAL_WORK"
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s "$hidden" -t "$hidden" -p 'accept_*.py' </dev/null
