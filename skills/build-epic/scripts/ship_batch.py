#!/usr/bin/env python3
"""Ships every ready ticket into the integration branch as one batch, with one Checks run: build-epic step 7.

Usage: python3 -I ship_batch.py <run>

Integration-branch mode only: when the run's base (`decisions.md`'s `base:`) is the default branch it fails
`FAIL run not-integration` and touches nothing, since the repo's gate judges each PR into the default branch.

A ticket is ready when its `Status:` is `building`, its `PR:` reads OPEN, its review is saved (the reviewed head
`ship_ticket.py merge` reads, `<run>/logs/PR<n>-review.json`), Ticket CI shows no red check (unless `decisions.md`
says `ci: off`), and `queue.json` does not hold it `red` at the same head. Ready tickets go in ticket-number order.
Each is first checked against the base tip the batch starts from (`steplib/ancestry.py`'s guards, pushing nothing): a
head whose tree is not the reviewed tree (`tree-mismatch`), that lacks the base's content (`reviewed-head-lacks-base`,
`task_merge_method` `squash` only) or whose reviewed head git does not know (`reviewed-unknown`) is **sent back**: left
as it is, never reset or rewritten, for a catch-up and a new review.

The rest are merged as they are (`git merge --no-ff`, no rebase) in that order into a scratch worktree
`<run>/worktrees/batch` on the start tip, and `map.md`'s Checks run once on the combined tree. A ticket whose head
conflicts with the ones before it is a **conflict** and ends the batch there. Green: each ticket lands through
`ship_ticket.py`'s `merge` (its guard and merge rule unchanged), with the batch's suite results saved as its
`reports/<TT>.checks.json` and its ancestry recorded against the start tip. Red: a linear prefix search reruns the
Checks on the first 1, 2, ... tickets until the first red one; the green prefix lands, that ticket is marked `red`
with its suite output in `<run>/logs/batch-<TT>-red.log`, and the tickets after it wait for the next batch.

State lives in `<run>/queue.json`: `{base, start_tip, state, tickets: [{ticket, pr, head, status, reason, log}]}`,
`state` `checking`, `landing` or `done`, each ticket `queued`, `landing`, `landed`, `in-review`, `failed`, `red`,
`conflict`, `sent-back` or `waiting`. A `red` ticket stays held, carried from batch to batch, until its head changes.
A waiting ticket's next batch starts on a base its landed tickets moved on: unless it already holds their content, it
is sent back `reviewed-head-lacks-base` to catch up and be reviewed again. `resume.py` settles a batch that stopped
before `done`.

Prints `OK batch landed <TT,...> suites=<n> [sent-back=<TT:reason,...>]`, `OK batch empty` (no ticket ready), or
`FAIL batch <reason> (log: ...)`: `red <TT>` (the first red ticket), `conflict <TT>` (its head conflicts with the
tickets before it), `<ship_ticket.py merge's reason> <TT>` (a landing refused or failed: the tickets after it wait),
`in-review <TT>` (the merge rule waits on a human: the tickets after it wait), `pr-view`, `ticket-ci`,
`git-<cmd>`, or `FAIL run not-integration`.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ship_ticket import Ship  # noqa: E402
from steplib import ancestry, checks, ci, core, gitops, merge, runfolder  # noqa: E402

GUARDS = ("tree-mismatch", "reviewed-head-lacks-base", "reviewed-unknown")


def queue_path(run):
    return core.resolve(run) / "queue.json"


def load(run):
    """`<run>/queue.json`, or None when there is none."""
    path = queue_path(run)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def save(run, queue):
    path = queue_path(run)
    tmp = path.with_name(".queue.json.tmp")
    tmp.write_text(json.dumps(queue, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


def integration(run):
    """True when the run's base is a branch other than its default branch."""
    return ci.base(run) != runfolder.run_info(run)["Default"]


class Batch:
    def __init__(self, ctx):
        self.ctx, self.run = ctx, ctx.run_dir
        info = runfolder.run_info(self.run)
        self.repo, self.default = core.resolve(info["Repo"]), info["Default"]
        self.base = ci.base(self.run, self.default)
        self.git = gitops.Git(self.repo, ctx.log)
        self.scratch = self.run / "worktrees" / "batch"
        self.queue = None

    def ship(self, tt):
        """`ship_ticket.py`'s `Ship` for ticket `tt`, logging to this step's log."""
        return Ship(core.Context(self.ctx.step, self.run, tt, [], self.ctx.log, self.ctx.log_path))

    def held_red(self):
        """{ticket: entry} of the tickets the last batch held `red`."""
        old = load(self.run) or {}
        return {t["ticket"]: t for t in old.get("tickets", []) if t.get("status") == "red"}

    def ready(self):
        """(the ready tickets in ticket-number order, the ones still held `red` at the same head), as queue entries."""
        red, found, held = self.held_red(), [], []
        for path in sorted((self.run / "tickets").glob("*.md"), key=lambda p: p.name):
            tt = path.name.split("-", 1)[0]
            number = ci.pr_number(runfolder.get(path, "PR"))
            branch = runfolder.get(path, "Branch")
            if runfolder.get(path, "Status") != "building" or not number or not branch:
                continue
            if merge.view(number, self.ctx.log, self.repo).get("state") != "OPEN":
                continue
            if not (self.run / "logs" / f"PR{number}-review.json").is_file():
                continue
            red_ci = ci.enabled(self.run) and any(c.get("bucket") == "fail"
                                                  for c in ci.checks(number, self.ctx.log, self.repo))
            if red_ci:
                self.ctx.note(f"{tt}: Ticket CI is red; not batched")
                continue
            head = self.git.tip(f"refs/remotes/origin/{branch}")
            if tt in red and red[tt].get("head") == head:
                self.ctx.note(f"{tt}: red at {head} in the last batch; not batched until its head changes")
                held.append(red[tt])
                continue
            found.append({"ticket": tt, "pr": number, "branch": branch, "head": head, "status": "queued"})
        return found, held

    def write(self, state=None):
        if state:
            self.queue["state"] = state
        save(self.run, self.queue)

    def send_back(self, entries, start_tip):
        """Marks `sent-back` each entry whose head fails the ancestry guards against `start_tip`; returns the rest."""
        squash = (merge.setting(self.repo, "task_merge_method", self.ctx.log) or "squash") == "squash"
        kept = []
        for e in entries:
            try:
                reviewed = self.ship(e["ticket"]).reviewed(e["pr"], None)
                if squash:
                    ancestry.check(self.git, start_tip, e["head"], reviewed, self.base)
                elif self.git.tree(e["head"]) != self.git.tree(reviewed):
                    raise core.Fail("tree-mismatch")
            except core.Fail as f:
                if f.reason not in GUARDS:
                    raise
                e.update(status="sent-back", reason=f.reason)
                self.ctx.note(f"{e['ticket']}: sent back, {f.reason}")
                continue
            kept.append(e)
        return kept

    def combine(self, entries, start_tip):
        """Merges each entry's head into the scratch worktree on `start_tip`, in order. Returns the combined commit
        after each merged entry; an entry that conflicts is marked `conflict`, and the ones after it `waiting`."""
        self.git.worktree_remove(self.scratch)
        self.git.worktree_add(self.scratch, ref=start_tip, detach=True)
        tips = []
        for i, e in enumerate(entries):
            res = self.git._git("merge", "--no-ff", "--no-edit", "-m", f"batch: {e['ticket']}", e["head"],
                                check=False, cwd=self.scratch)
            if res.rc != 0:
                self.git._git("merge", "--abort", check=False, cwd=self.scratch)
                e.update(status="conflict", reason="conflict")
                for later in entries[i + 1:]:
                    later["status"] = "waiting"
                break
            tips.append(self.git._git("rev-parse", "HEAD", cwd=self.scratch).out.strip())
        return tips

    def check(self, sha):
        """Runs the Checks on `sha` in the scratch worktree; returns the suites."""
        self.git._git("checkout", "-q", "--detach", sha, cwd=self.scratch)
        self.suites += 1
        return checks.run_all(checks.commands(self.run / "map.md"), self.scratch, self.ctx.log, self.run)

    def first_red(self, tips):
        """(index of the first red ticket, its suites, the last green prefix's suites) by a linear prefix search;
        the whole batch, `tips[-1]`, is known red."""
        green = None
        for i, sha in enumerate(tips[:-1]):
            suites = self.check(sha)
            if suites[-1].rc != 0:
                return i, suites, green
            green = suites
        return len(tips) - 1, self.last, green

    def step(self):
        self.git.fetch()
        start_tip = self.git.tip(f"refs/remotes/origin/{self.base}")
        entries, held = self.ready()
        if not entries:
            return "empty"
        self.queue = {"base": self.base, "start_tip": start_tip, "state": "checking", "tickets": entries + held}
        self.write()
        batch = self.send_back(entries, start_tip)
        self.write()
        self.suites, red, conflict = 0, None, None
        try:
            tips = self.combine(batch, start_tip)
            conflict = next((e for e in batch if e["status"] == "conflict"), None)
            batch = batch[:len(tips)]
            green = None
            if tips:
                self.last = self.check(tips[-1])
                green = self.last
                if self.last[-1].rc != 0:
                    i, suites, green = self.first_red(tips)
                    red = batch[i]
                    log = self.run / "logs" / f"batch-{red['ticket']}-red.log"
                    log.write_text("".join(f"$ {s.command}\n{s.out}[rc {s.rc}]\n" for s in suites), encoding="utf-8")
                    red.update(status="red", reason=f"red {suites[-1].command}", log=str(log))
                    for later in batch[i + 1:]:
                        later["status"] = "waiting"
                    batch = batch[:i]
        finally:
            self.git.worktree_remove(self.scratch)
        self.write("landing")
        landed = self.land(batch, start_tip, green)
        self.write("done")
        if red:
            raise core.Fail(f"red {red['ticket']}")
        if conflict:
            raise core.Fail(f"conflict {conflict['ticket']}")
        back = [f"{e['ticket']}:{e['reason']}" for e in self.queue["tickets"] if e["status"] == "sent-back"]
        details = [",".join(landed) or "none", f"suites={self.suites}"]
        details += [f"sent-back={','.join(back)}"] if back else []
        return "landed", " ".join(details)

    def land(self, batch, start_tip, suites):
        """Lands each entry in order through `Ship.merge`; a refusal or a wait on a human stops the ones after it."""
        landed = []
        for i, e in enumerate(batch):
            e["status"] = "landing"
            self.write()
            try:
                state, _ = self.ship(e["ticket"]).merge(batch=(start_tip, suites))
            except core.Fail as f:
                e.update(status="failed", reason=f.reason)
                for later in batch[i + 1:]:
                    later["status"] = "waiting"
                self.write("done")
                raise core.Fail(f"{f.reason} {e['ticket']}")
            if state == "in-review":
                e["status"] = "in-review"
                for later in batch[i + 1:]:
                    later["status"] = "waiting"
                self.write("done")
                raise core.Fail(f"in-review {e['ticket']}")
            e["status"] = "landed"
            landed.append(e["ticket"])
            self.write()
        return landed


def step(ctx):
    if ctx.args:
        raise core.Fail("usage: ship_batch.py <run>")
    if not integration(ctx.run_dir):
        raise core.Fail("not-integration")
    return Batch(ctx).step()


if __name__ == "__main__":
    args = sys.argv[1:]
    try:
        batch = len(args) == 1 and integration(core.resolve(args[0]))
    except (OSError, KeyError, core.Fail):
        batch = False
    # The batch's lines carry the id `batch`; a refusal before it starts is the run's.
    core.main("ship_batch", step, takes_id=batch, argv=[args[0], "batch"] if batch else args)
