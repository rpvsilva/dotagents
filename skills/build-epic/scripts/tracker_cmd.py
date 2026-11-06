#!/usr/bin/env python3
"""The run's tracker updates outside starting a ticket: the plan comment, the `blocked` label, claims, held updates.

Usage: python3 -I tracker_cmd.py <run> planned | blocked <TT|TT-NN> <why> | unblocked <TT> | claims | flush

- `planned` puts the plan in one comment on every ticket's issue, claimed ones included, editing the comment that
  starts with `<!-- build-epic-plan -->` when there is one. The comment holds `<run>/plan/TT.md` when the orchestrator
  wrote it (the answers decided for the ticket, any change to its scope), then the ticket's tasks (number, title,
  blocked by). Prints `OK plan commented <n> issues`.
- `blocked` adds the `blocked` label (created once per run) and one comment naming the task and `why` to each issue of
  the ticket; given a task id, its ticket's issues. Prints `OK TT blocked <n> issues`.
- `unblocked` removes the `blocked` label. Prints `OK TT unblocked <n> issues`.
- `claims` sets each `ready` ticket with an issue assigned to anyone but the human to `claimed`, recording who on a
  `Claimed by:` line. Prints `OK claims claimed <n> tickets [TT ...]`.
- `flush` applies the updates held while the identity was away. Prints `OK tracker flushed <n> held`.

Every command applies held updates first. When the `tracker` Setting names another tracker, nothing is touched and
the line ends `manual tracker=<name>`: the orchestrator makes the update through that tracker's connector.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import core, gh, runfolder, tracker  # noqa: E402

USAGE = "planned | blocked <TT|TT-NN> <why> | unblocked <TT> | claims | flush"
BLOCKED = "blocked"


def title(path):
    """The text after `# <id>:` on the file's first line."""
    first = Path(path).read_text().split("\n", 1)[0]
    return first.split(":", 1)[1].strip() if ":" in first else first.lstrip("# ").strip()


def tickets(run_dir):
    return sorted((run_dir / "tickets").glob("*.md"))


def number(path):
    return path.stem.split("-", 1)[0]


def manual(ctx, info):
    """The other tracker's name when the `tracker` Setting names one, else None."""
    kind = tracker.setting("build-epic", "tracker", info["Repo"], ctx.log)
    return kind if kind and kind.lower() != "github" else None


def open_tracker(ctx):
    """(`Tracker`, None), or (None, the other tracker's name); held updates applied."""
    info = runfolder.run_info(ctx.run_dir)
    other = manual(ctx, info)
    if other:
        return None, other
    t = tracker.Tracker(ctx.run_dir, info["Repo"], ctx.log)
    if t.held():
        t.flush()
    return t, None


def plan_body(run_dir, ticket):
    tt = number(ticket)
    lines = [tracker.MARKER, f"## Plan for ticket {tt}: {title(ticket)}", ""]
    notes = run_dir / "plan" / f"{tt}.md"
    if notes.exists():
        lines += [notes.read_text().strip(), ""]
    lines.append("Tasks:")
    for task in sorted((run_dir / "tasks").glob(f"{tt}-*.md")):
        tid = task.stem.split("-", 2)
        tid = "-".join(tid[:2]) if len(tid) > 1 and tid[1].isdigit() else task.stem
        lines.append(f"- {tid}: {title(task)} (blocked by: {runfolder.get(task, 'Blocked by') or 'none'})")
    return "\n".join(lines) + "\n"


def planned(ctx):
    t, other = open_tracker(ctx)
    if other:
        return "manual", f"tracker={other}"
    count = 0
    for ticket in tickets(ctx.run_dir):
        body = plan_body(ctx.run_dir, ticket)
        for n in tracker.issues(runfolder.get(ticket, "Issue")):
            t.upsert_plan(n, body)
            count += 1
    return "commented", f"{count} issues"


def ticket_of(ctx, ident):
    """(ticket file, task file or None) for a ticket number or a task id."""
    if "-" in ident:
        task = runfolder.task_file(ctx.run_dir, ident)
        return runfolder.ticket_file(ctx.run_dir, runfolder.get(task, "Ticket") or ident.split("-", 1)[0]), task
    return runfolder.ticket_file(ctx.run_dir, ident), None


def blocked(ctx, target):
    ticket, task = ticket_of(ctx, target)
    why = " ".join(ctx.args).strip()
    if not why:
        raise core.Fail("usage: tracker_cmd.py <run> blocked <TT|TT-NN> <why>")
    t, other = open_tracker(ctx)
    if other:
        return "manual", f"tracker={other}"
    what = f"task {target} ({title(task)})" if task else f"ticket {number(ticket)}"
    numbers = tracker.issues(runfolder.get(ticket, "Issue"))
    for n in numbers:
        t.add_label(n, BLOCKED)
        t.comment(n, f"Blocked: {what}: {why}")
    return "blocked", f"{len(numbers)} issues"


def unblocked(ctx, target):
    if ctx.args:
        raise core.Fail("usage: tracker_cmd.py <run> unblocked <TT>")
    ticket, _ = ticket_of(ctx, target)
    t, other = open_tracker(ctx)
    if other:
        return "manual", f"tracker={other}"
    numbers = tracker.issues(runfolder.get(ticket, "Issue"))
    for n in numbers:
        t.remove_label(n, BLOCKED)
    return "unblocked", f"{len(numbers)} issues"


def claims(ctx):
    t, other = open_tracker(ctx)
    if other:
        return "manual", f"tracker={other}"
    human = t.human()
    claimed = []
    for ticket in tickets(ctx.run_dir):
        if runfolder.get(ticket, "Status") != "ready":
            continue
        others = []
        for n in tracker.issues(runfolder.get(ticket, "Issue")):
            res = gh.gh(["issue", "view", str(n), "--json", "assignees", "--jq", ".assignees[].login"], ctx.log,
                        cwd=t.repo)
            if res.rc != 0:
                raise core.Fail("gh-issue-view")
            others += [login for login in res.out.split() if login != human and login not in others]
        if others:
            runfolder.set_header(ticket, "Claimed by", ", ".join(others))
            runfolder.set_header(ticket, "Status", "claimed")
            claimed.append(number(ticket))
    return "claimed", " ".join([f"{len(claimed)} tickets", *claimed])


def flush(ctx):
    info = runfolder.run_info(ctx.run_dir)
    other = manual(ctx, info)
    if other:
        return "manual", f"tracker={other}"
    t = tracker.Tracker(ctx.run_dir, info["Repo"], ctx.log)
    held = len(t.held())
    if held:
        t.flush()
    return "flushed", f"{held} held"


def no_args(fn):
    def step(ctx):
        if ctx.args:
            raise core.Fail(f"usage: tracker_cmd.py <run> {USAGE}")
        return fn(ctx)
    return step


# The commands that act on the run: the id on their output line and their step.
RUN_COMMANDS = {"planned": ("plan", no_args(planned)), "claims": ("claims", no_args(claims)),
                "flush": ("tracker", no_args(flush))}
# The commands that act on one ticket, named by its number or a task's id: `fn(ctx, target)`.
TICKET_COMMANDS = {"blocked": blocked, "unblocked": unblocked}


def cli(argv):
    command = argv[1] if len(argv) > 1 else ""
    if command in RUN_COMMANDS:
        ident, fn = RUN_COMMANDS[command]
        core.main("tracker", fn, argv=[argv[0], ident, *argv[2:]])
    if command in TICKET_COMMANDS and len(argv) > 2:
        # The ticket's number is the id, so the log is `<run>/logs/<TT>-tracker.log`.
        target, fn = argv[2], TICKET_COMMANDS[command]
        core.main("tracker", lambda ctx: fn(ctx, target), argv=[argv[0], target.split("-", 1)[0], *argv[3:]])
    sys.stdout.write(f"FAIL {command if core.ID.match(command) else 'tracker'} usage: tracker_cmd.py <run> {USAGE}\n")
    sys.exit(2)


if __name__ == "__main__":
    cli(sys.argv[1:])
