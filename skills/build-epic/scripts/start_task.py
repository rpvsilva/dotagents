#!/usr/bin/env python3
"""Starts a `ready` task of a `building` ticket: its own worktree and branch, cut from the ticket branch.

Usage: python3 -I start_task.py <run> <TT-NN>
Adds `<run>/worktrees/TT-NN` on a new `task/<ticket-slug>/NN` from `origin/<ticket-branch>` and sets the task
`in-progress`. Prints `OK TT-NN in-progress wt=<path> tip=<sha7>`. A hub task (one `ticket NN` blocker) also merges
`origin/<base>` there (`decisions.md`'s `base:`, else the default branch), adding `synced=<sha7>` (the tip it merged);
a conflict removes the worktree and branch, leaves the task `ready` and
prints `FAIL TT-NN sync-conflict <files>`.
After the sync, it appends `{task, waited_on, ready_at, started_at, minutes}` to `<run>/waits.jsonl`: `waited_on`
its `ticket NN` blockers, `ready_at` the latest `Done:` of its task blockers (null, and `minutes` null, when one has none,
as in a run from before `merge_task.py` wrote it), times in UTC ISO; `status.py --report` reads it.
A task not `ready`, or whose worktree or branch already exists, is refused.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import ci, core, gitops, runfolder  # noqa: E402


def names(task):
    """(`TT`, `NN` or the orchestrator task's name) from the task file's name, `01-02-x.md` or `01-tests.md`."""
    tt, rest = task.stem.split("-", 1)
    return tt, rest.split("-", 1)[0]


def slug(branch):
    """The ticket slug: the ticket branch without its `feat/` prefix."""
    return branch[len("feat/"):] if branch.startswith("feat/") else branch


STAMP = "%Y-%m-%dT%H:%M:%SZ"


def ticket_name(run, n, width):
    """Ticket number `n` as its file names it (`02`), else zero-padded to `width`."""
    try:
        return runfolder.ticket_file(run, n).stem.split("-", 1)[0]
    except core.Fail:
        return n.zfill(width)


def done_at(run, task_id):
    """The task's `Done:` time, or None when it has no task file or no readable `Done:`."""
    try:
        return datetime.strptime(runfolder.get(runfolder.task_file(run, task_id), "Done") or "", STAMP).replace(
            tzinfo=timezone.utc)
    except (core.Fail, ValueError):
        return None


def record_wait(run, task, tt, task_id):
    """Appends the hub task's wait row to `<run>/waits.jsonl`."""
    on_tasks, on_tickets = runfolder.task_blockers(task)
    done = [done_at(run, f"{tt}-{n}") for n in sorted(on_tasks)]
    started = datetime.now(timezone.utc).replace(microsecond=0)
    ready = max(done) if done and all(done) else None
    row = {"task": task_id,
           "waited_on": [ticket_name(run, n, len(tt)) for n in sorted(on_tickets, key=lambda n: (len(n), n))],
           "ready_at": ready and ready.strftime(STAMP), "started_at": started.strftime(STAMP),
           "minutes": ready and round((started - ready).total_seconds() / 60)}
    with core.file_lock(run / ".waits.lock"):
        with open(run / "waits.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")


def git_in(ctx, repo, where, *args):
    """`git -C <where> <args>` under the repo's lock, its `Result` returned whatever the exit code."""
    with gitops.lock(repo):
        return ctx.run(["git", "-C", where, *args])


def step(ctx):
    if ctx.args:
        raise core.Fail("usage: start_task.py <run> <TT-NN>")
    task = runfolder.task_file(ctx.run_dir, ctx.id)
    status = runfolder.get(task, "Status")
    if status != "ready":
        raise core.Fail(f"{'already-started' if status == 'in-progress' else 'not-ready'} status={status}")
    tt, nn = names(task)
    ticket = runfolder.ticket_file(ctx.run_dir, tt)
    if runfolder.get(ticket, "Status") != "building":
        raise core.Fail(f"ticket-not-building status={runfolder.get(ticket, 'Status')}")
    ticket_branch = runfolder.get(ticket, "Branch")
    if not ticket_branch:
        raise core.Fail("no-branch")
    info = runfolder.run_info(ctx.run_dir)
    git = gitops.Git(info["Repo"], ctx.log)
    wt = ctx.run_dir / "worktrees" / f"{tt}-{nn}"
    branch = f"task/{slug(ticket_branch)}/{nn}"
    if wt.exists():
        raise core.Fail(f"already-started worktree={wt}")
    if git_in(ctx, info["Repo"], info["Repo"], "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}").rc == 0:
        raise core.Fail(f"already-started branch={branch}")
    git.fetch()
    tip = git.tip(f"refs/remotes/origin/{ticket_branch}")
    wt.parent.mkdir(parents=True, exist_ok=True)
    git.worktree_add(wt, tip, branch=branch)
    details = ""
    if runfolder.task_blockers(task)[1]:
        base = f"refs/remotes/origin/{ci.base(ctx.run_dir, info['Default'])}"
        synced = git.tip(base)
        merge = git_in(ctx, info["Repo"], wt, "merge", "--no-edit", base)
        if merge.rc != 0:
            conflicted = git_in(ctx, info["Repo"], wt, "diff", "--name-only", "--diff-filter=U").out.split()
            git_in(ctx, info["Repo"], wt, "merge", "--abort")
            git.worktree_remove(wt)
            git.branch_delete(branch)
            raise core.Fail(f"sync-conflict {' '.join(conflicted)}" if conflicted else "sync-failed")
        details = f" synced={synced[:7]}"
        record_wait(ctx.run_dir, task, tt, f"{tt}-{nn}")
        tip = git.tip(f"refs/heads/{branch}")
    runfolder.set_header(task, "Status", "in-progress")
    return "in-progress", f"wt={wt} tip={tip[:7]}{details}"


if __name__ == "__main__":
    core.main("start_task", step)
