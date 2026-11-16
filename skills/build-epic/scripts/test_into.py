"""Tests for a run into a base other than the default branch (`decisions.md`'s `base:`): the ticket branch is cut from
it, and a hub task catches up with it. Against a bare origin and a stub `gh`; the ticket PR opening against it is in
test_ship.py."""
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import ci  # noqa: E402
from testkit import SCRIPTS, Kit  # noqa: E402

DECIDE = SCRIPTS / "decide.py"
BASE = "int/x"
BRANCH = "feat/t"


class IntoCase(unittest.TestCase):
    def setUp(self):
        self.kit = kit = Kit()
        self.addCleanup(kit.close)
        kit.make_origin()
        kit.init_run()
        for d in ("tickets", "tasks", "worktrees"):
            (kit.run_dir / d).mkdir()
        kit.branch_from_main(BASE)
        self.base_tip = self.commit(BASE, "base.txt", "integration work\n", "feat: on the base")
        self.ticket = kit.run_dir / "tickets" / "01-t.md"
        self.decide("base", BASE)

    def decide(self, key, value):
        p = self.kit.step(DECIDE, self.kit.run_dir, "set", key, value)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def commit(self, branch, name, text, message):
        """Commits `name` on origin's `branch` from the clone, pushes it and returns the new tip."""
        clone = self.kit.clone
        self.kit.git("fetch", "-q", "origin", cwd=clone)
        self.kit.git("checkout", "-q", "-B", branch, f"origin/{branch}", cwd=clone)
        (clone / name).write_text(text, encoding="utf-8")
        self.kit.git("add", name, cwd=clone)
        self.kit.git("commit", "-qm", message, cwd=clone)
        self.kit.git("push", "-q", "origin", branch, cwd=clone)
        self.kit.git("checkout", "-q", "--detach", cwd=clone)
        return self.kit.git("rev-parse", "HEAD", cwd=clone)

    def origin_tip(self, ref):
        return self.kit.git("--git-dir", str(self.kit.origin), "rev-parse", ref)

    def step(self, script, *args):
        p = self.kit.step(SCRIPTS / script, self.kit.run_dir, *args)
        self.assertEqual(p.stdout.count("\n"), 1, p.stdout + p.stderr)
        line = p.stdout.rstrip("\n")
        self.assertTrue(line.startswith("OK "), line)
        return line


class StartTicketIntoTest(IntoCase):
    def test_the_ticket_branch_is_cut_from_the_base_tip(self):
        self.ticket.write_text(f"# 01: T\nStatus: ready\nIssue: none\nBranch: {BRANCH}\n", encoding="utf-8")
        self.assertEqual(self.step("start_ticket.py", "01"),
                         f"OK 01 building branch={BRANCH} base={self.base_tip[:7]}")
        self.assertEqual(self.origin_tip(BRANCH), self.base_tip)
        self.assertNotEqual(self.origin_tip(BRANCH), self.origin_tip("main"))

    def test_with_no_base_the_ticket_branch_is_cut_from_the_default_branch(self):
        (self.kit.run_dir / "decisions.md").unlink()
        self.ticket.write_text(f"# 01: T\nStatus: ready\nIssue: none\nBranch: {BRANCH}\n", encoding="utf-8")
        self.step("start_ticket.py", "01")
        self.assertEqual(self.origin_tip(BRANCH), self.origin_tip("main"))


class HubTaskIntoTest(IntoCase):
    def test_a_hub_task_catches_up_with_the_base_not_the_default_branch(self):
        self.kit.branch_from_main(BRANCH)
        main_tip = self.commit("main", "main.txt", "default branch only\n", "feat: on main")
        self.ticket.write_text(f"# 01: T\nStatus: building\nIssue: none\nBranch: {BRANCH}\n", encoding="utf-8")
        (self.kit.run_dir / "tasks" / "01-02-hub.md").write_text(
            "# 01-02: Hub\nStatus: ready\nTicket: 01\nBlocked by: ticket 02\n", encoding="utf-8")
        line = self.step("start_task.py", "01-02")
        self.assertTrue(line.endswith(f" synced={self.base_tip[:7]}"), line)
        wt = self.kit.run_dir / "worktrees" / "01-02"
        head = self.kit.git("rev-parse", "HEAD", cwd=wt)
        self.kit.git("merge-base", "--is-ancestor", self.base_tip, head, cwd=wt)
        p = subprocess.run(["git", "-C", str(wt), "merge-base", "--is-ancestor", main_tip, head], env=self.kit.env)
        self.assertEqual(p.returncode, 1, "the default branch's new commit was merged in")
        self.assertEqual((wt / "base.txt").read_text(encoding="utf-8"), "integration work\n")


class BaseValueTest(unittest.TestCase):
    """`ci.base`, which every step reads the run's base through, reads `decisions.md`'s `base:` in this process."""

    def setUp(self):
        self.kit = Kit()
        self.addCleanup(self.kit.close)
        self.kit.init_run()

    def test_a_named_base_is_used_and_an_empty_one_means_the_default_branch(self):
        run, decisions = self.kit.run_dir, self.kit.run_dir / "decisions.md"
        self.assertEqual(ci.base(run), "main")  # no decisions.md
        for value, want in (("`int/x`", BASE), ("int/x", BASE), ("", "main"), ("none", "main"), ("Default", "main"),
                            ("null", "main"), ("~", "main")):
            with self.subTest(value=value):
                decisions.write_text(f"# Decisions\nbase: {value}\n", encoding="utf-8")
                self.assertEqual(ci.base(run), want)
        self.assertEqual(ci.base(run, "trunk"), "trunk")  # the caller's default, over run.md's


if __name__ == "__main__":
    unittest.main()
