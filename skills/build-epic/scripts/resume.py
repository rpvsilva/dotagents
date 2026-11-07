#!/usr/bin/env python3
"""Resumes a run folder that already exists: SKILL.md's resume rules in one call.

Usage: python3 -I resume.py <run>

- An `in-review` ticket whose PR merged becomes `merged`.
- An `in-progress` task with no `PR:` goes back to `ready`, its worktree and branch removed.
- A task not `done` whose PR merged becomes `done`, its worktree and branch removed, then Ticket CI: the draft ticket
  PR is opened when the ticket has none (unless `decisions.md` says `ci: off`).
- A task with an open PR is left as it is, for the orchestrator to take to Merge.
- When `dispatcher.sha` exists, `worktrees/dispatcher/` is re-created at that SHA if it is gone (or moved back to it),
  never at a newer commit.

Prints `OK run resumed <n> actions`, `n` counting each status set and each dispatcher worktree fixed; a rerun prints
`0 actions`. Fails `pr-view` when a PR's state cannot be read, `bad-dispatcher-sha <value>` on a pin that is no commit.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import ci, core, gitops, merge, runfolder  # noqa: E402
from start_task import names, slug  # noqa: E402


class Resume:
    def __init__(self, ctx):
        self.ctx, self.run = ctx, ctx.run_dir
        self.actions = 0
        self._info = None

    @property
    def info(self):
        if self._info is None:
            self._info = runfolder.run_info(self.run)
            self._info["Repo"] = core.resolve(self._info["Repo"])
        return self._info

    def git(self):
        return gitops.Git(self.info["Repo"], self.ctx.log)

    def merged(self, pr):
        return merge.view(pr, self.ctx.log, self.info["Repo"]).get("state") == "MERGED"

    def set_status(self, path, status):
        self.ctx.note(f"{path.name}: {runfolder.get(path, 'Status')} -> {status}")
        runfolder.set_header(path, "Status", status)
        self.actions += 1

    def tickets(self):
        for path in sorted((self.run / "tickets").glob("*.md")):
            pr = ci.pr_number(runfolder.get(path, "PR"))
            if runfolder.get(path, "Status") == "in-review" and pr and self.merged(pr):
                self.set_status(path, "merged")

    def drop_worktree(self, task):
        """Removes the task's worktree and its `task/<ticket-slug>/<NN>` branch, either already gone being fine."""
        tt, nn = names(task)
        ticket = runfolder.ticket_file(self.run, tt)
        git = self.git()
        git.worktree_remove(self.run / "worktrees" / f"{tt}-{nn}")
        branch = runfolder.get(ticket, "Branch")
        if branch:
            git.branch_delete(f"task/{slug(branch)}/{nn}")
        return ticket

    def tasks(self):
        for path in sorted((self.run / "tasks").glob("*.md")):
            status = runfolder.get(path, "Status")
            pr = ci.pr_number(runfolder.get(path, "PR"))
            if status == "done":
                continue
            if not pr:
                if status == "in-progress":
                    self.drop_worktree(path)
                    self.set_status(path, "ready")
            elif self.merged(pr):
                ticket = self.drop_worktree(path)
                self.set_status(path, "done")
                if ci.enabled(self.run):
                    ci.open_draft(ticket, self.info["Default"], self.ctx.log, self.info["Repo"])

    def dispatcher(self):
        pin = self.run / "dispatcher.sha"
        if not pin.exists():
            return
        sha = pin.read_text().strip()
        if not re.fullmatch(r"[0-9a-f]{7,64}", sha):
            raise core.Fail(f"bad-dispatcher-sha {sha.split()[0] if sha.split() else 'empty'}")
        git = self.git()
        if self.ctx.run(["git", "-C", self.info["Repo"], "cat-file", "-e", f"{sha}^{{commit}}"]).rc != 0:
            git.fetch()
        sha = git.tip(sha)
        wt = self.run / "worktrees" / "dispatcher"
        if wt.is_dir():
            if gitops.Git(wt, self.ctx.log).tip() == sha:
                return
            with gitops.lock(self.info["Repo"]):
                if self.ctx.run(["git", "-C", wt, "checkout", "-q", "--detach", sha]).rc != 0:
                    raise core.Fail("git-checkout")
        else:
            git.worktree_remove(wt)  # prunes a registration left by a worktree deleted by hand
            wt.parent.mkdir(parents=True, exist_ok=True)
            git.worktree_add(wt, sha, detach=True)
        self.ctx.note(f"dispatcher worktree at the pinned {sha}")
        self.actions += 1


def step(ctx):
    if ctx.args:
        raise core.Fail("usage: resume.py <run>")
    r = Resume(ctx)
    r.tickets()
    r.tasks()
    r.dispatcher()
    return "resumed", f"{r.actions} actions"


if __name__ == "__main__":
    if len(sys.argv) > 1 and not core.resolve(sys.argv[1]).is_dir():
        print(f"FAIL run no-run-folder: {core.resolve(sys.argv[1])}")  # core.main would create it for its log
        sys.exit(1)
    core.main("resume", step, takes_id=False)
