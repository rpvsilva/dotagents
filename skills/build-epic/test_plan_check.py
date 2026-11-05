"""Tests for plan_check.py on throwaway run folders."""
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import plan_check  # noqa: E402


def ticket(run, name, blocked="none"):
    (run / "tickets").mkdir(exist_ok=True)
    (run / "tickets" / name).write_text(f"# T\nStatus: ready\nIssue: #1\nBranch: feat/x\nBlocked by: {blocked}\n")


def task(run, name, ticket_no, writes, blocked="none"):
    (run / "tasks").mkdir(exist_ok=True)
    lines = ["# T", "Status: ready", f"Ticket: {ticket_no}", f"Blocked by: {blocked}"]
    if writes is not None:
        lines.append(f"Writes: {writes}")
    (run / "tasks" / name).write_text("\n".join(lines) + "\n\n## What to build\nx\n")


class PlanCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def check(self):
        return plan_check.check(self.run)

    def test_disjoint_tasks_pass_as_separate_lanes(self):
        ticket(self.run, "01-a.md")
        ticket(self.run, "02-b.md")
        task(self.run, "01-01-a.md", "01", "a.py")
        task(self.run, "02-01-b.md", "02", "b.py")
        self.assertEqual(self.check(), ([], 2, 2))

    def test_shared_path_in_one_ticket_needs_blocked_by(self):
        ticket(self.run, "01-a.md")
        task(self.run, "01-01-x.md", "01", "model.py, a.py")
        task(self.run, "01-02-y.md", "01", "`model.py`")
        problems, _, _ = self.check()
        self.assertEqual(problems, ["01-01-x.md and 01-02-y.md both write model.py with no Blocked by between them"])

    def test_transitive_blocked_by_orders_tasks(self):
        ticket(self.run, "01-a.md")
        task(self.run, "01-01-x.md", "01", "model.py")
        task(self.run, "01-02-y.md", "01", "b.py", blocked="01")
        task(self.run, "01-03-z.md", "01", "model.py", blocked="01-02")
        self.assertEqual(self.check()[0], [])

    def test_shared_path_across_tickets_needs_ticket_blocked_by(self):
        ticket(self.run, "01-a.md")
        ticket(self.run, "02-b.md")
        task(self.run, "01-01-x.md", "01", "README.md")
        task(self.run, "02-01-y.md", "02", "README.md")
        self.assertEqual(len(self.check()[0]), 1)

    def test_ticket_chain_orders_tasks_and_joins_one_lane(self):
        ticket(self.run, "01-a.md")
        ticket(self.run, "2-b.md", blocked="1")
        task(self.run, "01-01-x.md", "01", "README.md")
        task(self.run, "02-01-y.md", "02", "README.md")
        self.assertEqual(self.check(), ([], 2, 1))

    def test_hub_task_blocked_by_earlier_ticket_keeps_tickets_in_separate_lanes(self):
        ticket(self.run, "01-a.md")
        ticket(self.run, "02-b.md")
        task(self.run, "01-01-x.md", "01", "a.py")
        task(self.run, "01-02-hub.md", "01", "README.md", blocked="01")
        task(self.run, "02-01-y.md", "02", "b.py")
        task(self.run, "02-02-hub.md", "02", "README.md", blocked="01, ticket 01")
        self.assertEqual(self.check(), ([], 4, 2))

    def test_ticket_blocker_reached_through_an_earlier_task(self):
        ticket(self.run, "01-a.md")
        ticket(self.run, "02-b.md")
        task(self.run, "01-01-x.md", "01", "README.md")
        task(self.run, "02-01-y.md", "02", "b.py", blocked="ticket 1")
        task(self.run, "02-02-z.md", "02", "README.md", blocked="02-01")
        self.assertEqual(self.check()[0], [])

    def test_missing_writes_line_is_reported(self):
        ticket(self.run, "01-a.md")
        task(self.run, "01-01-x.md", "01", None)
        self.assertEqual(self.check()[0], ["01-01-x.md: no Writes line"])

    def test_orchestrator_tasks_are_skipped(self):
        ticket(self.run, "01-a.md")
        task(self.run, "01-01-x.md", "01", "a.py")
        task(self.run, "01-tests.md", "01", None)
        task(self.run, "01-02-review-fixes.md", "01", None)
        self.assertEqual(self.check()[0], [])

    def test_cycle_is_reported(self):
        ticket(self.run, "01-a.md")
        task(self.run, "01-01-x.md", "01", "a.py", blocked="02")
        task(self.run, "01-02-y.md", "01", "b.py", blocked="01")
        self.assertIn("01-01-x.md: Blocked by cycle", self.check()[0])

    def test_security_audit_tasks_without_ticket(self):
        task(self.run, "01-auth.md", "none", "auth.py")
        task(self.run, "02-api.md", "none", "auth.py, api.py")
        self.assertEqual(len(self.check()[0]), 1)
        task(self.run, "02-api.md", "none", "auth.py, api.py", blocked="01")
        self.assertEqual(self.check()[0], [])

    def test_main_exit_codes(self):
        ticket(self.run, "01-a.md")
        task(self.run, "01-01-x.md", "01", "a.py")
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(plan_check.main(["plan_check.py", str(self.run)]), 0)
            task(self.run, "01-02-y.md", "01", "a.py")
            self.assertEqual(plan_check.main(["plan_check.py", str(self.run)]), 1)
            self.assertEqual(plan_check.main(["plan_check.py"]), 2)
        self.assertIn("plan ok: 1 tasks, 1 lanes", out.getvalue())


if __name__ == "__main__":
    unittest.main()
