#!/usr/bin/env python3
"""Pushes a task's fix round to its open PR, after catching up with the ticket branch.

Usage: python3 -I push_fix.py <run> <TT-NN>

Fetches, merges `origin/<ticket-branch>` when the task branch lacks its tip, pushes, and prints
`OK TT-NN pushed head=<sha7>`. A conflict is left in the worktree and prints `FAIL TT-NN conflict <files>`. Run
`finish_task.py` first when the implementer left its fix uncommitted: this step fails on uncommitted changes.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import core, gitops, taskwt  # noqa: E402


def step(ctx):
    if ctx.args:
        raise core.Fail("usage: push_fix.py <run> <TT-NN>")
    task = taskwt.Task(ctx)
    task.need_worktree()
    task.refuse_unfinished()
    branch = task.branch()
    task.fetch()
    task.merge_upstream()
    if gitops.Git(task.worktree, ctx.log).push("origin", f"HEAD:refs/heads/{branch}").rc != 0:
        raise core.Fail("git-push")
    return "pushed", f"head={task.head()[:7]}"


if __name__ == "__main__":
    core.main("push_fix", step)
