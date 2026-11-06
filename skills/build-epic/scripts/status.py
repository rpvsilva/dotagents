#!/usr/bin/env python3
"""Shows a run at a glance: one line per ticket, each followed by its tasks, read from the run folder only.

Usage: python3 -I status.py <run> [--line <TT> | --write]

Each line is `<id> <status> <PR> <blocked by> <title>`, columns aligned, tasks indented under their ticket; `-` marks
no PR or no blocker. The one step that prints several stdout lines. Exits 0, or prints
`FAIL run no-run-folder: <path>` and exits 1.

`--line <TT>` prints the ticket's one status line for the human, at most 160 characters:
`<TT> <status> <PR> <title>  next: <what it waits on>` (`FAIL TT no-ticket` when there is none).
`--write` writes `<run>/state.md`, the one-screen summary the orchestrator continues from: every ticket's status line,
the tasks in flight, what needs a human (every `blocked` task, or one whose report says `blocked` or `plan-wrong`, with
its `why`; every `in-review` or `blocked` ticket) and the next actions. Prints `OK run state <path>`.
"""
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
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


MAX_LINE = 160


class Run:
    """The run folder's tickets and tasks, read once: `tickets[TT]` and `tasks[TT][NN]` hold (id, path, text)."""

    def __init__(self, run):
        self.dir = run
        self.tickets, self.tasks = {}, {}
        for f in sorted((run / "tickets").glob("*.md")):
            tt = f.stem.split("-", 1)[0]
            self.tickets[runfolder.norm(tt)] = (tt, f, f.read_text())
        for f in sorted((run / "tasks").glob("*.md")):
            ident = task_id(f)
            tt, _, nn = ident.partition("-")
            self.tasks.setdefault(runfolder.norm(tt), {})[runfolder.norm(nn)] = (ident, f, f.read_text())

    def status(self, text):
        return runfolder.header(text, "Status") or "?"

    def ticket_status(self, tt):
        return self.status(self.tickets[tt][2]) if tt in self.tickets else "missing"

    def why(self, ident):
        """The (status, why) of the task's stored report, or (None, None) without one."""
        report = self.dir / "reports" / f"{ident}.md"
        if not report.is_file():
            return None, None
        text = report.read_text()
        return runfolder.header(text, "status"), runfolder.header(text, "why") or ""

    def stuck(self, tt):
        """[(id, state, why)] for each task of ticket `tt` that waits on a human: `blocked`, or not `done` with a
        `blocked` or `plan-wrong` report."""
        out = []
        for ident, _, text in self.tasks.get(tt, {}).values():
            status = self.status(text)
            reported, why = self.why(ident)
            if status == "blocked" or (status != "done" and reported in ("blocked", "plan-wrong")):
                out.append((ident, reported if reported in ("blocked", "plan-wrong") else status, why or "no report"))
        return out

    def waits_on(self, tt):
        """The ticket numbers (as written) in `tt`'s `Blocked by` that are not `merged`."""
        tasks, tickets = runfolder.blockers(runfolder.header(self.tickets[tt][2], "Blocked by"))
        return [self.tickets[n][0] if n in self.tickets else n
                for n in sorted(tasks | tickets, key=lambda n: (not n.isdigit(), int(n) if n.isdigit() else 0, n))
                if self.ticket_status(n) != "merged"]

    def dispatchable(self, tt):
        """The `ready` tasks of ticket `tt` whose task blockers are `done` and ticket blockers `merged`."""
        tasks = self.tasks.get(tt, {})
        out = []
        for ident, _, text in tasks.values():
            if self.status(text) != "ready":
                continue
            on_tasks, on_tickets = runfolder.blockers(runfolder.header(text, "Blocked by"))
            if (all(n in tasks and self.status(tasks[n][2]) == "done" for n in on_tasks)
                    and all(self.ticket_status(n) == "merged" for n in on_tickets)):
                out.append(ident)
        return out

    def in_flight(self):
        return [ident for tasks in self.tasks.values() for ident, _, text in tasks.values()
                if self.status(text) == "in-progress"]

    def next(self, tt):
        """What ticket `tt` waits on next, in a few words."""
        text = self.tickets[tt][2]
        status = self.status(text)
        pr = ci.pr_number(runfolder.header(text, "PR"))
        if status == "merged":
            return "none"
        if status == "in-review":
            return f"human approval of #{pr}" if pr else "human approval"
        if status == "claimed":
            return f"{runfolder.header(text, 'Claimed by') or 'another developer'} (claimed)"
        stuck = self.stuck(tt)
        if stuck:
            return "human: " + ", ".join(f"task {i} {s}" for i, s, _ in stuck)
        if status == "blocked":
            return "human: ticket blocked"
        if status == "ready":
            waits = self.waits_on(tt)
            return ", ".join(f"ticket {n} ({self.ticket_status(runfolder.norm(n))})" for n in waits) or "start"
        tasks = self.tasks.get(tt, {}).values()
        if tasks and all(self.status(t) == "done" for _, _, t in tasks):
            return "ship"
        red = runfolder.header(text, "CI red")
        running = [i for i, _, t in tasks if self.status(t) == "in-progress"]
        ready = self.dispatchable(tt)
        parts = ([f"fix ticket CI red {red}"] if red else []) + (["tasks " + ", ".join(running)] if running else []) \
            + (["dispatch " + ", ".join(ready)] if ready else [])
        return "; ".join(parts) or "tasks"

    def line(self, tt):
        """The ticket's one status line, at most MAX_LINE characters: the title is cut first."""
        ident, _, text = self.tickets[tt]
        pr = ci.pr_number(runfolder.header(text, "PR"))
        head = f"{ident} {self.status(text)} {'#' + str(pr) if pr else '-'} "
        tail = f"  next: {self.next(tt)}"
        name = title(text)
        room = MAX_LINE - len(head) - len(tail)
        if len(name) > room:
            name = name[:max(room - 3, 0)].rstrip() + "..." if room > 3 else ""
        out = re.sub(r"\s+", " ", head + name).rstrip() + tail
        return out if len(out) <= MAX_LINE else out[:MAX_LINE - 3] + "..."

    def state(self):
        """The lines of `state.md`."""
        order = sorted(self.tickets, key=lambda n: (not n.isdigit(), int(n) if n.isdigit() else 0, n))
        human, start, ship, wait, dispatch = [], [], [], [], []
        for tt in order:
            ident, _, text = self.tickets[tt]
            status = self.status(text)
            human += [f"- task {i} {s}: {why}" for i, s, why in self.stuck(tt)]
            if status == "in-review":
                pr = ci.pr_number(runfolder.header(text, "PR"))
                human.append(f"- ticket {ident} in-review{' #' + str(pr) if pr else ''}: human approval")
            elif status == "blocked":
                human.append(f"- ticket {ident} blocked")
            elif status == "ready":
                waits = self.waits_on(tt)
                if waits:
                    wait.append(f"{ident} on {'+'.join(waits)}")
                else:
                    start.append(ident)
            elif status == "building":
                if self.next(tt) == "ship":
                    ship.append(ident)
                dispatch += self.dispatchable(tt)
        nexts = [f"- {what}: {', '.join(ids)}" for what, ids in
                 (("start", start), ("dispatch", dispatch), ("ship", ship), ("wait", wait)) if ids]
        return ([f"# Run state: {self.dir.name}",
                 f"Updated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
                 "",
                 "## Tickets"] + [self.line(tt) for tt in order]
                + ["", "## Tasks", f"In flight: {', '.join(self.in_flight()) or 'none'}",
                   "", "## Needs a human"] + (human or ["- nothing"])
                + ["", "## Next"] + (nexts or ["- nothing"]))


def write_state(run):
    """Writes `<run>/state.md` atomically and returns its path."""
    path = run / "state.md"
    fd, tmp = tempfile.mkstemp(dir=str(run), prefix=".state.md.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(Run(run).state()) + "\n")
        os.replace(tmp, str(path))
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return path


def main(argv):
    if not (len(argv) == 1 or (len(argv) == 2 and argv[1] == "--write")
            or (len(argv) == 3 and argv[1] == "--line")):
        print("FAIL run usage: status.py <run> [--line <TT> | --write]")
        return 2
    run = core.resolve(argv[0])
    if not run.is_dir():
        print(f"FAIL run no-run-folder: {run}")
        return 1
    if len(argv) == 3:
        state = Run(run)
        tt = runfolder.norm(argv[2])
        if tt not in state.tickets:
            print(f"FAIL {argv[2]} no-ticket")
            return 1
        print(state.line(tt))
    elif len(argv) == 2:
        print(f"OK run state {write_state(run)}")
    else:
        for line in table(run):
            print(line.rstrip())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
