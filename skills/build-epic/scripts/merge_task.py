#!/usr/bin/env python3
"""Merges one task's PR into its ticket branch: build-epic step 6's Merge, with Ticket CI.

Usage: python3 -I merge_task.py <run> <task>

In the task's worktree `<run>/worktrees/<task>`: reads the ticket PR's checks (waiting on pending ones), fetches and
merges the ticket branch's tip forward when the branch lacks it (never a reset or a checkout, so an earlier conflict
resolution is kept), runs `map.md`'s `Checks` timing each suite, pushes, and merges the task's `PR:` by the merge rule
(`merge_command` Setting), reading the PR back. Then sets the task `done`, removes its worktree and branch, opens the
draft ticket PR when the ticket has none, and, when this was the ticket's last implementer task and a suite ran at
least `tune_min_seconds`, writes the tuning task `tasks/TT-tests.md`.

Prints `OK <task> done suites=<n> <secs>s`, or `FAIL <task> <reason> (log: ...)`: `red <suite>`, `conflict <files>`,
`ticket-ci-red <run-id>`, `wrong-base <branch>` (a PR into anything but the task's own ticket branch), `merge-method`, `head-moved`, `merge`, `push`. Every FAIL before the merge leaves the task `in-progress`.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import checks, ci, core, gitops, merge, runfolder  # noqa: E402


def git_in(ctx, where, *args):
    """git in `where` (a worktree or the repo) under the repo's lock; returns the `Result`."""
    with gitops.lock(where):
        return ctx.run(["git", "-C", where, *args])


def catch_up(ctx, wt, ticket_ref):
    """Merges `ticket_ref` into the worktree's branch when the branch lacks it. Fails `conflict <files>`."""
    if gitops.Git(wt, ctx.log).is_ancestor(ticket_ref):
        return
    if git_in(ctx, wt, "merge", "--no-edit", ticket_ref).rc == 0:
        return
    files = git_in(ctx, wt, "diff", "--name-only", "--diff-filter=U").out.split()
    git_in(ctx, wt, "merge", "--abort")
    raise core.Fail("conflict " + (" ".join(files) or "?"))


def last_implementer_task(run, tt):
    """True when every numbered task of ticket `tt` is `done`."""
    tasks = [f for f in (run / "tasks").glob("*.md")
             if f.stem.split("-")[0].isdigit() and runfolder.norm(f.stem.split("-")[0]) == runfolder.norm(tt)
             and len(f.stem.split("-")) > 1 and f.stem.split("-")[1].isdigit()]
    return bool(tasks) and all(runfolder.get(f, "Status") == "done" for f in tasks)


def write_tuning_task(ctx, repo, tt, ticket, default, suites):
    """Writes `tasks/TT-tests.md`, `ready`, its Range from the ticket branch's fork point to its tip."""
    path = ctx.run_dir / "tasks" / f"{tt}-tests.md"
    if path.exists():
        return
    root = gitops.Git(repo, ctx.log)
    root.fetch()
    branch = runfolder.get(ticket, "Branch")
    commits = git_in(ctx, repo, "rev-list", "--first-parent", "--reverse", f"origin/{branch}", "--not",
                     f"origin/{default}").out.split()
    if not commits:
        ctx.note("ticket branch has no commits of its own: no tuning task")
        return
    first, tip = root.tip(f"{commits[0]}^"), root.tip(f"origin/{branch}")
    title = ci.title(ticket).split(": ", 1)[-1]
    lines = [f"# {tt}-tests: tune {title}", "Status: ready", f"Ticket: {tt}", f"Range: {first}..{tip}", "",
             "## Suites", ""] + [f"- `{s.command}`: {s.seconds:.0f}s" for s in suites]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ctx.note(f"wrote {path}")


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
    tune_min = int(merge.setting(repo, "tune_min_seconds", ctx.log) or 30)

    ci.gate(run, ticket, ctx.log, repo)
    gitops.Git(wt, ctx.log).fetch()
    catch_up(ctx, wt, f"origin/{ticket_branch}")
    suites = checks.run_all(checks.commands(run / "map.md"), wt, ctx.log)
    checks.save(run, ctx.id, suites)
    if suites[-1].rc != 0:
        raise core.Fail(f"red {suites[-1].command}")
    git = gitops.Git(wt, ctx.log)
    if git.push("origin", f"HEAD:refs/heads/{branch}").rc != 0:
        raise core.Fail("push")
    merge.merge(pr, git.tip(), repo, ctx.log, ticket_branch, command)

    runfolder.set_header(task, "Status", "done")
    root = gitops.Git(repo, ctx.log)
    root.worktree_remove(wt)
    root.branch_delete(branch)
    if (ctx.id.split("-", 1)[1].isdigit() and last_implementer_task(run, tt)
            and any(s.seconds >= tune_min for s in suites)):
        write_tuning_task(ctx, repo, tt, ticket, default, suites)
    if ci.enabled(run):
        ci.open_draft(ticket, default, ctx.log, repo)
    return "done", f"suites={len(suites)} {sum(s.seconds for s in suites):.0f}s"


if __name__ == "__main__":
    core.main("merge_task", step)
