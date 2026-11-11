#!/usr/bin/env bash
# Passes when the session reviewed itself and started no subagent, so the case's model is the reviewer's model.
set -euo pipefail
python3 -I - "$EVAL_TRANSCRIPT" <<'PY'
import json, sys
for line in open(sys.argv[1], errors="replace"):
    try:
        event = json.loads(line)
    except ValueError:
        continue
    content = (event.get("message") or {}).get("content") if isinstance(event, dict) else None
    for part in content if isinstance(content, list) else []:
        if isinstance(part, dict) and part.get("type") == "tool_use" and part.get("name") in ("Task", "Agent"):
            sys.exit(1)
PY
