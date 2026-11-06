#!/usr/bin/env python3
"""Pushes a finished task's branch and opens its PR into the ticket branch, or finds the one already open.

Usage: python3 -I open_pr.py <run> <TT-NN>

Prints `OK TT-NN pr=<n> <url>` and records `PR: <url>` on the task. Run it after `finish_task.py`: a worktree with
uncommitted changes or a merge in progress fails. The title is the subject of the oldest non-merge commit on the
branch's first-parent chain since the ticket branch, as written; the body comes from `steplib/prbody.py` and is
kept in `<run>/logs/<TT-NN>-pr-body.md`. Before each `gh pr create` the step looks for an open PR from the branch
(`gh pr list --head`), so a retry or a rerun never opens a second.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import core, gh, gitops, prbody, runfolder, taskwt  # noqa: E402

URL = re.compile(r"https?://\S+/pull/(\d+)")


def find_open(ctx, task, branch):
    """(number, url) of the open PR from `branch` into the ticket branch, or None."""
    res = gh.gh(["pr", "list", "--head", branch, "--base", task.ticket_branch, "--state", "open",
                 "--json", "number,url"], ctx.log, cwd=task.worktree)
    if res.rc != 0:
        raise core.Fail("gh-pr-list")
    try:
        prs = json.loads(res.out or "[]")
    except ValueError:
        raise core.Fail("gh-pr-list: not JSON")
    return (str(prs[0]["number"]), prs[0]["url"]) if prs else None


def create(ctx, task, branch, title, body_file):
    """Opens the PR. `gh pr create` itself is never retried blind: each try first looks for the PR it may have made."""
    for attempt in range(1, gh.TRIES + 1):
        found = find_open(ctx, task, branch)
        if found:
            return found
        res = core.run(["gh", "pr", "create", "--base", task.ticket_branch, "--head", branch,
                        "--title", title, "--body-file", body_file], ctx.log, cwd=task.worktree)
        m = URL.search(res.out) if res.rc == 0 else None
        if m:
            return m.group(1), m.group(0)
        if res.rc == 0 or attempt == gh.TRIES or not (gh.transient(res) or "already exists" in res.err):
            raise core.Fail("gh-pr-create")
        time.sleep(gh.WAITS[attempt - 1] * float(os.environ.get("BUILD_EPIC_RETRY_SLEEP", "1")))
    raise core.Fail("gh-pr-create")


def step(ctx):
    if ctx.args:
        raise core.Fail("usage: open_pr.py <run> <TT-NN>")
    task = taskwt.Task(ctx)
    task.need_worktree()
    task.refuse_unfinished()
    branch = task.branch()
    task.fetch()
    subjects = task.git("log", "--no-merges", "--first-parent", "--format=%s", f"{task.upstream}..HEAD").out
    subjects = [s for s in subjects.splitlines() if s.strip()]
    if not subjects:
        raise core.Fail("no-commits: run finish_task.py, which ends a task with no changes")
    title = subjects[-1]
    if gitops.Git(task.worktree, ctx.log).push("origin", f"HEAD:refs/heads/{branch}").rc != 0:
        raise core.Fail("git-push")
    body_file = ctx.run_dir / "logs" / f"{ctx.id}-pr-body.md"
    body_file.write_text(prbody.task_body(ctx.run_dir, ctx.id, task.path, task.ticket_path))
    number, url = create(ctx, task, branch, title, body_file)
    runfolder.set_header(task.path, "PR", url)
    return f"pr={number}", url


if __name__ == "__main__":
    core.main("open_pr", step)
