"""A task as the finish, open-PR and fix-round steps see it: its file, its ticket's branch and its worktree.

Git runs in the task's worktree (`<run>/worktrees/TT-NN`, or `TT-tests`) without the repo's lock: these commands edit
only the worktree and its own branch. A merge of the ticket branch whose conflicts `union.resolve` settles (hub files
only, `test_command:` green) is committed and its files kept in `Task.union`; any other conflict raises
`core.Fail("conflict <files>")` and is left in progress, so whoever resolves it keeps the markers; `conclude_merge`
commits it once they are gone.
"""
import re

from . import core, gitops, runfolder, union


def title(path):
    """The task's title: its first `# ` line without the `TT-NN:` prefix, never re-cased."""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            return re.sub(r"^\d+-[A-Za-z0-9-]+:\s*", "", line[2:].strip())
    return path.stem


def clean(value):
    return (value or "").strip().strip("`").strip()


class Task:
    def __init__(self, ctx):
        self.ctx = ctx
        self.path = runfolder.task_file(ctx.run_dir, ctx.id)
        ticket = clean(runfolder.get(self.path, "Ticket")).split(" ")[0] or self.path.stem.split("-", 1)[0]
        self.ticket_path = runfolder.ticket_file(ctx.run_dir, ticket)
        self.ticket_branch = clean(runfolder.get(self.ticket_path, "Branch"))
        if not self.ticket_branch:
            raise core.Fail("no-ticket-branch")
        self.repo = core.resolve(runfolder.run_info(ctx.run_dir)["Repo"])
        parts = self.path.stem.split("-", 2)
        name = f"{parts[0]}-{parts[1]}" if len(parts) > 1 and parts[1].isdigit() else self.path.stem
        self.worktree = ctx.run_dir / "worktrees" / name
        self.upstream = f"origin/{self.ticket_branch}"
        self.union = []

    def git(self, *args, check=True):
        """`git <args>` in the worktree; a failure raises `Fail("git-<subcommand>")`."""
        res = core.run(["git", "-C", self.worktree, *args], self.ctx.log)
        if check and res.rc != 0:
            raise core.Fail(f"git-{args[0]}")
        return res

    def need_worktree(self):
        if not self.worktree.is_dir():
            raise core.Fail(f"no-worktree: {self.worktree}")

    def branch(self):
        name = self.git("rev-parse", "--abbrev-ref", "HEAD").out.strip()
        if name == "HEAD":
            raise core.Fail("detached-head")
        return name

    def head(self):
        return self.git("rev-parse", "HEAD").out.strip()

    def dirty(self):
        return bool(self.git("status", "--porcelain").out.strip())

    def merging(self):
        return self.git("rev-parse", "-q", "--verify", "MERGE_HEAD", check=False).rc == 0

    def fetch(self):
        gitops.Git(self.repo, self.ctx.log).fetch()

    def changed(self):
        """Files the task branch changes against the ticket branch (as fetched)."""
        out = self.git("diff", "--name-only", f"{self.upstream}...HEAD").out
        return [line for line in out.splitlines() if line]

    def unmerged(self):
        out = self.git("diff", "--name-only", "--diff-filter=U").out
        return sorted({line for line in out.splitlines() if line})

    def merge_upstream(self):
        """Merges the fetched ticket branch when HEAD lacks its tip. Returns True when it merged."""
        if gitops.Git(self.worktree, self.ctx.log).is_ancestor(self.upstream):
            return False
        res = self.git("merge", "--no-edit", self.upstream, check=False)
        if res.rc != 0:
            self.union = union.resolve(self.ctx.run_dir, self.worktree, self.ctx.log) or []
            if self.union:
                return True
            files = self.unmerged()
            if files:
                raise core.Fail(f"conflict {' '.join(files)}")
            raise core.Fail("git-merge")
        return True

    def conclude_merge(self):
        """Commits a merge left in progress, once no conflict marker is left in it."""
        self.git("add", "-A")
        check = self.git("diff", "--cached", "--check", check=False).out
        marked = sorted({line.split(":", 1)[0] for line in check.splitlines() if "conflict marker" in line})
        if marked:
            raise core.Fail(f"conflict {' '.join(marked)}")
        self.git("commit", "--no-edit")

    def refuse_unfinished(self):
        """Fails when the worktree holds uncommitted changes or a merge in progress."""
        if self.merging():
            raise core.Fail(f"conflict {' '.join(self.unmerged()) or 'merge-in-progress'}")
        if self.dirty():
            raise core.Fail("uncommitted: run finish_task.py first")
