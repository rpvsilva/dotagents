#!/usr/bin/env bash
# Passes as build-epic's own report_check.py judges an implementer: the final answer is the 4-line block with
# status done, and the report file the prompt names is not empty. The brief also asks for the block at the end of
# the report file, but build-epic acts on the final answer's block, so that copy is not checked here.
set -euo pipefail
id=02-01
answer="$(python3 -I - "$EVAL_CHECKOUT/evals/_runner" "$(dirname "$EVAL_TRANSCRIPT")" <<'PY'
import importlib, sys
sys.path.insert(0, sys.argv[1])
for tool in ("claude", "codex", "cursor"):  # the run's tool is not given to checks; only its own adapter reads it
    try:
        text = importlib.import_module("adapters." + tool).answer(sys.argv[2])
    except Exception:
        continue
    if isinstance(text, str) and text.strip():
        sys.stdout.write(text)
        break
PY
)"
[ -n "$answer" ]
out="$(printf '%s\n' "$answer" | python3 -I "$EVAL_CHECKOUT/skills/build-epic/scripts/report_check.py" "$EVAL_SCRATCH" "$id")"
[ "$out" = "OK $id done" ]
