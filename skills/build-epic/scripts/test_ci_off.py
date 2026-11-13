"""Tests for a run with `ci: off` in `<run>/decisions.md` (spec behaviour 36): the PR bodies say the local run of the
Checks is the proof."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import decisions, prbody  # noqa: E402
from testkit import Kit  # noqa: E402


def decide(run, key, value):
    """What `decide.py <run> set <key> <value>` records (its writing is tested in test_decisions.py), in-process."""
    d = decisions.load(run) or decisions.Decisions()
    decisions.check_value(key, value)
    d.headers[key] = value
    decisions.save(run, d)


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
        self.ticket.write_text(TICKET, encoding="utf-8")
        self.task = run / "tasks" / "01-02-json-export.md"
        self.task.write_text(TASK, encoding="utf-8")

    def bodies(self):
        run = self.kit.run_dir
        return (prbody.task_body(run, "01-02", self.task, self.ticket),
                prbody.ticket_body(run, self.ticket))

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
