#!/usr/bin/env python3
"""Offers, then makes, the catch-up of a run whose base changes mid-run: build-epic step 6, behaviour 23.

Usage: python3 -I catch_up.py <run> <new-base> [--apply]

Lists the unstarted tickets (`ready`, their branch not on origin), which `start_ticket.py` will cut from the new base
once it is recorded, and the started ones (`building` or `in-review`, their branch on origin) whose branch lacks the
new base's tip on origin. Without `--apply` it changes nothing and prints
`OK run catch-up <n> unstarted <m> started unstarted=<TT,..|none> started=<TT,..|none>`.

With `--apply` it records `base: <new-base>` in `decisions.md`, then, for each started ticket listed, merges
`origin/<new-base>` into its branch in a scratch worktree and pushes it (a fast-forward, never a force). Every started
ticket whose branch holds the new base's tip, merged now or before, then has its open PR (its `PR:` line) moved to the
new base when it is on another one. Prints `OK run caught-up base=<new-base> <n> unstarted <m> started ...`. A
conflicted merge is aborted and leaves that branch and its PR as they were; the others still merge, and it prints
`FAIL run conflict <TT>: <files>[; <TT>: <files>]`. A PR that will not move fails `pr-edit <TT>`. Rerun it once those
are resolved: a branch already holding the tip is not merged again, and a PR still on the old base is moved then. A
new base missing on origin fails `no-base <new-base>` and records nothing.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import ci, core, decisions, gh, gitops, merge, runfolder  # noqa: E402

USAGE = "usage: catch_up.py <run> <new-base> [--apply]"
STARTED = ("building", "in-review")


def tickets(run):
    """(`TT`, path) of every ticket file, in order."""
    return [(f.stem.split("-", 1)[0], f) for f in sorted((run / "tickets").glob("*.md"))]


def on_origin(git, branch):
    return git._git("rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}^{{commit}}", check=False).rc == 0


def plan(run, git, tip):
    """(unstarted, started, behind): the tickets to cut from the new base, every started one, and the started ones
    lacking its `tip`."""
    unstarted, started, behind = [], [], []
    for tt, path in tickets(run):
        status, branch = runfolder.get(path, "Status"), runfolder.get(path, "Branch")
        if not branch:
            continue
        pushed = on_origin(git, branch)
        if status == "ready" and not pushed:
            unstarted.append((tt, path, branch))
        elif status in STARTED and pushed:
            started.append((tt, path, branch))
            if not git.is_ancestor(tip, f"refs/remotes/origin/{branch}"):
                behind.append((tt, path, branch))
    return unstarted, started, behind


def record(run, base):
    with decisions.lock(run):
        d = decisions.load(run) or decisions.Decisions()
        d.headers["base"] = base
        decisions.save(run, d)


def catch_up(ctx, git, repo, tt, branch, base):
    """Merges `origin/<base>` into the ticket branch and pushes it; returns the conflicted files, or None."""
    wt = ctx.run_dir / "worktrees" / f"catch-up-{tt}"
    wt.parent.mkdir(parents=True, exist_ok=True)
    git.worktree_add(wt, f"refs/remotes/origin/{branch}", detach=True)
    try:
        with gitops.lock(repo):
            merged = ctx.run(["git", "-C", wt, "merge", "--no-edit", f"refs/remotes/origin/{base}"])
        if merged.rc != 0:
            with gitops.lock(repo):  # the worktree's removal below drops the half-done merge
                files = ctx.run(["git", "-C", wt, "diff", "--name-only", "--diff-filter=U"]).out.split()
            return files or ["(merge failed)"]
        if git.push("origin", f"{_head(ctx, repo, wt)}:refs/heads/{branch}").rc != 0:
            raise core.Fail(f"push {tt}")
    finally:
        git.worktree_remove(wt)
    return None


def retarget(ctx, repo, tt, path, base):
    """Moves the ticket's open PR to `base` when it is on another one."""
    number = ci.pr_number(runfolder.get(path, "PR"))
    if not number:
        return
    got = merge.view(number, ctx.log, repo)
    if got.get("state") != "OPEN" or got.get("baseRefName") == base:
        return
    if gh.gh(["pr", "edit", number, "--base", base], ctx.log, cwd=repo).rc != 0:
        raise core.Fail(f"pr-edit {tt}")


def _head(ctx, repo, wt):
    with gitops.lock(repo):
        return ctx.run(["git", "-C", wt, "rev-parse", "HEAD"]).out.strip()


def step(ctx):
    args = list(ctx.args)
    apply = args[1:] == ["--apply"]
    if not args or args[0].startswith("-") or len(args) > (2 if apply else 1):
        raise core.Fail(USAGE)
    base = args[0]
    repo = runfolder.run_info(ctx.run_dir)["Repo"]
    git = gitops.Git(repo, ctx.log)
    git.fetch()
    if not on_origin(git, base):
        raise core.Fail(f"no-base {base}")
    tip = git.tip(f"refs/remotes/origin/{base}")
    unstarted, started, behind = plan(ctx.run_dir, git, tip)
    names = lambda ts: ",".join(t[0] for t in ts) or "none"  # noqa: E731
    counts = f"{len(unstarted)} unstarted {len(behind)} started unstarted={names(unstarted)} started={names(behind)}"
    ctx.note(f"base {base} at {tip}: {counts}")
    if not apply:
        return "catch-up", counts
    record(ctx.run_dir, base)
    conflicts = []
    for tt, path, branch in started:
        files = catch_up(ctx, git, repo, tt, branch, base) if (tt, path, branch) in behind else None
        if files:
            conflicts.append(f"{tt}: {' '.join(files)}")
        else:
            retarget(ctx, repo, tt, path, base)
    if conflicts:
        raise core.Fail(f"conflict {'; '.join(conflicts)}")
    return "caught-up", f"base={base} {counts}"


if __name__ == "__main__":
    core.main("catch_up", step, takes_id=False, usage="<new-base> [--apply]")
