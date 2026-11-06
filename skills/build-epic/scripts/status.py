#!/usr/bin/env python3
"""Shows a run at a glance: one line per ticket, each followed by its tasks, read from the run folder only.

Usage: python3 -I status.py <run>

Each line is `<id> <status> <PR> <blocked by> <title>`, columns aligned, tasks indented under their ticket; `-` marks
no PR or no blocker. The one step that prints several stdout lines. Exits 0, or prints
`FAIL run no-run-folder: <path>` and exits 1.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import ci, core, runfolder  # noqa: E402


def task_id(path):
    """`TT-NN` for `TT-NN-<slug>.md`, the whole stem for orchestrator tasks (`TT-tests`)."""
    parts = path.stem.split("-", 2)
    return f"{parts[0]}-{parts[1]}" if len(parts) > 1 and parts[1].isdigit() else path.stem


def title(text):
    for line in text.splitlines():
        if line.startswith("# "):
            return re.sub(r"^\d+(-[A-Za-z0-9-]+)?:\s*", "", line[2:].strip())
    return ""


def row(ident, path):
    text = path.read_text()
    pr = ci.pr_number(runfolder.header(text, "PR"))
    blocked = (runfolder.header(text, "Blocked by") or "").strip()
    if not blocked or blocked.lower().startswith("none"):
        blocked = "-"
    blocked = re.sub(r"\s+", "-", re.sub(r"\s*,\s*", ",", blocked))
    return [ident, runfolder.header(text, "Status") or "?", f"#{pr}" if pr else "-", blocked, title(text)]


def table(run):
    tickets = {runfolder.norm(f.stem.split("-", 1)[0]): f for f in sorted((run / "tickets").glob("*.md"))}
    tasks = {}
    for f in sorted((run / "tasks").glob("*.md")):
        tasks.setdefault(runfolder.norm(f.stem.split("-", 1)[0]), []).append(f)
    rows = []
    for tt in sorted(set(tickets) | set(tasks), key=lambda n: (not n.isdigit(), int(n) if n.isdigit() else 0, n)):
        if tt in tickets:
            rows.append(row(tickets[tt].stem.split("-", 1)[0], tickets[tt]))
        rows += [["  " + r[0]] + r[1:] for r in (row(task_id(f), f) for f in tasks.get(tt, ()))]
    widths = [max((len(r[i]) for r in rows), default=0) for i in range(4)]
    return ["  ".join(c.ljust(w) for c, w in zip(r, widths)) + "  " + r[4] for r in rows]


def main(argv):
    if len(argv) != 1:
        print("FAIL run usage: status.py <run>")
        return 2
    run = core.resolve(argv[0])
    if not run.is_dir():
        print(f"FAIL run no-run-folder: {run}")
        return 1
    for line in table(run):
        print(line.rstrip())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
