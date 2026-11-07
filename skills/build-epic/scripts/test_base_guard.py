"""Tests for the base guard and the task merge method in `steplib/merge.py`, through `merge_task.py` and
`ship_ticket.py merge` against a stub `gh`: a PR merges only into the run's base or one of its ticket branches."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_merge_task  # noqa: E402
import test_ship  # noqa: E402
from steplib import runfolder  # noqa: E402
from testkit import SCRIPTS  # noqa: E402

SHIP = SCRIPTS / "ship_ticket.py"


class TaskBaseGuardTest(test_merge_task.MergeTaskTest):
    """The merge_task sandbox; the base class's own tests run from test_merge_task.py only (see the end)."""

    def into(self, base):
        (self.kit.answers / "pr-base").write_text(base)

    def assert_wrong_base(self, line, base):
        self.assertTrue(line.startswith(f"FAIL 01-01 wrong-base {base} (log: "), line)
        self.assertEqual(self.calls("pr", "merge"), [])
        self.assertEqual(runfolder.get(self.task, "Status"), "in-progress")

    def test_a_pr_into_the_default_branch_while_the_base_is_another_branch_is_refused(self):
        (self.kit.run_dir / "decisions.md").write_text("# Decisions\nbase: int/x\n")
        self.into("main")
        self.assert_wrong_base(self.merge(), "main")

    def test_a_pr_into_an_unrelated_branch_is_refused(self):
        self.into("feat/other")
        self.assert_wrong_base(self.merge(), "feat/other")

    def test_a_pr_into_its_ticket_branch_merges(self):
        (self.kit.run_dir / "decisions.md").write_text("# Decisions\nbase: int/x\n")
        self.into("feat/t")
        self.assertRegex(self.merge(), r"^OK 01-01 done ")
        self.assertEqual(len(self.calls("pr", "merge")), 1)

    def test_a_pr_into_another_ticket_branch_of_the_run_merges(self):
        (self.kit.run_dir / "tickets" / "02-u.md").write_text("# 02: U\nStatus: building\nBranch: feat/u\n")
        self.into("feat/u")
        self.assertRegex(self.merge(), r"^OK 01-01 done ")

    # task_merge_method

    def test_merge_method_merges_a_task_pr_with_a_merge_commit(self):
        self.setting("task_merge_method", "merge")
        head = self.kit.git("rev-parse", "HEAD", cwd=self.wt)
        self.assertRegex(self.merge(), r"^OK 01-01 done ")
        self.assertEqual(self.calls("pr", "merge"),
                         [["pr", "merge", "5", "--merge", "--delete-branch", "--match-head-commit", head]])

    def test_squash_is_the_default_task_merge_method(self):
        self.merge()
        self.assertIn("--squash", self.calls("pr", "merge")[0])

    def test_merge_method_passes_merge_to_merge_pr_sh(self):
        log = self.kit.tmp / "rule.log"
        rule = self.kit.tmp / "merge-pr.sh"
        rule.write_text(f'#!/usr/bin/env bash\necho "$*" >>"{log}"\n')
        rule.chmod(0o755)
        self.setting("merge_command", f"{rule} {{pr}}")
        self.setting("task_merge_method", "merge")
        self.assertRegex(self.merge(), r"^OK 01-01 done ")
        self.assertEqual(log.read_text(), "--merge 5\n")

    def test_merge_method_with_a_rule_that_has_no_merge_option_merges_nothing(self):
        log = self.kit.tmp / "rule.log"
        rule = self.kit.tmp / "my-merge"
        rule.write_text(f'#!/usr/bin/env bash\necho "$*" >>"{log}"\n')
        rule.chmod(0o755)
        self.setting("merge_command", f"{rule} {{pr}}")
        self.setting("task_merge_method", "merge")
        self.assertTrue(self.merge().startswith("FAIL 01-01 merge-method (log: "))
        self.assertFalse(log.exists())


class TicketBaseGuardTest(test_ship.Base):
    def setUp(self):
        super().setUp()
        runfolder.set_header(self.ticket, "PR", "https://github.com/o/r/pull/20")
        self.kit.answer("pr-list", out="[]")
        self.assertEqual(self.one_line(self.kit.step(SHIP, self.kit.run_dir, "01", "open")), "OK 01 open pr=20")
        (self.kit.run_dir / "decisions.md").write_text("# Decisions\nbase: int/x\n")

    def ship(self):
        return self.one_line(self.kit.step(SHIP, self.kit.run_dir, "01", "merge"))

    def test_a_ticket_pr_into_the_default_branch_while_the_base_is_another_branch_is_refused(self):
        line = self.ship()
        self.assertTrue(line.startswith("FAIL 01 wrong-base main (log: "), line)
        self.assertEqual(self.calls("pr", "merge"), [])
        self.assertEqual(runfolder.get(self.ticket, "Status"), "building")

    def test_a_ticket_pr_into_the_base_merges_and_squashes_whatever_the_task_merge_method(self):
        (self.kit.answers / "pr-base").write_text("int/x")
        self.set_setting("task_merge_method", "merge")
        self.assertEqual(self.ship(), "OK 01 merged pr=20")
        self.assertIn("--squash", self.calls("pr", "merge")[0])


for _name in [n for n in dir(test_merge_task.MergeTaskTest) if n.startswith("test_")]:
    setattr(TaskBaseGuardTest, _name, None)

if __name__ == "__main__":
    unittest.main()
