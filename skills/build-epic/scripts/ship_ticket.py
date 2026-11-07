#!/usr/bin/env python3
"""Ships one ticket's PR into the base branch: build-epic step 7.

Usage: python3 -I ship_ticket.py <run> <TT> open [--body-file <file>]
       python3 -I ship_ticket.py <run> <TT> merge [--reviewed <sha>]

The base a new PR opens against is `decisions.md`'s `base:` line when it names a branch, else the run's default branch.

`open` adds the ticket's worktree `<run>/worktrees/TT` on its branch when it is missing, then makes the ticket PR
ready with the ticket's title and body (`steplib/prbody.py`, kept in `<run>/logs/TT-pr-body.md`): the orchestrator's
`--body-file` text, filling every section of the repo's PR template, then the ticket's tasks and `Closes #n`. When the
repo has a template and a section of it is missing, blank, `TODO`, `N/A` or only unchecked boxes, it fails
`empty-template-section <sections>` and sends nothing. Otherwise it edits the ticket's `PR:` (or the open PR from the
ticket branch) and marks it ready, or opens one and records it as the ticket's `PR:` line. Prints `OK TT open pr=<n>`.

`merge` reads the PR's real base (`baseRefName`). Into the default branch with the `merge_command` Setting unset, a
repo with a `.review-gate.yml` fails `no-merge-rule`: a bare `gh pr merge` would land around the repo's gate. Otherwise
it merges the ticket PR at the ticket branch's tip on origin by the merge rule, then sets the ticket `merged`, removes
its worktree and local branch, and, when the PR's base is the default branch, closes the ticket's issues (into any other
base they stay open). When the `merge_command` rule refuses for want of a human approval (its own output names an
approval, a required review or a code owner), it sets the ticket `in-review` and removes the worktree instead.

Into any other base, with the `task_merge_method` Setting `squash` (the default), it first records the integration
ancestry the squash merges dropped (`steplib/ancestry.py`): a `git merge -s ours origin/<base>` commit on the ticket
branch, pushed, and the merge is pinned to it. Only when the reviewed head contains the base tip
(`reviewed-head-lacks-base`) and the ticket's tree is the reviewed tree (`tree-mismatch`); either refusal merges and
pushes nothing. The reviewed head is `--reviewed <sha>` (the reviewed commit holding the base tip, e.g. the hub task's
head), else the commit the ticket PR's review was posted on (`<run>/logs/PR<n>-review.json`), else it fails
`no-review`. With `merge`, no ancestry step runs. A ticket merged into such a base labels its issues `merged-to-<base>`.

Prints `OK TT merged pr=<n>` or `OK TT in-review pr=<n>`, or `FAIL TT <reason> (log: ...)`: `wrong-base <branch>` (a PR
into neither the run's base nor a ticket branch), `merge`, `head-moved`,
`no-pr`, `no-merge-rule`, `no-review`, `reviewed-unknown`, `reviewed-head-lacks-base`, `tree-mismatch`, `pr-view`,
`pr-edit`, `pr-ready`, `pr-create`, `empty-template-section <sections>`, `no-body-file`.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import ancestry, ci, core, gh, gitops, merge, prbody, runfolder, tracker  # noqa: E402

USAGE = "ship_ticket.py <run> <TT> open [--body-file <file>] | merge [--reviewed <sha>]"
GATE = ".review-gate.yml"
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

    def open(self, lead_file=None):
        lead = ""
        if lead_file:
            path = core.resolve(lead_file)
            if not path.is_file():
                raise core.Fail(f"no-body-file: {path}")
            lead = path.read_text()
        self.git.fetch()
        if not self.worktree.is_dir():
            self.git.worktree_add(self.worktree, ref=f"origin/{self.branch}", branch=self.branch)
        body = prbody.ticket_body(self.run, self.ticket, lead)
        body_file = self.run / "logs" / f"{self.ctx.id}-pr-body.md"
        body_file.write_text(body)
        template = prbody.template(self.worktree)
        empty = prbody.empty_sections(template, body) if template is not None else []
        if empty:
            self.ctx.note(f"the PR template's sections left empty: {', '.join(empty)}; body in {body_file}")
            raise core.Fail(f"empty-template-section {', '.join(empty)}")
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

    def gated(self):
        """True when the repo has a `.review-gate.yml`, on the default branch's tip on origin or in its checkout."""
        res = self.git._git("cat-file", "-e", f"origin/{self.default}:{GATE}", check=False)
        return res.rc == 0 or (self.repo / GATE).is_file()

    def finish(self):
        """Removes the ticket's worktree and its local branch."""
        self.git.worktree_remove(self.worktree)
        self.git.branch_delete(self.branch)

    def reviewed(self, number, given):
        """The reviewed head: `given`, else the commit the PR's posted review was on."""
        if given:
            return given
        path = self.run / "logs" / f"PR{number}-review.json"
        try:
            commit = json.loads(path.read_text()).get("commit_id") if path.is_file() else None
        except (ValueError, AttributeError):
            commit = None
        if not commit:
            self.ctx.note(f"no reviewed head: pass --reviewed <sha>, or post the ticket's review ({path} is missing)")
            raise core.Fail("no-review")
        return commit

    def merge(self, reviewed=None):
        line = runfolder.get(self.ticket, "PR")
        number = ci.pr_number(line)
        if not number:
            raise core.Fail("no-pr")
        command = merge.setting(self.repo, "merge_command", self.ctx.log)
        self.git.fetch()
        sha = self.git.tip(f"origin/{self.branch}")
        into = merge.view(number, self.ctx.log, self.repo).get("baseRefName")
        if not into:
            raise core.Fail("pr-view")
        if not command and into == self.default and self.gated():
            self.ctx.note(f"{GATE} gates {into}, and the merge_command Setting is unset: set it to the repo's merge rule")
            raise core.Fail("no-merge-rule")
        if into != self.default and (merge.setting(self.repo, "task_merge_method", self.ctx.log) or "squash") == "squash":
            sha = ancestry.record(self.git, self.branch, into, self.reviewed(number, reviewed))
        try:
            merge.merge(number, sha, self.repo, self.ctx.log, command)
        except core.Fail as e:
            # Only the repo's own rule answers "waits on a human"; a bare gh merge's refusal is a failure.
            if not command or e.reason != "merge" or not WAITS_ON_HUMAN.search(getattr(e, "said", "")):
                raise
            runfolder.set_header(self.ticket, "Status", "in-review")
            self.finish()
            return "in-review", f"pr={number}"
        runfolder.set_header(self.ticket, "Status", "merged")
        self.finish()
        issues = tracker.Tracker(self.run, self.repo, self.ctx.log)
        for n in prbody.issues(self.ticket):
            if into == self.default:
                issues.close(n, f"Shipped in {line}.")
            else:
                issues.add_label(n, f"merged-to-{into}")
        return "merged", f"pr={number}"


def step(ctx):
    args = list(ctx.args)
    if args[:1] == ["open"] and len(args) in (1, 3) and (len(args) == 1 or args[1] == "--body-file"):
        return Ship(ctx).open(args[2] if len(args) == 3 else None)
    if args[:1] == ["merge"] and len(args) in (1, 3) and (len(args) == 1 or args[1] == "--reviewed"):
        return Ship(ctx).merge(args[2] if len(args) == 3 else None)
    raise core.Fail(f"usage: {USAGE}")


if __name__ == "__main__":
    core.main("ship_ticket", step, usage="open [--body-file <file>] | merge [--reviewed <sha>]")
