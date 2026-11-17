"""Shared by this case's checks, not a check itself (not executable).

  python3 -I _cites.py <path> <first line> <last line> <regex>

Exits 0 when a finding in the reviewer's output (`report.json`, its `findings`) cites <path> (or a path ending
`/<path>`) at a line in [first, last] and <regex> matches, case-insensitively, its claim, scenario or rule: the finding
names the right place and the right reason.
"""
import json
import os
import re
import sys
from pathlib import Path

path, first, last, reason = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
report = Path(os.environ["EVAL_SCRATCH"]) / "report.json"
try:
    findings = json.loads(report.read_text(errors="replace")).get("findings") or []
except (OSError, ValueError, AttributeError):
    sys.exit(1)
why = re.compile(reason, re.IGNORECASE)
for f in findings:
    cited = f.get("path") if isinstance(f, dict) else None
    if not isinstance(cited, str) or not (cited == path or cited.endswith("/" + path)):
        continue
    if not isinstance(f.get("line"), int):
        continue
    text = " ".join(str(f.get(k, "")) for k in ("claim", "scenario", "rule"))
    if first <= f["line"] <= last and why.search(text):
        sys.exit(0)
sys.exit(1)
