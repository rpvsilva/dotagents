#!/usr/bin/env python3
"""Resumes a run folder that already exists: SKILL.md's resume rules in one call.

Usage: python3 -I resume.py <run>

- An `in-review` ticket whose PR merged becomes `merged`.
- An `in-progress` task whose branch head is on the local ticket branch (`merge_task.py` merged it, then stopped)
  becomes `done`, with the merge commit as its `Commit:` line, its worktree and branch removed.
- Any other `in-progress` task with a `Finished:` line (`finish_task.py` committed its work) is left as it is, for the
  orchestrator to take to Merge.
- An `in-progress` task with neither goes back to `ready`, its worktree and branch removed: its implementer had not
  reported.

Prints `OK run resumed <n> actions`, `n` counting each status set; a rerun prints `0 actions`. Fails `pr-view` when a
ticket PR's state cannot be read.
"""
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

    def landed(self, path):
        """The merge commit that landed the task's branch head on the local ticket branch, or None."""
        tt, nn = names(path)
        branch = runfolder.get(runfolder.ticket_file(self.run, tt), "Branch")
        git = self.git()
        task_ref, ticket_ref = f"refs/heads/task/{slug(branch or '')}/{nn}", f"refs/heads/{branch}"
        for ref in (task_ref, ticket_ref):
            if not branch or git._git("rev-parse", "--verify", "--quiet", ref, check=False).rc != 0:
                return None
        head = git.tip(task_ref)
        if not git.is_ancestor(head, ticket_ref):
            return None
        out = git._git("rev-list", "--first-parent", "--merges", "--parents", ticket_ref).out
        return next((line.split()[0] for line in out.splitlines() if head in line.split()[2:]), None)

    def tasks(self):
        for path in sorted((self.run / "tasks").glob("*.md")):
            if runfolder.get(path, "Status") != "in-progress":
                continue
            commit = self.landed(path)
            if commit:
                self.drop_worktree(path)
                runfolder.set_header(path, "Commit", commit)
                self.set_status(path, "done")
            elif not runfolder.get(path, "Finished"):
                self.drop_worktree(path)
                self.set_status(path, "ready")


def step(ctx):
    if ctx.args:
        raise core.Fail("usage: resume.py <run>")
    r = Resume(ctx)
    r.tickets()
    r.tasks()
    return "resumed", f"{r.actions} actions"


if __name__ == "__main__":
    if len(sys.argv) > 1 and not core.resolve(sys.argv[1]).is_dir():
        print(f"FAIL run no-run-folder: {core.resolve(sys.argv[1])}")  # core.main would create it for its log
        sys.exit(1)
    core.main("resume", step, takes_id=False)
