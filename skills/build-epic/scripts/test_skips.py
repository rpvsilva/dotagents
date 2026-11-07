"""Tests for skipped tests' reasons under `Not exercised here`: in the ticket PR body (`steplib/prbody.py`) and in
`status.py <run> --report` (spec behaviour 38)."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import prbody  # noqa: E402
from testkit import SCRIPTS, Kit  # noqa: E402

STATUS = SCRIPTS / "status.py"
ZSH = "test_start.StartTest.test_zsh: zsh is not installed"
NODE = "mod.test.sh: node 22.6+ is missing"
CLAUDE = "mod.test.sh: claude is not installed"


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class SkipsTest(unittest.TestCase):
    def setUp(self):
        self.kit = Kit()
        self.addCleanup(self.kit.close)
        run = self.run = self.kit.run_dir
        self.alpha = write(run / "tickets" / "01-alpha.md", "# 01: Alpha\nStatus: building\nIssue: #41\n")
        self.beta = write(run / "tickets" / "02-beta.md", "# 02: Beta\nStatus: ready\nIssue: #42\n")
        write(run / "tasks" / "01-01-a.md", "# 01-01: First\nStatus: done\nTicket: 01\n")
        write(run / "tasks" / "01-02-b.md", "# 01-02: Second\nStatus: done\nTicket: 01\n")
        write(run / "tasks" / "02-01-c.md", "# 02-01: Third\nStatus: ready\nTicket: 02\n")

    def checks(self, ident, *suites):
        write(self.run / "reports" / f"{ident}.checks.json", json.dumps(list(suites)))

    def suite(self, command, *reasons):
        return {"command": command, "passed": True, "skipped": len(reasons), "seconds": 1.0,
                "skip_reasons": list(reasons)}

    def not_exercised(self, ticket):
        return prbody.section(prbody.ticket_body(self.run, ticket), "Not exercised here")

    def report(self):
        p = self.kit.step(STATUS, self.run, "--report")
        self.assertEqual((p.returncode, p.stderr), (0, ""), p.stdout)
        return p.stdout

    def test_a_ticket_body_lists_each_saved_skip_reason_once(self):
        self.checks("01-01", self.suite("tests/run.sh", ZSH, NODE), self.suite("python3 -m unittest", ZSH))
        section = self.not_exercised(self.alpha)
        for reason in (ZSH, NODE):
            self.assertEqual(section.count(reason), 1, section)

    def test_a_ticket_body_gathers_reasons_from_all_its_tasks_and_no_other_ticket(self):
        self.checks("01-01", self.suite("tests/run.sh", ZSH))
        self.checks("01-02", self.suite("tests/run.sh", NODE, ZSH))
        self.checks("02-01", self.suite("tests/run.sh", CLAUDE))
        section = self.not_exercised(self.alpha)
        self.assertEqual([section.count(r) for r in (ZSH, NODE, CLAUDE)], [1, 1, 0], section)
        self.assertIn(CLAUDE, self.not_exercised(self.beta))

    def test_a_ticket_body_without_skips_has_no_not_exercised_section(self):
        self.checks("01-01", self.suite("tests/run.sh"))
        body = prbody.ticket_body(self.run, self.alpha)
        self.assertNotIn("Not exercised here", body)

    def test_report_lists_skip_reasons_by_ticket(self):
        self.checks("01-01", self.suite("tests/run.sh", ZSH, NODE))
        self.checks("01-02", self.suite("tests/run.sh", ZSH))
        self.checks("02-01", self.suite("tests/run.sh", CLAUDE))
        out = self.report()
        self.assertIn("## Not exercised here", out)
        section = out.split("## Not exercised here", 1)[1]
        alpha, beta = section.split("02 Beta", 1)
        self.assertIn("01 Alpha", alpha)
        self.assertEqual([alpha.count(r) for r in (ZSH, NODE, CLAUDE)], [1, 1, 0], section)
        self.assertEqual([beta.count(r) for r in (ZSH, NODE, CLAUDE)], [0, 0, 1], section)

    def test_report_without_skips_prints_no_not_exercised_section(self):
        self.checks("01-01", self.suite("tests/run.sh"))
        out = self.report()
        self.assertIn("01-01", out)
        self.assertNotIn("Not exercised here", out)


if __name__ == "__main__":
    unittest.main()
