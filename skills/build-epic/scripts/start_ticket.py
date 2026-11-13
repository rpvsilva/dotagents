#!/usr/bin/env python3
"""Starts a `ready` ticket: makes its branch from `origin/<base>`, here and on origin, sets it `building`, marks its
issues started.

Usage: python3 -I start_ticket.py <run> <TT>
The base is `decisions.md`'s `base:`, or the default branch when it is unset. The local branch is where the ticket's
tasks merge (`merge_task.py`), reset to the base here so a branch left by an earlier run is never built on; origin's
copy stays at the base until `ship_ticket.py open` pushes the ticket.
Prints `OK TT building branch=<b> base=<sha7>`, plus `started=manual` when the `tracker` Setting names another
tracker or the `in_progress` Setting is set: the orchestrator then marks the issues started as those say. Started,
by default: the `in progress` label added (created once per run) and each issue assigned to the human.
A failed push prints `FAIL TT push` and leaves the ticket `ready`; a ticket not `ready` is refused.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import ci, core, gitops, runfolder, tracker  # noqa: E402

STARTED = ("building", "in-review", "merged")


def step(ctx):
    if ctx.args:
        raise core.Fail("usage: start_ticket.py <run> <TT>")
    ticket = runfolder.ticket_file(ctx.run_dir, ctx.id)
    status = runfolder.get(ticket, "Status")
    if status in STARTED:
        raise core.Fail(f"already-started status={status}")
    if status != "ready":
        raise core.Fail(f"not-ready status={status}")
    branch = runfolder.get(ticket, "Branch")
    if not branch:
        raise core.Fail("no-branch")
    info = runfolder.run_info(ctx.run_dir)
    git = gitops.Git(info["Repo"], ctx.log)
    git.fetch()
    base = git.tip(f"refs/remotes/origin/{ci.base(ctx.run_dir, info['Default'])}")
    # The SHA, not a ref name, on the left: nothing in the refspec is ever read by a shell or rewritten (bug 6).
    if git.push("origin", f"{base}:refs/heads/{branch}").rc != 0:
        raise core.Fail("push")
    with gitops.lock(info["Repo"]):
        git._git("branch", "-f", "--no-track", branch, base)
    runfolder.set_header(ticket, "Status", "building")
    details = f"branch={branch} base={base[:7]}"
    numbers = tracker.issues(runfolder.get(ticket, "Issue"))
    if numbers:
        kind = tracker.setting("build-epic", "tracker", info["Repo"], ctx.log)
        mark = tracker.setting("build-epic", "in_progress", info["Repo"], ctx.log)
        if kind and kind.lower() != "github":
            return "building", details + " started=manual"
        t = tracker.Tracker(ctx.run_dir, info["Repo"], ctx.log)
        human = t.human()
        for n in numbers:
            if not mark:
                t.add_label(n, tracker.IN_PROGRESS)
            t.assign(n, human)
        if mark:
            details += " started=manual"
    return "building", details


if __name__ == "__main__":
    core.main("start_ticket", step)
