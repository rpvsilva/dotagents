"""Tests for `ship_batch.py`: ready tickets integrate into the integration branch as one batch, and `resume.py` on an
interrupted `queue.json`."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import runfolder  # noqa: E402
from testkit import SCRIPTS, Kit  # noqa: E402

BATCH = SCRIPTS / "ship_batch.py"
RESUME = SCRIPTS / "resume.py"
REPO = SCRIPTS.parents[2]
BASE = "integ"

# `gh pr view <n>` answers like GitHub: the head is the tip on origin of the branch `answers/pr-<n>-branch` names, the
# base `integ`, the state `answers/pr-<n>-state` (default OPEN). `gh pr merge <n>` squash-merges that branch into
# `integ` on origin and sets the state to MERGED. Every other call goes to the testkit stub.
GH = r'''#!{python} -IS
import os, sys
d = os.environ["GH_STUB_DIR"]
args = sys.argv[1:]
if args[:2] not in (["pr", "view"], ["pr", "merge"]):
    os.execv(sys.executable, [sys.executable, "-IS", {kit_gh!r}, *args])
import json, subprocess
with open(os.path.join(d, "gh.log"), "a", encoding="utf-8") as f:
    f.write(json.dumps(args) + "\n")
state = os.path.join(d, "answers", f"pr-{{args[2]}}-state")
branch = open(os.path.join(d, "answers", f"pr-{{args[2]}}-branch"), encoding="utf-8").read().strip()

def git(*a):
    return subprocess.run(["git", "--git-dir", {origin!r}, *a], capture_output=True, text=True,
                          encoding="utf-8", check=True).stdout.split("\n", 1)[0].strip()

if args[1] == "merge":  # a squash merge: the merged tree as one new commit on integ
    tree = git("merge-tree", "--write-tree", "integ", branch)
    git("update-ref", "refs/heads/integ", git("commit-tree", tree, "-p", "integ", "-m", "squash"))
    open(state, "w", encoding="utf-8").write("MERGED")
    sys.exit(0)
print(json.dumps({{"state": open(state, encoding="utf-8").read().strip() if os.path.exists(state) else "OPEN",
                  "headRefOid": git("rev-parse", branch), "baseRefName": "integ"}}))
'''

TICKET = ("# {tt}: ticket {tt}\nStatus: building\nBranch: feat/{tt}\nBlocked by: none\n"
          "PR: https://github.com/o/r/pull/{pr}\n")


class BatchTest(unittest.TestCase):
    """Three ticket branches cut from `integ`, each adding its own file, each PR reviewed at its head."""

    def setUp(self):
        self.kit = kit = Kit()
        self.addCleanup(kit.close)
        kit.env.pop("XDG_CONFIG_HOME", None)
        kit.make_origin()
        (kit.bin / "gh").rename(kit.bin / "gh-kit")
        (kit.bin / "gh").write_text(GH.format(python=sys.executable, kit_gh=str(kit.bin / "gh-kit"),
                                              origin=str(kit.origin)), encoding="utf-8")
        (kit.bin / "gh").chmod(0o755)
        (kit.home / ".agents" / "bin").mkdir(parents=True)
        (kit.home / ".agents" / "bin" / "settings").symlink_to(REPO / "scripts" / "settings.sh")
        kit.init_run()
        for d in ("tickets", "tasks", "worktrees"):
            (kit.run_dir / d).mkdir()
        (kit.run_dir / "decisions.md").write_text(f"# Decisions\nbase: {BASE}\n", encoding="utf-8")
        kit.answer("pr-checks", out="[]")
        # Each suite run counts itself; it goes red when the combined tree holds a `red.txt`.
        self.runs = kit.tmp / "suite-runs"
        (kit.run_dir / "map.md").write_text(
            f"# Map\n\n## Checks\n\n```\necho run >> {self.runs} && test ! -e red.txt\n```\n", encoding="utf-8")
        self.work = kit.tmp / "work"
        kit.git("worktree", "add", "-q", "--detach", str(self.work), "origin/main", cwd=kit.clone)
        self.push(self.git("rev-parse", "HEAD"), BASE)

    def git(self, *args):
        return self.kit.git(*args, cwd=self.work)

    def commit(self, path, text, on=None):
        if on:
            self.git("checkout", "-q", "--detach", on)
        (self.work / path).write_text(text, encoding="utf-8")
        self.git("add", path)
        self.git("commit", "-qm", f"add {path}")
        return self.git("rev-parse", "HEAD")

    def push(self, sha, branch):
        self.git("push", "-q", "-f", "origin", f"{sha}:refs/heads/{branch}")

    def tip(self, branch):
        return self.kit.git("--git-dir", str(self.kit.origin), "rev-parse", branch)

    def ticket(self, tt, path, text="x\n", on=f"origin/{BASE}"):
        """Ticket `tt`: branch `feat/<tt>` from `on`, one commit adding `path`, its PR `2<tt>` reviewed at its head."""
        self.git("fetch", "-q", "origin")
        head = self.commit(path, text, on=on)
        self.push(head, f"feat/{tt}")
        pr = f"2{tt[-1]}"
        (self.kit.answers / f"pr-{pr}-branch").write_text(f"feat/{tt}", encoding="utf-8")
        (self.kit.run_dir / "tickets" / f"{tt}-t.md").write_text(TICKET.format(tt=tt, pr=pr), encoding="utf-8")
        self.review(pr, head)
        return head

    def review(self, pr, sha):
        (self.kit.run_dir / "logs" / f"PR{pr}-review.json").write_text(json.dumps({"commit_id": sha}),
                                                                        encoding="utf-8")

    def batch(self):
        p = self.kit.step(BATCH, self.kit.run_dir)
        self.assertEqual(p.stdout.count("\n"), 1, p.stdout + p.stderr)
        return p.stdout.rstrip("\n")

    def merged_prs(self):
        return [c[2] for c in self.kit.gh_calls() if c[:2] == ["pr", "merge"]]

    def suite_runs(self):
        return len(self.runs.read_text(encoding="utf-8").splitlines()) if self.runs.exists() else 0

    def queue(self):
        return {t["ticket"]: t for t in json.loads((self.kit.run_dir / "queue.json").read_text())["tickets"]}

    def status(self, tt):
        return runfolder.get(self.kit.run_dir / "tickets" / f"{tt}-t.md", "Status")

    def test_three_green_tickets_land_with_one_suite_run(self):
        for tt in ("01", "02", "03"):
            self.ticket(tt, f"{tt}.txt")
        self.assertEqual(self.batch(), "OK batch landed 01,02,03 suites=1")
        self.assertEqual(self.suite_runs(), 1)
        self.assertEqual(self.merged_prs(), ["21", "22", "23"])
        self.assertEqual([self.status(tt) for tt in ("01", "02", "03")], ["merged"] * 3)
        self.assertEqual({tt: t["status"] for tt, t in self.queue().items()},
                         {"01": "landed", "02": "landed", "03": "landed"})
        for tt in ("01", "02", "03"):
            saved = json.loads((self.kit.run_dir / "reports" / f"{tt}.checks.json").read_text())
            self.assertEqual([s["rc"] for s in saved], [0])

    def test_with_the_second_red_the_first_lands_and_the_third_waits(self):
        self.ticket("01", "01.txt")
        self.ticket("02", "red.txt", "the suite fails on me\n")
        third = self.ticket("03", "03.txt")
        line = self.batch()
        self.assertRegex(line, r"^FAIL batch red 02 \(log: .+\)$")
        self.assertEqual(self.merged_prs(), ["21"])
        self.assertEqual([self.status(tt) for tt in ("01", "02", "03")], ["merged", "building", "building"])
        queue = self.queue()
        self.assertEqual({tt: t["status"] for tt, t in queue.items()}, {"01": "landed", "02": "red", "03": "waiting"})
        red_log = Path(queue["02"]["log"])
        self.assertIn("rc 1", red_log.read_text(encoding="utf-8"))
        self.assertTrue((self.kit.run_dir / "reports" / "01.checks.json").is_file())
        self.assertFalse((self.kit.run_dir / "reports" / "02.checks.json").exists())
        self.assertFalse((self.kit.run_dir / "reports" / "03.checks.json").exists())
        self.assertEqual(self.tip("feat/03"), third)  # never reset or re-cut
        # The next batch leaves the red ticket out until its head changes. The waiting one lacks what 01 brought to
        # the base: it is sent back, untouched, until it catches up and is reviewed again.
        self.assertEqual(self.batch(), "OK batch landed none suites=0 sent-back=03:reviewed-head-lacks-base")
        self.assertEqual(self.tip("feat/03"), third)
        self.git("fetch", "-q", "origin")
        self.git("checkout", "-q", "--detach", third)
        self.git("merge", "-q", "--no-edit", f"origin/{BASE}")
        caught_up = self.git("rev-parse", "HEAD")
        self.push(caught_up, "feat/03")
        self.review("23", caught_up)
        self.assertEqual(self.batch(), "OK batch landed 03 suites=1")
        self.assertEqual(self.merged_prs(), ["21", "23"])

    def test_a_reviewed_conflict_resolution_lands_as_reviewed(self):
        self.commit("shared.txt", "old\n", on=f"origin/{BASE}")
        self.push(self.git("rev-parse", "HEAD"), BASE)
        self.git("fetch", "-q", "origin")
        cut = self.git("rev-parse", "HEAD")
        # Ticket 02 changes shared.txt, the base changes it too, and 02 merges the base with a reviewed resolution.
        self.commit("shared.txt", "ticket\n", on=cut)
        mine = self.git("rev-parse", "HEAD")
        self.push(self.commit("shared.txt", "base\n", on=cut), BASE)
        self.git("fetch", "-q", "origin")
        self.git("checkout", "-q", "--detach", mine)
        merged = subprocess.run(["git", "merge", "-q", "--no-edit", f"origin/{BASE}"], cwd=self.work,
                                env=self.kit.env, capture_output=True)
        self.assertNotEqual(merged.returncode, 0)  # a real conflict
        (self.work / "shared.txt").write_text("ticket and base\n", encoding="utf-8")
        self.git("add", "shared.txt")
        self.git("commit", "-qm", "merge integ")
        resolved = self.git("rev-parse", "HEAD")
        self.push(resolved, "feat/02")
        (self.kit.answers / "pr-22-branch").write_text("feat/02", encoding="utf-8")
        (self.kit.run_dir / "tickets" / "02-t.md").write_text(TICKET.format(tt="02", pr="22"), encoding="utf-8")
        self.review("22", resolved)
        self.ticket("01", "01.txt")
        self.assertEqual(self.batch(), "OK batch landed 01,02 suites=1")
        landed = self.tip("feat/02")
        self.assertEqual(self.kit.git("--git-dir", str(self.kit.origin), "rev-parse", f"{landed}^{{tree}}"),
                         self.git("rev-parse", f"{resolved}^{{tree}}"))
        self.kit.git("--git-dir", str(self.kit.origin), "merge-base", "--is-ancestor", resolved, landed)
        self.assertEqual(self.merged_prs(), ["21", "22"])

    def test_a_head_moved_after_review_is_sent_back_and_never_rewritten(self):
        self.ticket("01", "01.txt")
        self.ticket("02", "02.txt")
        self.ticket("03", "03.txt")
        moved = self.commit("late.txt", "unreviewed\n", on="origin/feat/03")
        self.push(moved, "feat/03")
        self.assertEqual(self.batch(), "OK batch landed 01,02 suites=1 sent-back=03:tree-mismatch")
        self.assertEqual(self.merged_prs(), ["21", "22"])
        self.assertEqual(self.tip("feat/03"), moved)
        self.assertEqual(self.status("03"), "building")
        self.assertEqual(self.queue()["03"]["status"], "sent-back")

    def test_the_default_branch_is_refused(self):
        (self.kit.run_dir / "decisions.md").write_text("# Decisions\n", encoding="utf-8")
        self.ticket("01", "01.txt")
        self.assertRegex(self.batch(), r"^FAIL run not-integration \(log: .+\)$")
        self.assertEqual(self.merged_prs(), [])
        self.assertEqual(self.suite_runs(), 0)

    def test_resume_settles_an_interrupted_queue(self):
        self.ticket("01", "01.txt")
        self.ticket("02", "02.txt")
        start = self.tip(BASE)
        # Interrupted while landing: 01's PR merged on GitHub, its ticket file not updated yet; 02 not merged.
        (self.kit.answers / "pr-21-state").write_text("MERGED", encoding="utf-8")
        queue = {"base": BASE, "start_tip": start, "state": "landing",
                 "tickets": [{"ticket": "01", "pr": "21", "status": "landing"},
                             {"ticket": "02", "pr": "22", "status": "queued"}]}
        (self.kit.run_dir / "queue.json").write_text(json.dumps(queue), encoding="utf-8")
        p = self.kit.step(RESUME, self.kit.run_dir)
        self.assertEqual(p.stdout, "OK run resumed 2 actions\n", p.stdout + p.stderr)
        got = json.loads((self.kit.run_dir / "queue.json").read_text())
        self.assertEqual(got["state"], "interrupted")
        self.assertEqual({t["ticket"]: t["status"] for t in got["tickets"]}, {"01": "landed", "02": "waiting"})
        self.assertEqual(self.status("01"), "merged")
        self.assertEqual(self.status("02"), "building")
        p = self.kit.step(RESUME, self.kit.run_dir)
        self.assertEqual(p.stdout, "OK run resumed 0 actions\n", p.stdout + p.stderr)
        self.assertEqual(self.batch(), "OK batch landed 02 suites=1")


if __name__ == "__main__":
    unittest.main()
