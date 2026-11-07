"""The git operations steps share, each under a per-repo lock so two steps never run git in one repo at once.

The lock is `<git common dir>/build-epic.lock`, shared by the main checkout and all its worktrees. A failed command
raises `core.Fail("git-<subcommand>")`, its output in the step's log.
"""
import contextlib
import subprocess

from . import core, gh


def common_dir(repo):
    """The repo's git common dir, absolute and resolved (the same for every worktree of one repo)."""
    out = subprocess.run(["git", "-C", str(repo), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    if out.returncode != 0:
        raise core.Fail(f"not-a-repo: {repo}")
    return core.resolve(out.stdout.strip())


@contextlib.contextmanager
def lock(repo):
    """Holds the repo's git lock for the `with` block."""
    with core.file_lock(common_dir(repo) / "build-epic.lock"):
        yield


class Git:
    """Git in one repo (any of its worktrees), logging to `log`."""

    def __init__(self, repo, log):
        self.repo, self.log = core.resolve(repo), log

    def _git(self, *args, check=True, cwd=None):
        with lock(self.repo):
            res = core.run(["git", "-C", cwd or self.repo, *args], self.log)
        if check and res.rc != 0:
            raise core.Fail(f"git-{args[0]}")
        return res

    def fetch(self, remote="origin", *refs):
        self._git("fetch", "--prune", remote, *refs)

    def worktree_add(self, path, ref=None, branch=None, detach=False):
        """Adds a worktree at `path` on `ref`; `branch` creates (or resets) that branch there."""
        args = ["worktree", "add"]
        if branch:
            args += ["-B", branch]
        if detach:
            args.append("--detach")
        args.append(str(path))
        if ref:
            args.append(ref)
        self._git(*args)

    def worktree_remove(self, path):
        """Removes the worktree at `path`, changes and all; a worktree already gone is fine."""
        res = self._git("worktree", "remove", "--force", str(path), check=False)
        if res.rc != 0:
            self._git("worktree", "prune")
            listed = self._git("worktree", "list", "--porcelain").out
            if f"worktree {core.resolve(path)}\n" in listed + "\n":
                raise core.Fail("git-worktree")

    def branch_delete(self, name):
        """Deletes local branch `name`; a branch already gone is fine."""
        if self._git("rev-parse", "--verify", "--quiet", f"refs/heads/{name}", check=False).rc == 0:
            self._git("branch", "-D", name)

    def tip(self, ref="HEAD"):
        return self._git("rev-parse", "--verify", f"{ref}^{{commit}}").out.strip()

    def tree(self, ref="HEAD"):
        return self._git("rev-parse", "--verify", f"{ref}^{{tree}}").out.strip()

    def is_ancestor(self, ancestor, descendant="HEAD"):
        res = self._git("merge-base", "--is-ancestor", ancestor, descendant, check=False)
        if res.rc not in (0, 1):
            raise core.Fail("git-merge-base")
        return res.rc == 0

    def push(self, *args):
        """`git push <args>` with the retry on transient failures, under the lock. Returns the `Result`."""
        with lock(self.repo):
            return gh.push(list(args), self.log, cwd=self.repo)
