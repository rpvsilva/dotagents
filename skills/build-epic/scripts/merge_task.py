#!/usr/bin/env python3
"""Merges one task's PR into its ticket branch: build-epic step 6's Merge, with Ticket CI.

Usage: python3 -I merge_task.py <run> <task>

In the task's worktree `<run>/worktrees/<task>`: reads the ticket PR's checks (waiting on pending ones), fetches and
merges the ticket branch's tip forward when the branch lacks it (never a reset or a checkout, so an earlier conflict
resolution is kept; a conflict is aborted), runs the merge guard on `origin/<ticket branch>...HEAD` (`steplib/guard.py`: rules read from the
run's default branch on origin, a hit passing only when `decisions.md` waives its rule `yes`), runs `map.md`'s `Checks`
timing each suite, inside the run's environment as `test_command:` runs them (`<run>/env`, `steplib/checks.py`'s
`in_env`), pushes, and merges the task's `PR:` by the merge rule (`merge_command` Setting), reading the PR back. Then
sets the task `done` with a `Done: <UTC ISO>` header, removes its worktree and branch, and opens the draft ticket PR
when the ticket has none.

Prints `OK <task> done suites=<n> <secs>s`, or `FAIL <task> <reason> (log: ...)`: `red <suite>`, `conflict <files>`,
`guarded <file:line rule>; ...`, `guard-no-base: <ref>`, `guard-bad-range: <range>`, `bad-rule: <why>`, `git-<cmd>` (a
git call of the guard failed), `ticket-ci-red <run-id>`, `wrong-base <branch>` (a PR into anything but the task's own
ticket branch), `merge-method`, `head-moved`, `merge`, `push`. Every FAIL before the merge leaves the task
`in-progress`.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import checks, ci, core, gitops, guard, merge, runfolder  # noqa: E402


def git_in(ctx, where, *args):
    """git in `where` (a worktree or the repo); returns the `Result`. Its commands edit no shared metadata, so no lock."""
    return ctx.run(["git", "-C", where, *args])


def catch_up(ctx, wt, ticket_ref):
    """Merges `ticket_ref` into the worktree's branch when the branch lacks it; Fails `conflict <files>`."""
    if gitops.Git(wt, ctx.log).is_ancestor(ticket_ref):
        return
    if git_in(ctx, wt, "merge", "--no-edit", ticket_ref).rc == 0:
        return
    files = git_in(ctx, wt, "diff", "--name-only", "--diff-filter=U").out.split()
    git_in(ctx, wt, "merge", "--abort")
    raise core.Fail("conflict " + (" ".join(files) or "?"))


def step(ctx):
    run = ctx.run_dir
    info = runfolder.run_info(run)
    repo, default = core.resolve(info["Repo"]), info["Default"]
    task = runfolder.task_file(run, ctx.id)
    tt = ctx.id.split("-", 1)[0]
    ticket = runfolder.ticket_file(run, tt)
    ticket_branch = runfolder.get(ticket, "Branch")
    if not ticket_branch:
        raise core.Fail("ticket-no-branch")
    pr = ci.pr_number(runfolder.get(task, "PR"))
    if not pr:
        raise core.Fail("no-pr")
    wt = run / "worktrees" / ctx.id
    if not wt.is_dir():
        raise core.Fail(f"no-worktree: {wt}")
    branch = git_in(ctx, wt, "symbolic-ref", "--short", "HEAD").out.strip()
    if not branch:
        raise core.Fail("detached-head")
    if git_in(ctx, wt, "status", "--porcelain", "--untracked-files=no").out.strip():
        raise core.Fail("uncommitted-changes")
    command = merge.setting(repo, "merge_command", ctx.log)

    ci.gate(run, ticket, ctx.log, repo)
    gitops.Git(wt, ctx.log).fetch()
    catch_up(ctx, wt, f"origin/{ticket_branch}")
    guard.check(wt, f"origin/{ticket_branch}...HEAD", run, ctx.log)
    suites = checks.run_all(checks.commands(run / "map.md"), wt, ctx.log, run)
    checks.save(run, ctx.id, suites)
    if suites[-1].rc != 0:
        raise core.Fail(f"red {suites[-1].command}")
    git = gitops.Git(wt, ctx.log)
    if git.push("origin", f"HEAD:refs/heads/{branch}").rc != 0:
        raise core.Fail("push")
    merge.merge(pr, git.tip(), repo, ctx.log, ticket_branch, command)

    runfolder.set_header(task, "Status", "done")
    runfolder.set_header(task, "Done", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    root = gitops.Git(repo, ctx.log)
    root.worktree_remove(wt)
    root.branch_delete(branch)
    if ci.enabled(run):
        ci.open_draft(ticket, default, ctx.log, repo)
    return "done", f"suites={len(suites)} {sum(s.seconds for s in suites):.0f}s"


if __name__ == "__main__":
    core.main("merge_task", step)
