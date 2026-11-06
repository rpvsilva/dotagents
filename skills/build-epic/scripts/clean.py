#!/usr/bin/env python3
"""Cleans up after a run: its worktrees, its task branches and the run's labels on the issues it closed.

Usage: python3 -I clean.py <run>
Removes every worktree under `<run>/worktrees/` (`dispatcher/` included), every local `task/<ticket-slug>/*` branch
of the run's tickets, and the `in progress` and `blocked` labels from each closed issue of the run's tickets (unless
the `tracker` Setting names another tracker). Prints `OK run clean <n> removed`, `n` counting worktrees, branches and
labels. The run folder's files stay. Refused while a task is `in-progress`: `FAIL run in-progress <ids>`.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import core, gh, gitops, runfolder, tracker  # noqa: E402

LABELS = (tracker.IN_PROGRESS, "blocked")


def slug(branch):
    """The ticket slug: the ticket branch without its `feat/` prefix."""
    return branch[len("feat/"):] if branch.startswith("feat/") else branch


def task_id(path):
    """`TT-NN` from `TT-NN-<slug>.md`, or `TT-<name>` from an orchestrator task's `TT-<name>.md`."""
    parts = path.stem.split("-", 2)
    return "-".join(parts[:2]) if len(parts) > 1 and parts[1].isdigit() else path.stem


def git_out(ctx, repo, *args):
    """`git <args>` in `repo` under its lock; its stdout, or `FAIL git-<subcommand>`."""
    with gitops.lock(repo):
        res = ctx.run(["git", "-C", repo, *args])
    if res.rc != 0:
        raise core.Fail(f"git-{args[0]}")
    return res.out


def step(ctx):
    if ctx.args:
        raise core.Fail("usage: clean.py <run>")
    busy = [task_id(t) for t in sorted((ctx.run_dir / "tasks").glob("*.md"))
            if runfolder.get(t, "Status") == "in-progress"]
    if busy:
        raise core.Fail(f"in-progress {' '.join(busy)}")
    info = runfolder.run_info(ctx.run_dir)
    repo = info["Repo"]
    git = gitops.Git(repo, ctx.log)
    removed = 0

    # Worktrees git still knows under `<run>/worktrees/`, after dropping the ones whose folder is already gone.
    git_out(ctx, repo, "worktree", "prune")
    root = ctx.run_dir / "worktrees"
    for line in git_out(ctx, repo, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree ") and core.resolve(line[len("worktree "):]).parent == root:
            git.worktree_remove(line[len("worktree "):])
            removed += 1

    tickets = sorted((ctx.run_dir / "tickets").glob("*.md"))
    for ticket in tickets:
        branch = runfolder.get(ticket, "Branch")
        if not branch:
            continue
        for name in git_out(ctx, repo, "for-each-ref", "--format=%(refname:short)",
                            f"refs/heads/task/{slug(branch)}/").split():
            git.branch_delete(name)
            removed += 1

    kind = tracker.setting("build-epic", "tracker", repo, ctx.log)
    if kind and kind.lower() != "github":
        return "clean", f"{removed} removed tracker={kind}"
    t = None
    for ticket in tickets:
        for n in tracker.issues(runfolder.get(ticket, "Issue")):
            res = gh.gh(["issue", "view", str(n), "--json", "state,labels", "--jq", ".state, .labels[].name"], ctx.log,
                        cwd=repo)
            if res.rc != 0:
                raise core.Fail("gh-issue-view")
            lines = res.out.splitlines()
            if not lines or lines[0].strip() != "CLOSED":
                continue
            for label in LABELS:
                if label in (x.strip() for x in lines[1:]):
                    t = t or tracker.Tracker(ctx.run_dir, repo, ctx.log)
                    t.remove_label(n, label)
                    removed += 1
    return "clean", f"{removed} removed"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.stdout.write("FAIL run usage: clean.py <run>\n")
        sys.exit(2)
    # `run` is the id: the log is `<run>/logs/run-clean.log`, and a missing run folder is refused, not created.
    core.main("clean", step, argv=[sys.argv[1], "run", *sys.argv[2:]])
