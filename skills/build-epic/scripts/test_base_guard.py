"""Tests for the base guard in `steplib/merge.py`, through `ship_ticket.py merge` against a stub `gh`: a ticket PR
merges only into the run's base."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_ship  # noqa: E402
from steplib import runfolder  # noqa: E402
from testkit import SCRIPTS  # noqa: E402

SHIP = SCRIPTS / "ship_ticket.py"


class TicketBaseGuardTest(test_ship.Base):
    def setUp(self):
        super().setUp()
        runfolder.set_header(self.ticket, "PR", "https://github.com/o/r/pull/20")
        self.kit.answer("pr-list", out="[]")
        self.assertEqual(self.one_line(self.kit.step(SHIP, self.kit.run_dir, "01", "open")), "OK 01 open pr=20")
        (self.kit.run_dir / "decisions.md").write_text("# Decisions\nbase: int/x\n", encoding="utf-8")

    def ship(self):
        return self.one_line(self.kit.step(SHIP, self.kit.run_dir, "01", "merge"))

    def test_a_ticket_pr_into_the_default_branch_while_the_base_is_another_branch_is_refused(self):
        line = self.ship()
        self.assertTrue(line.startswith("FAIL 01 wrong-base main (log: "), line)
        self.assertEqual(self.calls("pr", "merge"), [])
        self.assertEqual(runfolder.get(self.ticket, "Status"), "building")

    def test_a_ticket_pr_into_a_ticket_branch_is_refused(self):
        (self.kit.run_dir / "tickets" / "02-u.md").write_text("# 02: U\nStatus: building\nBranch: feat/u\n", encoding="utf-8")
        (self.kit.answers / "pr-base").write_text("feat/u", encoding="utf-8")
        line = self.ship()
        self.assertTrue(line.startswith("FAIL 01 wrong-base feat/u (log: "), line)
        self.assertEqual(self.calls("pr", "merge"), [])

    def test_a_ticket_pr_into_the_base_merges_and_squashes(self):
        self.kit.branch_from_main("int/x")  # the merge guard reads its rules from origin/<base>
        (self.kit.answers / "pr-base").write_text("int/x", encoding="utf-8")
        (self.kit.run_dir / "logs" / "PR20-review.json").write_text('{"commit_id": "%s"}' % self.head(), encoding="utf-8")
        self.assertEqual(self.ship(), "OK 01 merged pr=20")
        self.assertIn("--squash", self.calls("pr", "merge")[0])


if __name__ == "__main__":
    unittest.main()
