#!/usr/bin/env python3
"""Starts a `ready` task of a `building` ticket: its own worktree and branch, cut from the ticket branch.

Usage: python3 -I start_task.py <run> <TT-NN|TT-tests>
Adds `<run>/worktrees/TT-NN` on a new `task/<ticket-slug>/NN` from `origin/<ticket-branch>` and sets the task
`in-progress`. Prints `OK TT-NN in-progress wt=<path> tip=<sha7>`. A hub task (one `ticket NN` blocker) also merges
`origin/<base>` there (`decisions.md`'s `base:`, else the default branch), adding `synced=<sha7>` (the tip it merged);
on a conflict it removes the worktree and branch, leaves the task `ready` and prints `FAIL TT-NN sync-conflict <files>`.
A task not `ready`, or whose worktree or branch already exists, is refused.
"""
import sys
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


def git_in(ctx, repo, where, *args):
    """`git -C <where> <args>` under the repo's lock, its `Result` returned whatever the exit code."""
    with gitops.lock(repo):
        return ctx.run(["git", "-C", where, *args])


def step(ctx):
    if ctx.args:
        raise core.Fail("usage: start_task.py <run> <TT-NN|TT-tests>")
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
        tip = git.tip(f"refs/heads/{branch}")
    runfolder.set_header(task, "Status", "in-progress")
    return "in-progress", f"wt={wt} tip={tip[:7]}{details}"


if __name__ == "__main__":
    core.main("start_task", step)
