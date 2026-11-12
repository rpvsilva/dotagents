#!/usr/bin/env bash
# Passes when the repo's own suite, with the implementer's new tests in it, is green: AGENTS.md's check command.
# Needs a python3 on PATH that imports pytest (the run has no network to install it); fails saying so otherwise.
set -euo pipefail
cd "$EVAL_WORK"
if ! python3 -c 'import pytest' 2>/dev/null; then
  echo "suite: the python3 on PATH has no pytest; run the evals with a venv holding it first on PATH" >&2
  exit 1
fi
PYTHONDONTWRITEBYTECODE=1 python3 -I -m pytest -q -c pyproject.toml -p no:cacheprovider </dev/null
