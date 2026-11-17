"""Tests for `report_check.py`: a subagent's output is its role's JSON at `<run>/reports/<id>.json`, and its final
message the status and that path, at most 400 characters."""
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from testkit import SCRIPTS, Kit  # noqa: E402

REPORT_CHECK = SCRIPTS / "report_check.py"

OUTPUT = {"status": "done", "commit": "abc1234", "files": ["skills/x.py"], "suites": [], "followups": [],
          "tests": [{"name": "test_x", "criterion": "x works", "broke": "returned None"}]}


class ReportCheckTest(unittest.TestCase):
    def setUp(self):
        self.kit = Kit()
        self.addCleanup(self.kit.close)
        self.reports = self.kit.run_dir / "reports"
        self.reports.mkdir()
        self.write("04-02", OUTPUT)

    def write(self, ident, output):
        path = self.reports / f"{ident}.json"
        path.write_text(output if isinstance(output, str) else json.dumps(output), encoding="utf-8")
        return path

    def message(self, ident="04-02", status="done", **extra):
        return json.dumps({"status": status, "output": str(self.reports / f"{ident}.json"), **extra})

    def check(self, message, ident="04-02", *flags):
        p = subprocess.run([sys.executable, "-I", str(REPORT_CHECK), str(self.kit.run_dir), ident, *flags],
                           cwd="/", env=self.kit.env, input=message.encode(), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE)
        out = p.stdout.decode()
        self.assertEqual(len(out.splitlines()), 1, out)
        return p.returncode, out.strip()

    def assertViolation(self, message, detail, ident="04-02", *flags):
        rc, line = self.check(message, ident, *flags)
        self.assertEqual(rc, 1, line)
        m = re.fullmatch(rf"FAIL {ident} brief-violation (.+) \(log: (.+)\)", line)
        self.assertTrue(m, line)
        self.assertIn(detail, m.group(1))
        self.assertTrue(Path(m.group(2)).is_file(), line)

    def test_a_valid_output_and_message_print_ok_and_the_status(self):
        self.assertEqual(self.check(self.message()), (0, "OK 04-02 done"))
        self.write("04-02", dict(OUTPUT, status="plan-wrong", why="the store has no ids"))
        self.assertEqual(self.check(self.message(status="plan-wrong")), (0, "OK 04-02 plan-wrong"))

    def test_a_code_fence_and_blank_lines_around_the_message_are_allowed(self):
        self.assertEqual(self.check("\n```json\n" + self.message() + "\n```\n\n"), (0, "OK 04-02 done"))

    def test_the_role_follows_the_id(self):
        self.write("plan-B", {"status": "done", "wrote": ["map.md"]})
        self.assertEqual(self.check(self.message("plan-B"), "plan-B"), (0, "OK plan-B done"))
        self.write("explore-store", {"status": "done", "note": "notes/store.md"})
        self.assertEqual(self.check(self.message("explore-store"), "explore-store"), (0, "OK explore-store done"))
        self.write("plan-B", {"status": "done"})
        self.assertViolation(self.message("plan-B"), "missing wrote", "plan-B")

    def test_a_long_message_is_a_length_violation(self):
        self.assertViolation(self.message(note="x" * 400), "length=")

    def test_the_old_four_line_block_is_a_violation(self):
        self.assertViolation("status: done\nchanged: x\nwhy:\nfollow-up:\n", "message: json")

    def test_a_message_disagreeing_with_the_output_is_a_violation(self):
        self.assertViolation(self.message(status="blocked"), "status-mismatch")

    def test_a_missing_or_broken_output_is_a_violation(self):
        (self.reports / "04-02.json").unlink()
        self.assertViolation(self.message(), "no-output")
        self.write("04-02", "Built it, all green.")
        self.assertViolation(self.message(), "json")

    def test_an_output_off_its_schema_names_the_problem(self):
        self.write("04-02", dict(OUTPUT, status="blocked"))
        self.assertViolation(self.message(status="blocked"), "$: missing why")

    def test_from_report_checks_the_output_alone(self):
        self.assertEqual(self.check("", "04-02", "--from-report"), (0, "OK 04-02 done"))
        self.write("04-02", dict(OUTPUT, extra=1))
        self.assertViolation("", "$: unknown key extra", "04-02", "--from-report")


if __name__ == "__main__":
    unittest.main()
