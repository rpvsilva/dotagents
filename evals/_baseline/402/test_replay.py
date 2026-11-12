"""replay.py: seeds a run folder from the fixed plan, and build-epic's offline step scripts read it."""
import contextlib
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import replay  # noqa: E402

PLAN = HERE / "plan"
SCRIPTS = HERE.parents[2] / "skills" / "build-epic" / "scripts"


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = replay.main([str(a) for a in argv])
    return rc, out.getvalue().strip()


class Replay(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.run_dir = self.tmp / "run"

    def plan_copy(self):
        plan = self.tmp / "plan"
        shutil.copytree(PLAN, plan)
        return plan

    def assert_refused(self, plan, reason):
        rc, out = run(plan, self.run_dir, "--base", "b1")
        self.assertEqual(rc, 1)
        self.assertTrue(out.startswith("FAIL replay "), out)
        self.assertIn(reason, out)
        self.assertFalse(self.run_dir.exists() and any(self.run_dir.iterdir()))

    def test_fixed_plan_seeds_a_run_folder_with_the_base_filled_in(self):
        rc, out = run(PLAN, self.run_dir, "--base", "baseline-402-r1")
        self.assertEqual(rc, 0, out)
        self.assertEqual(out, f"OK replay seeded {self.run_dir.resolve()} tickets=5 tasks=16 base=baseline-402-r1")
        texts = [f.read_text(encoding="utf-8") for f in self.run_dir.rglob("*.md")]
        self.assertFalse(any(replay.PLACEHOLDER in t for t in texts))
        self.assertIn("(`baseline-402-r1`)", (self.run_dir / "spec.md").read_text(encoding="utf-8"))
        self.assertFalse((self.run_dir / "run.md").exists() or (self.run_dir / "decisions.md").exists())

    def test_fixed_plan_reads_as_just_approved(self):
        tickets, tasks = replay.check_plan(PLAN)
        self.assertEqual(len(tickets), 5)
        self.assertFalse([f.name for f in tasks if "review-fixes" in f.name or f.name.endswith("-tests.md")])

    def test_status_and_resume_read_the_seeded_folder_offline(self):
        self.assertEqual(run(PLAN, self.run_dir, "--base", "b1")[0], 0)
        for script, args in (("status.py", []), ("status.py", ["--write"]), ("resume.py", [])):
            out = subprocess.run([sys.executable, "-I", str(SCRIPTS / script), str(self.run_dir), *args],
                                 capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("OK run resumed 0 actions", out.stdout)
        status = subprocess.run([sys.executable, "-I", str(SCRIPTS / "status.py"), str(self.run_dir)],
                                capture_output=True, text=True).stdout
        self.assertEqual(sum(" ready " in line for line in status.splitlines()), 21, status)

    def test_non_empty_run_folder_is_refused(self):
        self.run_dir.mkdir()
        (self.run_dir / "run.md").write_text("# Run\n", encoding="utf-8")
        rc, out = run(PLAN, self.run_dir, "--base", "b1")
        self.assertEqual((rc, out), (1, f"FAIL replay run folder not empty: {self.run_dir.resolve()}"))

    def test_a_run_time_line_is_refused(self):
        plan = self.plan_copy()
        task = next((plan / "tasks").glob("01-01-*.md"))
        task.write_text(task.read_text(encoding="utf-8").replace("Tier: low", "Tier: low\nPR: #1"), encoding="utf-8")
        self.assert_refused(plan, "run-time PR: line")

    def test_a_status_other_than_ready_is_refused(self):
        plan = self.plan_copy()
        ticket = next((plan / "tickets").glob("01-*.md"))
        ticket.write_text(ticket.read_text(encoding="utf-8").replace("Status: ready", "Status: merged"),
                          encoding="utf-8")
        self.assert_refused(plan, "is not Status: ready")

    def test_an_orchestrator_task_is_refused(self):
        plan = self.plan_copy()
        (plan / "tasks" / "02-04-review-fixes.md").write_text("# fixes\nStatus: ready\n", encoding="utf-8")
        self.assert_refused(plan, "orchestrator task 02-04-review-fixes.md")

    def test_a_plan_check_problem_is_refused_and_leaves_the_folder_empty(self):
        plan = self.plan_copy()
        task = next((plan / "tasks").glob("02-01-*.md"))
        text = task.read_text(encoding="utf-8")
        task.write_text(text.replace("Writes: ", "Writes: todo/cli/commands/show.py, "), encoding="utf-8")
        self.assert_refused(plan, "plan_check: ")

    def test_a_bad_base_is_refused(self):
        for base in (".x", "a..b", "a b", "x.lock"):
            rc, out = run(PLAN, self.run_dir, "--base", base)
            self.assertEqual(rc, 1, base)
            self.assertTrue(out.startswith("FAIL replay bad-base"), out)


if __name__ == "__main__":
    unittest.main()
