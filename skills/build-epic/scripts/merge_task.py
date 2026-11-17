#!/usr/bin/env python3
"""Merges one task into its ticket branch, locally: build-epic step 6's Merge. No PR and no GitHub call.

Usage: python3 -I merge_task.py <run> <task>

The ticket branch's tip is the local `refs/heads/<branch>` (`gitops.ticket_tip`: made from `origin/<branch>` when
missing); nothing is pushed until the ticket ships. In the task's worktree `<run>/worktrees/<task>`: a task whose file
says `Tier: high` needs its review first, a reviewer's output `<run>/reviews/<task>-<sha7>.json` (`.md` in a run
from before the agent protocol) of the head it merges (before the catch-up below), else it fails `unreviewed`. Then it merges the ticket branch's tip forward when the task branch
lacks it (never a reset or a checkout, so an earlier conflict resolution is kept; a conflict is aborted), runs the
merge guard on `<ticket branch>...HEAD` (`steplib/guard.py`: rules read from the run's default branch on origin, a hit
passing only when `decisions.md` waives its rule `yes`) and `map.md`'s `Checks`, timing each suite, inside the run's
environment as `test_command:` runs them (`steplib/checks.py`'s `in_env`). Then it makes a merge commit on the ticket
branch, `Merge task <task>: <title>`, parents the ticket's tip and the task's head, the task's tree, and moves the
branch to it only if no other merge moved it meanwhile (`ticket-moved`). When the ticket already has a PR (`PR:`, a fix
round after `ship_ticket.py open`), it pushes the ticket branch, so the PR shows the fix. Then it sets the task
`done` with `Done: <UTC ISO>` and `Commit: <merge sha>` headers and removes its worktree and branch. A rerun whose task
head is already on the ticket branch skips the merge.

Prints `OK <task> done suites=<n> <secs>s commit=<sha7>`, or `FAIL <task> <reason> (log: ...)`: `unreviewed`,
`red <suite>`, `conflict <files>`, `guarded <file:line rule>; ...`, `guard-no-base: <ref>`, `guard-bad-range: <range>`,
`bad-rule: <why>`, `git-<cmd>` (a git call failed), `ticket-moved` or `push`. Every FAIL leaves the task `in-progress`.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import checks, core, gitops, guard, runfolder, taskwt  # noqa: E402


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


def high_tier(task):
    return (runfolder.get(task, "Tier") or "").strip().strip("`").lower() == "high"


def reviewed(run, ident, head):
    """True when `<run>/reviews/<ident>-<sha7>.json` (`.md` in an older run) holds a review of `head`."""
    reviews = core.resolve(run) / "reviews"
    return any((reviews / f"{ident}-{head[:7]}{ext}").is_file() for ext in (".json", ".md"))


def step(ctx):
    run = ctx.run_dir
    repo = core.resolve(runfolder.run_info(run)["Repo"])
    task = runfolder.task_file(run, ctx.id)
    tt = ctx.id.split("-", 1)[0]
    ticket = runfolder.ticket_file(run, tt)
    ticket_branch = runfolder.get(ticket, "Branch")
    if not ticket_branch:
        raise core.Fail("ticket-no-branch")
    wt = run / "worktrees" / ctx.id
    if not wt.is_dir():
        raise core.Fail(f"no-worktree: {wt}")
    branch = git_in(ctx, wt, "symbolic-ref", "--short", "HEAD").out.strip()
    if not branch:
        raise core.Fail("detached-head")
    if git_in(ctx, wt, "status", "--porcelain", "--untracked-files=no").out.strip():
        raise core.Fail("uncommitted-changes")
    root, here = gitops.Git(repo, ctx.log), gitops.Git(wt, ctx.log)
    if high_tier(task) and not reviewed(run, ctx.id, here.tip()):
        ctx.note(f"Tier: high, and no reviews/{ctx.id}-<sha7>.json reviews the head {here.tip()}")
        raise core.Fail("unreviewed")

    ticket_ref = f"refs/heads/{ticket_branch}"
    tip = root.ticket_tip(ticket_branch)
    head = here.tip()
    landed = root.is_ancestor(head, tip)  # a rerun after the merge commit landed
    suites = []
    if not landed:
        catch_up(ctx, wt, ticket_ref)
        guard.check(wt, f"{ticket_ref}...HEAD", run, ctx.log)
        suites = checks.run_all(checks.commands(run / "map.md"), wt, ctx.log, run)
        checks.save(run, ctx.id, suites)
        if suites[-1].rc != 0:
            raise core.Fail(f"red {suites[-1].command}")
        head = here.tip()
        message = f"Merge task {ctx.id}: {taskwt.title(task)}"
        merged = git_in(ctx, repo, "commit-tree", f"{head}^{{tree}}", "-p", tip, "-p", head, "-m", message)
        if merged.rc != 0:
            raise core.Fail("git-commit-tree")
        root.advance(ticket_branch, merged.out.strip(), tip)
        tip = merged.out.strip()
    if runfolder.get(ticket, "PR") and root.push("origin", f"{ticket_ref}:{ticket_ref}").rc != 0:
        raise core.Fail("push")

    runfolder.set_header(task, "Status", "done")
    runfolder.set_header(task, "Done", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    runfolder.set_header(task, "Commit", tip)
    root.worktree_remove(wt)
    root.branch_delete(branch)
    return "done", f"suites={len(suites)} {sum(s.seconds for s in suites):.0f}s commit={tip[:7]}"


if __name__ == "__main__":
    core.main("merge_task", step)
