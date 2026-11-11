"""Tests for `review_plan.py`: the reviewer runs per ticket the approval message shows (spec behaviours 45, 46, 48, 51)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from testkit import SCRIPTS, Kit  # noqa: E402

REVIEW_PLAN = SCRIPTS / "review_plan.py"


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def task(tid, writes, blocked="none", extra=""):
    return f"# {tid}: t\nStatus: ready\nTicket: {tid.split('-')[0]}\nBlocked by: {blocked}\nWrites: {writes}\n{extra}"


class ReviewPlanTest(unittest.TestCase):
    def setUp(self):
        self.kit = Kit()
        self.addCleanup(self.kit.close)
        self.run_dir = self.kit.run_dir
        write(self.run_dir / "tickets" / "01-alpha.md", "# 01: Alpha\nStatus: ready\nBlocked by: none\n")
        write(self.run_dir / "tickets" / "02-beta.md", "# 02: Beta\nStatus: ready\nBlocked by: none\n")

    def decide(self, value):
        write(self.run_dir / "decisions.md", f"# Decisions\nreview_before_agent_merge: {value}\n")

    def plan(self):
        p = self.kit.step(REVIEW_PLAN, self.run_dir)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p.stdout.splitlines()

    def line(self, lines, ticket):
        return next(line for line in lines if line.startswith(ticket + " "))

    def test_a_ticket_with_a_hub_task_shows_one_hub_run_and_no_ticket_run(self):
        self.decide("no")
        write(self.run_dir / "tasks" / "01-01-a.md", task("01-01", "src/a.py"))
        write(self.run_dir / "tasks" / "01-02-hub.md", task("01-02", "README.md", blocked="01, ticket 02"))
        write(self.run_dir / "tasks" / "02-01-b.md", task("02-01", "src/b.py"))
        lines = self.plan()
        alpha, beta = self.line(lines, "01"), self.line(lines, "02")
        self.assertIn("hub 1 (small)", alpha)
        self.assertNotIn("ticket", alpha.split(":", 1)[1])
        self.assertIn("ticket 1 (small)", beta)
        self.assertNotIn("hub", beta)

    def test_the_hub_task_gets_no_task_run_of_its_own_when_reviews_are_required(self):
        self.decide("yes")
        write(self.run_dir / "tasks" / "01-01-a.md", task("01-01", "src/a.py"))
        write(self.run_dir / "tasks" / "01-02-hub.md", task("01-02", "README.md", blocked="01, ticket 02"))
        self.assertIn("task 1 (small)", self.line(self.plan(), "01"))

    def test_no_review_before_agent_merge_and_low_tasks_show_no_task_runs(self):
        self.decide("no")
        write(self.run_dir / "tasks" / "01-01-a.md", task("01-01", "src/a.py, docs/a.md"))
        write(self.run_dir / "tasks" / "01-02-b.md", task("01-02", "tests/test_a.py"))
        lines = self.plan()
        self.assertFalse([line for line in lines if "task " in line and "small" in line], lines)
        self.assertNotIn("security", self.line(lines, "01"))

    def test_review_before_agent_merge_gives_every_task_a_small_run(self):
        self.decide("yes")
        write(self.run_dir / "tasks" / "01-01-a.md", task("01-01", "src/a.py"))
        write(self.run_dir / "tasks" / "01-02-b.md", task("01-02", "src/b.py"))
        write(self.run_dir / "tasks" / "01-tests.md", "# 01-tests: tune\nStatus: ready\nTicket: 01\n")
        self.assertIn("task 2 (small)", self.line(self.plan(), "01"))

    def test_a_task_writing_a_storage_format_gets_a_small_run_and_its_ticket_a_security_run(self):
        self.decide("no")
        write(self.run_dir / "tasks" / "01-01-a.md", task("01-01", "src/a.py, data/schema.json"))
        write(self.run_dir / "tasks" / "02-01-b.md", task("02-01", "src/b.py"))
        lines = self.plan()
        alpha, beta = self.line(lines, "01"), self.line(lines, "02")
        self.assertIn("task 1 (small)", alpha)
        self.assertIn("security 1 (large)", alpha)
        self.assertIn("estimated high", alpha)
        self.assertNotIn("security", beta)
        self.assertIn("estimated low", beta)

    def test_a_tier_line_on_the_task_overrides_the_path_estimate(self):
        self.decide("no")
        write(self.run_dir / "tasks" / "01-01-a.md", task("01-01", "src/a.py", extra="Tier: high\n"))
        write(self.run_dir / "tasks" / "02-01-b.md", task("02-01", "config.json", extra="Tier: low\n"))
        lines = self.plan()
        self.assertIn("security 1 (large)", self.line(lines, "01"))
        self.assertNotIn("security", self.line(lines, "02"))

    def test_the_total_line_gives_runs_per_ticket_and_large_model_runs(self):
        self.decide("yes")
        write(self.run_dir / "tasks" / "01-01-a.md", task("01-01", "src/a.py, store.json"))
        write(self.run_dir / "tasks" / "01-02-hub.md", task("01-02", "README.md", blocked="01, ticket 02"))
        write(self.run_dir / "tasks" / "02-01-b.md", task("02-01", "src/b.py"))
        lines = self.plan()
        # 01: task 1 + hub 1 + security 1; 02: task 1 + ticket 1. 5 runs over 2 tickets, only security on the large model.
        self.assertEqual(lines[-1], "total: 5 runs over 2 tickets, 2.5 per ticket, 1 on the large model")

    def test_no_decisions_file_counts_as_no_review_before_agent_merge(self):
        write(self.run_dir / "tasks" / "01-01-a.md", task("01-01", "src/a.py"))
        self.assertNotIn("task ", self.line(self.plan(), "01").split(":", 1)[1])

    def test_a_missing_run_folder_fails_in_one_line(self):
        p = self.kit.step(REVIEW_PLAN, self.kit.tmp / "nope")
        self.assertEqual(p.returncode, 1)
        self.assertEqual(len(p.stdout.splitlines()), 1)
        self.assertTrue(p.stdout.startswith("FAIL run no-run-folder: "), p.stdout)


if __name__ == "__main__":
    unittest.main()
