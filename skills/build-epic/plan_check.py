#!/usr/bin/env python3
"""Checks a build-epic (or security-audit) plan for tasks that would conflict when run at once.

Usage: plan_check.py <run folder>

Reads <run>/tasks/*.md and <run>/tickets/*.md. Two tasks whose `Writes:` lines share a path must be ordered by
`Blocked by`: within a ticket through a chain of tasks; across tickets through a chain of tickets, or through a task
blocked by the other ticket (`Blocked by: 02, ticket 01`: that ticket merged). Every task the planner wrote needs a
`Writes:` line; tasks the orchestrator writes itself (`*-tests.md`, `*-review-fixes.md`, `*-fix-ci.md`) are skipped.
Prints one line per problem and exits 1, or prints `plan ok: <n> tasks, <m> lanes` and exits 0. A lane is a group of
tickets chained by ticket `Blocked by`; tickets ordered only through a hub task stay in separate lanes.
"""
import re
import sys
from pathlib import Path

ORCHESTRATOR_TASKS = ("-tests.md", "-review-fixes.md", "-fix-ci.md")


def header(text, key):
    m = re.search(rf"^{key}:[ \t]*(.*)$", text, re.MULTILINE)
    return m.group(1).strip() if m else None


def norm(n):
    return str(int(n)) if n.isdigit() else n


def blockers(value):
    """A `Blocked by` line split into (task numbers, ticket numbers). `01`, `TT-01` name tasks; `ticket 01` a ticket."""
    if not value or value.lower().startswith("none"):
        return set(), set()
    tickets = {norm(m.group(1)) for m in re.finditer(r"\bticket\s+(\d+)\b", value, re.IGNORECASE)}
    rest = re.sub(r"\bticket\s+\d+\b", "", value, flags=re.IGNORECASE)
    tasks = {norm(m.group(2) or m.group(1)) for m in re.finditer(r"\b(\d+)(?:-(\d+))?\b", rest)}
    return tasks, tickets


def closure(graph):
    """Each node mapped to every node it reaches through `Blocked by`, transitively."""
    out = {}
    for start in graph:
        seen, todo = set(), list(graph[start])
        while todo:
            n = todo.pop()
            if n not in seen:
                seen.add(n)
                todo.extend(graph.get(n, ()))
        out[start] = seen
    return out


def load(run):
    tickets = {}
    for f in sorted((run / "tickets").glob("*.md")):
        tickets[norm(f.name.split("-", 1)[0])] = blockers(header(f.read_text(), "Blocked by"))[0]
    tasks, problems = {}, []
    for f in sorted((run / "tasks").glob("*.md")):
        if f.name.endswith(ORCHESTRATOR_TASKS):
            continue
        text = f.read_text()
        ticket = header(text, "Ticket") or "none"
        ticket = "none" if ticket.lower().startswith("none") else norm(ticket.split()[0])
        parts = f.stem.split("-")
        num = parts[1] if ticket != "none" and len(parts) > 1 and parts[1].isdigit() else parts[0]
        writes = header(text, "Writes")
        if writes is None:
            problems.append(f"{f.name}: no Writes line")
            writes = ""
        task_blocks, ticket_blocks = blockers(header(text, "Blocked by"))
        tasks[(ticket, norm(num))] = {
            "file": f.name,
            "writes": {p.strip().strip("`") for p in writes.split(",") if p.strip() and p.strip().lower() != "none"},
            "blocked": task_blocks,
            "tickets": ticket_blocks,
        }
    return tickets, tasks, problems


def check(run):
    tickets, tasks, problems = load(Path(run))
    task_reach = closure({k: {(k[0], b) for b in v["blocked"]} for k, v in tasks.items()})
    ticket_reach = closure(tickets)
    for key, reach in task_reach.items():
        if key in reach:
            problems.append(f"{tasks[key]['file']}: Blocked by cycle")
    for t, reach in ticket_reach.items():
        if t in reach:
            problems.append(f"ticket {t}: Blocked by cycle")

    def waits_for_ticket(task):
        """Every ticket that must be merged before this task starts."""
        direct = set(tasks[task]["tickets"])
        for up in task_reach.get(task, ()):
            direct |= tasks.get(up, {}).get("tickets", set())
        direct |= tickets.get(task[0], set())
        out = set(direct)
        for t in direct:
            out |= ticket_reach.get(t, set())
        return out

    def ordered(a, b):
        if a[0] == b[0]:
            return b in task_reach.get(a, ()) or a in task_reach.get(b, ())
        return a[0] in waits_for_ticket(b) or b[0] in waits_for_ticket(a)

    keys = sorted(tasks)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            shared = tasks[a]["writes"] & tasks[b]["writes"]
            if shared and not ordered(a, b):
                problems.append(f"{tasks[a]['file']} and {tasks[b]['file']} both write {', '.join(sorted(shared))}"
                                " with no Blocked by between them")

    lane = {t: t for t in set(tickets) | {k[0] for k in tasks}}

    def find(t):
        while lane[t] != t:
            t = lane[t]
        return t

    for t, blocked in tickets.items():
        for b in blocked:
            if b in lane:
                lane[find(t)] = find(b)
    return problems, len(tasks), len({find(t) for t in lane})


def main(argv):
    if len(argv) != 2:
        print("usage: plan_check.py <run folder>", file=sys.stderr)
        return 2
    problems, n_tasks, n_lanes = check(argv[1])
    for p in problems:
        print(p)
    if problems:
        return 1
    print(f"plan ok: {n_tasks} tasks, {n_lanes} lanes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
