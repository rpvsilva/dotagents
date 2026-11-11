"""Shared by this case's checks, not a check itself (not executable).

  python3 -I _cites.py <path> <first line> <last line> <regex>

Exits 0 when the report cites <path>:<n> (or a range starting at <n>) with n in [first, last], and <regex> matches,
case-insensitively, within 6 lines of that cite: the finding names the right place and the right reason.
"""
import os
import re
import sys
from pathlib import Path

path, first, last, reason = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
report = Path(os.environ["EVAL_SCRATCH"]) / "report.md"
if not report.is_file():
    sys.exit(1)
lines = report.read_text(errors="replace").splitlines()
cite = re.compile(r"(?<![\w/.])" + re.escape(path) + r":(\d+)")
why = re.compile(reason, re.IGNORECASE)
for i, line in enumerate(lines):
    if any(first <= int(n) <= last for n in cite.findall(line)):
        if why.search("\n".join(lines[max(0, i - 6):i + 7])):
            sys.exit(0)
sys.exit(1)
