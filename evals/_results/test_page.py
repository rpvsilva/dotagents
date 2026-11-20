"""Tests for `page.py`: the results page on the fixtures, run as a command in a throwaway folder, never reading HOME."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
PAGE = HERE / "page.py"
GOLDEN = FIXTURES / "golden.md"
DAYS = {"aaaa111": "2026-11-01", "bbbb222": "2026-11-03"}

# Raw values planted in the fixtures, none of which may reach the page.
PLANTED = ("5e55105e-aaaa-bbbb-cccc-planted00001", "/Users/someone", "secret-client-repo", "secret-sandbox",
           "gh-999-secret-run", "planted-maintainer-bot", "planted-label", "planted-branch", "planted transcript",
           "planted subagent", "planted decision", "4242", "sessionId", "/Users/", "aaaa111", "bbbb222")


class PageTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.fx = self.tmp / "fixtures"
        shutil.copytree(FIXTURES, self.fx)
        for sha, day in DAYS.items():
            stamp = datetime.fromisoformat(day).replace(hour=12, tzinfo=timezone.utc).timestamp()
            for p in self.fx.glob(f"evals-results/*/{sha}/**/result.json"):
                os.utime(p, (stamp, stamp))
        self.env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.tmp / "no-home")}

    def page(self, *args, out="page.md"):
        cmd = [sys.executable, "-I", str(PAGE), *args, "--out", str(self.tmp / out)]
        got = subprocess.run(cmd, capture_output=True, text=True, env=self.env, stdin=subprocess.DEVNULL)
        self.assertEqual(got.returncode, 0, got.stderr)
        return (self.tmp / out).read_text(encoding="utf-8")

    def full(self, **kw):
        return self.page("--run", str(self.fx / "runs" / "gh-999-secret-run"), "--metrics", str(self.fx / "metrics.json"),
                         "--telemetry", str(self.fx / "session.json"), "--pricing", str(self.fx / "pricing.json"),
                         "--evals-results", str(self.fx / "evals-results"), **kw)

    def test_page_matches_the_golden_page_from_the_fixtures(self):
        self.assertEqual(self.full(), GOLDEN.read_text(encoding="utf-8"))

    def test_page_holds_none_of_the_planted_raw_values(self):
        page = self.full()
        for value in PLANTED:
            self.assertNotIn(value, page)

    def test_every_figure_row_has_a_date_a_model_and_a_version(self):
        rows = [line for line in self.full().splitlines() if line.startswith("| ") and not line.startswith(("| run |", "| case |"))]
        self.assertEqual(len(rows), 3)
        for row in rows:
            cells = [c.strip() for c in row.strip("|").split("|")]
            self.assertRegex(cells[1], r"^\d{4}-\d{2}-\d{2}$")
            self.assertRegex(cells[2], r"model-a")
            self.assertRegex(cells[3], r"^\d+\.\d+\.\d+$")

    def test_cost_per_task_names_its_basis_and_the_pricing_date(self):
        row = next(line for line in self.full().splitlines() if line.startswith("| run 1"))
        self.assertIn("$0.88", row)  # (2.0 USD session + 1.5 remote) over 4 done tasks
        self.assertIn("measured", row)
        self.assertIn("2026-01-01", row)

    def test_prs_merged_by_the_agent_is_a_count(self):
        row = next(line for line in self.full().splitlines() if line.startswith("| run 1"))
        self.assertTrue(row.rstrip(" |").endswith("| 2 | 3"), row)

    def test_eval_pass_rate_reads_the_latest_ref_of_each_case(self):
        page = self.full()
        self.assertIn("| skill-x/case-one | 2026-11-03 | model-a | 2.1.327 | 67% (2/3) | 3 |", page)
        self.assertIn("| skill-x/case-two | 2026-11-03 | model-a | 2.1.327 | unknown | 0 |", page)

    def test_missing_inputs_print_unknown_never_zero(self):
        empty = self.tmp / "empty-run"
        empty.mkdir()
        (self.tmp / "bare.json").write_text("{}")
        page = self.page("--run", str(empty), "--metrics", str(self.tmp / "bare.json"))
        row = next(line for line in page.splitlines() if line.startswith("| run 1"))
        self.assertEqual([c.strip() for c in row.strip("|").split("|")], ["run 1"] + ["unknown"] * 8)
        self.assertIn("## Eval pass rates\n\nunknown", page)

    def test_first_line_names_the_command(self):
        self.assertTrue(self.full().startswith("Written by `python3 -I evals/_results/page.py"))

    def test_second_run_on_the_same_inputs_is_byte_identical(self):
        self.assertEqual(self.full(out="a.md"), self.full(out="b.md"))
        self.assertEqual((self.tmp / "a.md").read_bytes(), (self.tmp / "b.md").read_bytes())

    def test_fixtures_hold_no_test_files(self):
        self.assertEqual(list(FIXTURES.rglob("test_*.py")), [])

    def test_no_input_exits_2(self):
        got = subprocess.run([sys.executable, "-I", str(PAGE), "--out", str(self.tmp / "x.md")], capture_output=True,
                             text=True, env=self.env, stdin=subprocess.DEVNULL)
        self.assertEqual(got.returncode, 2)


if __name__ == "__main__":
    unittest.main()
