#!/usr/bin/env python3
"""The reviewer runs the review rules will start, per ticket, for the approval message (spec behaviours 45-48, 51).

Usage: python3 -I review_plan.py <run>

Reads `<run>/tickets/*.md`, `<run>/tasks/*.md` and `decisions.md`'s `review_before_agent_merge:` (missing: `no`).
- `task` (small model): one per task when `review_before_agent_merge: yes`, or when the task is estimated high.
  The hub task (one with a `ticket NN` blocker) gets none: its review is the first half of the `hub` run.
- `hub` (large) for a ticket with a hub task, else `ticket` (large): the ticket's one deep review.
- `security` (large): only on a ticket estimated high, i.e. one with a task estimated high.
Orchestrator tasks (`-tests`, `-review-fixes`, `-fix-ci`) count for nothing: a tuning PR is checked by a script.

A task's estimate is its `Tier: high|low` line when it has one; else high when its `Writes` touch a storage format
(`STORAGE` below), else low. The true tier comes from the diff at review time; this is the plan's guess.

Prints one line per ticket, `<TT> <title>: <mode> <n> (<model>), ...: <runs> runs, <large> large, estimated <tier>`,
then `total: <runs> runs over <tickets> tickets, <per ticket> per ticket, <large> on the large model`. Exits 0, or
prints `FAIL run no-run-folder: <path>` and exits 1.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import core, runfolder  # noqa: E402

ORCHESTRATOR_TASKS = ("-tests", "-review-fixes", "-fix-ci")
STORAGE = (".json", ".jsonl", ".yml", ".yaml", ".toml", ".ini", ".csv", ".sql", ".db", ".sqlite", ".xml")
MODELS = {"task": "small", "hub": "large", "ticket": "large", "security": "large"}


def ticket_num(path):
    return runfolder.norm(path.stem.split("-", 1)[0])


def title(text):
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].split(":", 1)[-1].strip()
    return ""


def estimated_high(text):
    tier = (runfolder.header(text, "Tier") or "").strip().lower()
    if tier in ("high", "low"):
        return tier == "high"
    writes = [p.strip().strip("`") for p in (runfolder.header(text, "Writes") or "").split(",")]
    return any(p.lower().endswith(STORAGE) for p in writes if p)


def review_before_merge(run):
    path = run / "decisions.md"
    value = runfolder.get(path, "review_before_agent_merge") if path.exists() else None
    return (value or "").strip().strip("`").lower() == "yes"


def plan(run):
    """`[(ticket, title, {mode: runs}, high)]` in ticket order."""
    required = review_before_merge(run)
    tickets = {ticket_num(f): title(f.read_text(encoding="utf-8")) for f in sorted((run / "tickets").glob("*.md"))}
    tasks = {}
    for f in sorted((run / "tasks").glob("*.md")):
        if not f.stem.endswith(ORCHESTRATOR_TASKS):
            tasks.setdefault(ticket_num(f), []).append(f.read_text(encoding="utf-8"))
    out = []
    for tt in sorted(set(tickets) | set(tasks), key=lambda n: (not n.isdigit(), int(n) if n.isdigit() else 0, n)):
        runs, hub, high = {}, False, False
        for text in tasks.get(tt, ()):
            task_high = estimated_high(text)
            high = high or task_high
            if runfolder.blockers(runfolder.header(text, "Blocked by"))[1]:
                hub = True
            elif required or task_high:
                runs["task"] = runs.get("task", 0) + 1
        runs["hub" if hub else "ticket"] = 1
        if high:
            runs["security"] = 1
        out.append((tt, tickets.get(tt, ""), runs, high))
    return out


def lines(run):
    rows, total, large = [], 0, 0
    tickets = plan(run)
    for tt, name, runs, high in tickets:
        n = sum(runs.values())
        big = sum(c for mode, c in runs.items() if MODELS[mode] == "large")
        total, large = total + n, large + big
        modes = ", ".join(f"{mode} {runs[mode]} ({MODELS[mode]})" for mode in MODELS if mode in runs)
        rows.append(f"{tt.zfill(2) if tt.isdigit() else tt} {name}: {modes}: {n} runs, {big} large, "
                    f"estimated {'high' if high else 'low'}")
    per = f"{total / len(tickets):.1f}" if tickets else "0"
    rows.append(f"total: {total} runs over {len(tickets)} tickets, {per} per ticket, {large} on the large model")
    return rows


def main(argv):
    if len(argv) != 1:
        print("FAIL run usage: review_plan.py <run>")
        return 2
    run = core.resolve(argv[0])
    if not run.is_dir():
        print(f"FAIL run no-run-folder: {run}")
        return 1
    for line in lines(run):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
