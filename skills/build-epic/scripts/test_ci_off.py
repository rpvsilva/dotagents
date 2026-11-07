"""Tests for a run with `ci: off` in `<run>/decisions.md` (spec behaviour 36): no step waits on checks, and the PR
bodies say the local run of the Checks is the proof."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_merge_task as mt  # noqa: E402
from steplib import decisions, prbody, runfolder  # noqa: E402
from testkit import Kit  # noqa: E402

RED = '[{"name": "ci", "bucket": "fail", "link": "https://github.com/o/r/actions/runs/4242/job/7"}]'
PENDING = '[{"name": "ci", "bucket": "pending", "link": ""}]'


def decide(run, key, value):
    """What `decide.py <run> set <key> <value>` records (its writing is tested in test_decisions.py), in-process."""
    d = decisions.load(run) or decisions.Decisions()
    decisions.check_value(key, value)
    d.headers[key] = value
    decisions.save(run, d)


class CiOffMergeTest(unittest.TestCase):
    # merge_task.py's sandbox: a task worktree with one commit, a ticket branch on origin, a stub `gh`.
    setUp = mt.MergeTaskTest.setUp
    checks = mt.MergeTaskTest.checks
    commit = mt.MergeTaskTest.commit
    merge = mt.MergeTaskTest.merge
    calls = mt.MergeTaskTest.calls

    def test_ci_off_merges_on_green_local_checks_with_no_pr_checks_call(self):
        decide(self.kit.run_dir, "ci", "off")
        runfolder.set_header(self.ticket, "PR", "https://github.com/o/r/pull/9")
        self.kit.answer("pr-checks", out=RED, rc=1)
        self.assertRegex(self.merge(), r"^OK 01-01 done ")
        self.assertEqual(self.calls("pr", "checks"), [])
        self.assertEqual(self.calls("run", "view"), [])
        self.assertEqual(len(self.calls("pr", "merge")), 1)
        self.assertEqual(runfolder.get(self.task, "Status"), "done")

    def test_ci_off_opens_no_draft_ticket_pr_to_wait_on(self):
        decide(self.kit.run_dir, "ci", "off")
        self.assertRegex(self.merge(), r"^OK 01-01 done ")
        self.assertEqual(self.calls("pr", "create") + self.calls("pr", "list") + self.calls("pr", "checks"), [])
        self.assertIsNone(runfolder.get(self.ticket, "PR"))

    def test_ci_off_still_refuses_a_red_local_check(self):
        decide(self.kit.run_dir, "ci", "off")
        self.checks("false")
        self.assertTrue(self.merge().startswith("FAIL 01-01 red false (log: "))
        self.assertEqual(self.calls("pr", "merge") + self.calls("pr", "checks"), [])

    def test_ci_on_still_waits_on_the_ticket_checks(self):
        decide(self.kit.run_dir, "ci", "on")
        runfolder.set_header(self.ticket, "PR", "https://github.com/o/r/pull/9")
        self.kit.answer("pr-checks", out=PENDING, rc=8, n=1)
        self.kit.answer("pr-checks", out='[{"name": "ci", "bucket": "pass", "link": ""}]')
        self.assertRegex(self.merge(), r"^OK 01-01 done ")
        self.assertEqual(len(self.calls("pr", "checks")), 2)


TICKET = "# Alpha\nStatus: building\nBranch: feat/alpha\nIssue: #42\n"
TASK = "# 01-02: JSON export\nStatus: in-progress\nTicket: 01\n\n## What to build\nExports JSON.\n\n## Check\n`t`.\n"


class CiOffBodyTest(unittest.TestCase):
    def setUp(self):
        self.kit = Kit()
        self.addCleanup(self.kit.close)
        self.kit.make_origin()
        self.kit.init_run()
        run = self.kit.run_dir
        (run / "tickets").mkdir()
        (run / "tasks").mkdir()
        self.ticket = run / "tickets" / "01-alpha.md"
        self.ticket.write_text(TICKET)
        self.task = run / "tasks" / "01-02-json-export.md"
        self.task.write_text(TASK)

    def bodies(self):
        run = self.kit.run_dir
        return (prbody.task_body(run, "01-02", self.task, self.ticket),
                prbody.ticket_body(run, self.ticket, "## Testing\nRan it."))

    def test_ci_off_bodies_say_the_local_checks_run_is_the_proof(self):
        decide(self.kit.run_dir, "ci", "off")
        for body in self.bodies():
            self.assertIn(prbody.LOCAL_PROOF, body)
            self.assertEqual(body.count(prbody.LOCAL_PROOF), 1, body)

    def test_ci_on_bodies_omit_the_local_proof_line(self):
        decide(self.kit.run_dir, "ci", "on")
        for body in self.bodies():
            self.assertNotIn(prbody.LOCAL_PROOF, body)
            self.assertIn("## Merge", body)

    def test_no_ci_line_counts_as_ci_on(self):
        decide(self.kit.run_dir, "merge_identity", "app")
        for body in self.bodies():
            self.assertNotIn(prbody.LOCAL_PROOF, body)


if __name__ == "__main__":
    unittest.main()
