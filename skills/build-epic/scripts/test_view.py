"""Tests for `view.py`: what the human reads (the plan, a stuck task, the report) rendered from the run folder."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
VIEW = HERE / "view.py"


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class ViewTest(unittest.TestCase):
    def setUp(self):
        self.run_dir = run = Path(tempfile.mkdtemp()) / "run"
        write(run / "tickets" / "01-notes.md", "# TT: Notes\nStatus: ready\nIssue: #404\nBlocked by: none\n")
        write(run / "tickets" / "02-snooze.md",
              "# TT: Snooze\nStatus: claimed\nIssue: #405\nBlocked by: 01\nClaimed by: someone\n")
        write(run / "tasks" / "01-01-note.md", "# 01-01: todo note\nStatus: blocked\nTicket: 01\nBlocked by: none\n"
              "Writes: a.py\nTier: high\n\n## Check\nGAP: run it by hand\n")
        write(run / "spec.md", "# Spec\n\n## Open questions\n1. Max length? Assumed: 1000.\n")
        write(run / "map.md", "# Map\n\n## Gaps\n- help text untested\n\n## Lanes\n- Lane 1: 01 -> 02\n")

    def view(self, *args):
        p = subprocess.run([sys.executable, "-I", str(VIEW), str(self.run_dir), *args], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p.stdout

    def test_the_plan_shows_tickets_tasks_lanes_gaps_and_questions_from_the_files(self):
        text = self.view("plan")
        self.assertIn("- **01** Notes (#404); blocked by: none", text)
        self.assertIn("claimed by someone", text)
        self.assertIn("  - 01-01 todo note; blocked by: none; tier: high; check: GAP: run it by hand", text)
        self.assertIn("## Lanes\n\n- Lane 1: 01 -> 02", text)
        self.assertIn("- 01-01: GAP: run it by hand\n- help text untested", text)
        self.assertIn("1. Max length? Assumed: 1000.", text)
        self.assertIn(f"Spec: {self.run_dir.resolve() / 'spec.md'}", text)

    def test_a_stuck_task_is_rendered_never_raw(self):
        write(self.run_dir / "reports" / "01-01.json", json.dumps({
            "status": "blocked", "why": "the store has no ids", "commit": "", "files": [], "tests": [],
            "suites": [], "followups": []}))
        text = self.view("task", "01-01")
        self.assertIn("Status: blocked: the store has no ids", text)
        self.assertNotIn('"status"', text)
        write(self.run_dir / "reports" / "01-01.json", "status: blocked\nwhy: x\n")
        self.assertIn("No valid output at", self.view("task", "01-01"))

    def test_the_report_gathers_waits_and_every_follow_up(self):
        write(self.run_dir / "reports" / "01-01.json", json.dumps({
            "status": "done", "commit": "abc1234", "files": [], "tests": [], "suites": [],
            "followups": [{"severity": "should-fix", "kind": "owner-doc", "path": "AGENTS.md", "item": "Checks",
                           "claim": "name the suite", "patch": "+x"}]}))
        write(self.run_dir / "reviews" / "7-abc1234.json", json.dumps({
            "status": "done", "mode": "ticket", "findings": [], "checked": "all", "process": [],
            "followups": [{"severity": "nit", "kind": "interaction", "path": "b.py", "line": 3, "claim": "flag"}]}))
        text = self.view("report")
        self.assertIn("- ticket 02 claimed by someone", text)
        self.assertIn("### reports/01-01.json\n- should-fix owner-doc AGENTS.md#Checks: name the suite\n```diff\n+x\n```",
                      text)
        self.assertIn("### reviews/7-abc1234.json\n- nit interaction b.py:3: flag", text)


if __name__ == "__main__":
    unittest.main()
