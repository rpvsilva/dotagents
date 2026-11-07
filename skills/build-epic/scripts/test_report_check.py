"""Tests for `report_check.py`: a subagent's final message is the 4-line block, at most 400 characters, with its
full report at `<run>/reports/<id>.md`."""
import re
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from testkit import SCRIPTS, Kit  # noqa: E402

REPORT_CHECK = SCRIPTS / "report_check.py"

BLOCK = "status: done\nchanged: abc1234 skills/x.py\nwhy:\nfollow-up: none\n"


def padded(length):
    """A valid block exactly `length` characters long, its trailing newline not counted."""
    head = "status: done\nchanged: abc1234 "
    tail = "\nwhy:\nfollow-up: none"
    return head + "x" * (length - len(head) - len(tail)) + tail + "\n"


class ReportCheckTest(unittest.TestCase):
    def setUp(self):
        self.kit = Kit()
        self.addCleanup(self.kit.close)
        self.reports = self.kit.run_dir / "reports"
        self.reports.mkdir()
        (self.reports / "04-02.md").write_text("# 04-02 report\n\nAll of it.\n")

    def check(self, message, ident="04-02", run=None):
        p = subprocess.run([sys.executable, "-I", str(REPORT_CHECK), str(run or self.kit.run_dir), ident],
                           cwd="/", env=self.kit.env, input=message.encode(), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE)
        out = p.stdout.decode()
        self.assertEqual(len(out.splitlines()), 1, out)
        return p.returncode, out.strip()

    def assertViolation(self, message, detail, ident="04-02"):
        rc, line = self.check(message, ident)
        self.assertEqual(rc, 1, line)
        m = re.fullmatch(rf"FAIL {ident} brief-violation {detail} \(log: (.+)\)", line)
        self.assertTrue(m, line)
        self.assertTrue(Path(m.group(1)).is_file(), line)

    def test_valid_block_with_its_report_file_prints_ok_and_the_status(self):
        self.assertEqual(self.check(BLOCK), (0, "OK 04-02 done"))
        self.assertEqual(self.check(BLOCK.replace("done", "plan-wrong")), (0, "OK 04-02 plan-wrong"))

    def test_block_of_exactly_400_characters_passes(self):
        self.assertEqual(self.check(padded(400)), (0, "OK 04-02 done"))

    def test_401_character_block_is_a_length_violation(self):
        self.assertViolation(padded(401), "length=401")

    def test_missing_report_file_is_a_no_report_violation(self):
        (self.reports / "04-02.md").unlink()
        self.assertViolation(BLOCK, "no-report")

    def test_empty_report_file_is_a_no_report_violation(self):
        (self.reports / "04-02.md").write_text("")
        self.assertViolation(BLOCK, "no-report")

    def test_long_message_without_a_report_names_both(self):
        (self.reports / "04-02.md").unlink()
        self.assertViolation(padded(500), "length=500 no-report")

    def test_extra_line_after_the_block_is_a_violation(self):
        self.assertViolation(BLOCK + "Also, I refactored the parser.\n", "lines=5")

    def test_extra_line_before_the_block_is_a_violation(self):
        self.assertViolation("Here is my report:\n" + BLOCK, "lines=5")

    def test_second_follow_up_line_is_a_violation(self):
        self.assertViolation(BLOCK + "follow-up: docs for --flag\n", "lines=5")

    def test_missing_or_misordered_field_is_a_violation(self):
        self.assertViolation(BLOCK.replace("why:\n", ""), "lines=3")
        self.assertViolation(BLOCK.replace("why:", "reason:"), "shape")
        self.assertViolation("changed: x\nstatus: done\nwhy:\nfollow-up: none\n", "shape")

    def test_empty_status_is_a_violation(self):
        self.assertViolation(BLOCK.replace("status: done", "status:"), "shape")

    def test_empty_message_is_a_violation(self):
        self.assertViolation("", "lines=0")

    def test_surrounding_blank_lines_and_a_code_fence_are_not_extra_lines(self):
        self.assertEqual(self.check("\n```\n" + BLOCK + "```\n\n"), (0, "OK 04-02 done"))

    def from_report(self, report, ident="04-02"):
        (self.reports / f"{ident}.md").write_text(report)
        p = subprocess.run([sys.executable, "-I", str(REPORT_CHECK), str(self.kit.run_dir), ident, "--from-report"],
                           cwd="/", env=self.kit.env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE)
        out = p.stdout.decode()
        self.assertEqual(len(out.splitlines()), 1, out)
        return p.returncode, out.strip()

    def test_from_report_finds_the_block_ending_the_report_file(self):
        self.assertEqual(self.from_report("# 04-02 report\n\nAll of it.\n\n" + BLOCK), (0, "OK 04-02 done"))

    def test_from_report_allows_a_trailing_blank_line_or_a_closing_fence(self):
        self.assertEqual(self.from_report("All of it.\n\n" + BLOCK + "\n\n"), (0, "OK 04-02 done"))
        self.assertEqual(self.from_report("All of it.\n\n```\n" + BLOCK + "```\n"), (0, "OK 04-02 done"))
        self.assertEqual(self.from_report("All of it.\n```\n\n" + BLOCK.replace("done", "blocked") + "\n```\n\n"),
                         (0, "OK 04-02 blocked"))

    def test_from_report_takes_the_last_block_when_the_report_quotes_an_earlier_one(self):
        self.assertEqual(self.from_report(BLOCK.replace("done", "blocked") + "\nMore.\n\n" + BLOCK),
                         (0, "OK 04-02 done"))

    def test_from_report_still_refuses_a_line_after_the_block(self):
        rc, line = self.from_report("All of it.\n\n" + BLOCK + "Also, I refactored the parser.\n")
        self.assertEqual(rc, 1, line)
        self.assertRegex(line, r"^FAIL 04-02 brief-violation lines=5 \(log: .+\)$")

    def test_from_report_without_a_block_or_a_file_is_a_violation(self):
        rc, line = self.from_report("No block here.\n")
        self.assertRegex(line, r"^FAIL 04-02 brief-violation shape \(log: .+\)$")
        (self.reports / "04-02.md").unlink()
        p = subprocess.run([sys.executable, "-I", str(REPORT_CHECK), str(self.kit.run_dir), "04-02", "--from-report"],
                           cwd="/", env=self.kit.env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)
        self.assertEqual(p.returncode, 1)
        self.assertRegex(p.stdout.decode(), r"^FAIL 04-02 brief-violation shape no-report \(log: .+\)\n$")

    def test_report_found_through_a_linked_run_path(self):
        link = self.kit.link(self.kit.run_dir, "run-link")
        rc, line = self.check(BLOCK, run=link)
        self.assertEqual((rc, line), (0, "OK 04-02 done"))


if __name__ == "__main__":
    unittest.main()
