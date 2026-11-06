#!/usr/bin/env python3
"""Ships one ticket's PR into the base branch: build-epic step 7.

Usage: python3 -I ship_ticket.py <run> <TT> open
       python3 -I ship_ticket.py <run> <TT> merge

The base is `decisions.md`'s `base:` line when it names a branch, else the run's default branch.

`open` adds the ticket's worktree `<run>/worktrees/TT` on its branch when it is missing, then makes the ticket PR
ready with the ticket's title and body (`steplib/prbody.py`, kept in `<run>/logs/TT-pr-body.md`): it edits the
ticket's `PR:` (or the open PR from the ticket branch) and marks it ready, or opens one and records it as the
ticket's `PR:` line. Prints `OK TT open pr=<n>`.

`merge` merges the ticket PR at the ticket branch's tip on origin by the merge rule (`merge_command` Setting), then
sets the ticket `merged`, removes its worktree and local branch, and, when the base is the default branch, closes
the ticket's issues. When the rule refuses for want of a human approval (its output names an approval, a required
review or a code owner), it sets the ticket `in-review` and removes the worktree instead. Prints
`OK TT merged pr=<n>` or `OK TT in-review pr=<n>`, or `FAIL TT <reason> (log: ...)`: `merge`, `head-moved`,
`no-pr`, `pr-edit`, `pr-ready`, `pr-create`.
"""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import ci, core, gh, gitops, merge, prbody, runfolder, tracker  # noqa: E402

USAGE = "ship_ticket.py <run> <TT> open|merge"
URL = re.compile(r"https?://\S+/pull/(\d+)")
WAITS_ON_HUMAN = re.compile(r"approv|review required|required review|code ?owner", re.IGNORECASE)


def base_branch(run, default):
    """`decisions.md`'s `base:` when it names a branch, else `default`."""
    path = run / "decisions.md"
    value = runfolder.get(path, "base") if path.exists() else None
    value = (value or "").strip().strip("`")
    return default if value.lower() in ("", "null", "none", "default", "~") else value


class Ship:
    def __init__(self, ctx):
        self.ctx, self.run = ctx, ctx.run_dir
        info = runfolder.run_info(self.run)
        self.repo, self.default = core.resolve(info["Repo"]), info["Default"]
        self.base = base_branch(self.run, self.default)
        self.ticket = runfolder.ticket_file(self.run, ctx.id)
        self.branch = runfolder.get(self.ticket, "Branch")
        if not self.branch:
            raise core.Fail("ticket-no-branch")
        self.worktree = self.run / "worktrees" / ctx.id
        self.git = gitops.Git(self.repo, ctx.log)

    def gh(self, args, what):
        res = gh.gh(args, self.ctx.log, cwd=self.repo)
        if res.rc != 0:
            raise core.Fail(what)
        return res

    def find_open(self):
        """(number, url) of the open PR from the ticket branch into the base, or None."""
        res = self.gh(["pr", "list", "--head", self.branch, "--base", self.base, "--state", "open",
                       "--json", "number,url"], "pr-list")
        try:
            prs = json.loads(res.out or "[]")
        except ValueError:
            raise core.Fail("pr-list")
        return (str(prs[0]["number"]), prs[0].get("url") or f"#{prs[0]['number']}") if prs else None

    def open(self):
        self.git.fetch()
        if not self.worktree.is_dir():
            self.git.worktree_add(self.worktree, ref=f"origin/{self.branch}", branch=self.branch)
        body_file = self.run / "logs" / f"{self.ctx.id}-pr-body.md"
        body_file.write_text(prbody.ticket_body(self.run, self.ticket))
        title = ci.title(self.ticket)
        line = runfolder.get(self.ticket, "PR")
        found = (ci.pr_number(line), line) if ci.pr_number(line) else self.find_open()
        if found:
            number, url = found
            self.gh(["pr", "edit", number, "--title", title, "--body-file", body_file], "pr-edit")
            res = gh.gh(["pr", "ready", number], self.ctx.log, cwd=self.repo)
            if res.rc != 0 and "already" not in (res.out + res.err).lower():
                raise core.Fail("pr-ready")
        else:
            res = self.gh(["pr", "create", "--base", self.base, "--head", self.branch, "--title", title,
                           "--body-file", body_file], "pr-create")
            m = URL.search(res.out)
            if not m:
                raise core.Fail("pr-create")
            number, url = m.group(1), m.group(0)
        if not line:
            runfolder.set_header(self.ticket, "PR", url)
        return "open", f"pr={number}"

    def finish(self):
        """Removes the ticket's worktree and its local branch."""
        self.git.worktree_remove(self.worktree)
        self.git.branch_delete(self.branch)

    def merge(self):
        line = runfolder.get(self.ticket, "PR")
        number = ci.pr_number(line)
        if not number:
            raise core.Fail("no-pr")
        command = merge.setting(self.repo, "merge_command", self.ctx.log)
        self.git.fetch()
        sha = self.git.tip(f"origin/{self.branch}")
        self.ctx.log.flush()
        start = os.path.getsize(self.ctx.log_path)
        try:
            merge.merge(number, sha, self.repo, self.ctx.log, command)
        except core.Fail as e:
            self.ctx.log.flush()
            with open(self.ctx.log_path, errors="replace") as f:
                f.seek(start)
                said = f.read()
            if e.reason != "merge" or not WAITS_ON_HUMAN.search(said):
                raise
            runfolder.set_header(self.ticket, "Status", "in-review")
            self.finish()
            return "in-review", f"pr={number}"
        runfolder.set_header(self.ticket, "Status", "merged")
        self.finish()
        if self.base == self.default:
            issues = tracker.Tracker(self.run, self.repo, self.ctx.log)
            for n in prbody.issues(self.ticket):
                issues.close(n, f"Shipped in {line}.")
        return "merged", f"pr={number}"


def step(ctx):
    if len(ctx.args) != 1 or ctx.args[0] not in ("open", "merge"):
        raise core.Fail(f"usage: {USAGE}")
    ship = Ship(ctx)
    return ship.open() if ctx.args[0] == "open" else ship.merge()


if __name__ == "__main__":
    core.main("ship_ticket", step, usage="open|merge")
