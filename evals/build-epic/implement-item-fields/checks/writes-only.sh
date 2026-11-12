#!/usr/bin/env bash
# Passes when every file the implementer changed, committed or not, is on the task's `Writes:` line or is a test
# file under tests/ (`*_test.py` or `test_*.py`): other tasks build beside it on every other file.
set -euo pipefail
tasks=("$EVAL_INPUT"/tasks/*.md)
[ "${#tasks[@]}" -eq 1 ] && [ -f "${tasks[0]}" ]
python3 -I - "$EVAL_WORK" "${tasks[0]}" <<'PY'
import re
import subprocess
import sys
from pathlib import PurePosixPath

work, task = sys.argv[1:]
line = next(line for line in open(task) if line.startswith("Writes:"))
writes = {w.strip() for w in line.split(":", 1)[1].split(",") if w.strip()}


def git(*args):
    return subprocess.run(["git", "-C", work, *args], check=True, capture_output=True, text=True).stdout.splitlines()


def test_file(path):
    p = PurePosixPath(path)
    return p.parts[0] == "tests" and re.fullmatch(r"test_.*\.py|.*_test\.py", p.name) is not None


root = git("rev-list", "--max-parents=0", "HEAD")[-1]
changed = set(git("diff", "--name-only", root)) | set(git("ls-files", "--others", "--exclude-standard"))
outside = sorted(p for p in changed if p not in writes and not test_file(p))
for p in outside:
    print(f"writes-only: {p} is outside the task's Writes", file=sys.stderr)
sys.exit(1 if outside else 0)
PY
