import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
FIXTURES = HERE / "fixtures"
PRICING = str(FIXTURES / "pricing.json")


def report(*args, stdin=None):
    proc = subprocess.run(
        [sys.executable, str(HERE / "report.py"), "--pricing", PRICING, *args],
        capture_output=True, text=True, input=stdin,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


class SingleRun(unittest.TestCase):
    def setUp(self):
        self.out = report(str(FIXTURES / "run-a.json"))

    def test_draws_the_boxed_table(self):
        # model-a: input 100k x $1 + output 50k x $10 + cache read 1.2M x $0.1
        # + 1h cache write 100k x $2 = 0.10 + 0.50 + 0.12 + 0.20 = $0.92
        expected = "\n".join([
            "┌───────────────┬───────────────────────┐",
            "│               │ renovate-prs 1234abcd │",
            "├───────────────┼───────────────────────┤",
            "│ Wall clock    │ 116 s                 │",
            "├───────────────┼───────────────────────┤",
            "│ Turns         │ 10                    │",
            "├───────────────┼───────────────────────┤",
            "│ Tool calls    │ 15                    │",
            "├───────────────┼───────────────────────┤",
            "│ Input tokens  │ 100k                  │",
            "├───────────────┼───────────────────────┤",
            "│ Output tokens │ 50k                   │",
            "├───────────────┼───────────────────────┤",
            "│ Cache read    │ 1.2M                  │",
            "├───────────────┼───────────────────────┤",
            "│ Cache write   │ 100k                  │",
            "├───────────────┼───────────────────────┤",
            "│ Subagents     │ 0                     │",
            "├───────────────┼───────────────────────┤",
            "│ Cost          │ $0.92                 │",
            "└───────────────┴───────────────────────┘",
        ])
        self.assertTrue(self.out.startswith(expected + "\n"), self.out)

    def test_lists_tool_calls_with_mcp_tools_grouped_by_server(self):
        self.assertIn("\n".join([
            "Tool calls",
            "  Bash               12",
            "  datadog-mcp (MCP)   3  (search_logs 2, get_trace 1)",
        ]), self.out)

    def test_says_no_subagents(self):
        self.assertIn("Subagents: none", self.out)


class Breakdowns(unittest.TestCase):
    def test_lists_each_subagent_with_its_cost(self):
        out = report(str(FIXTURES / "run-b.json"))
        # 10k output tokens x $10/MTok = $0.10
        self.assertIn("Subagents (by cost)\n  general-purpose · Check PR · 2 turns · 1 tool calls · $0.10", out)

    def test_cost_is_unknown_when_a_model_has_no_price(self):
        run = (FIXTURES / "run-a.json").read_text().replace('"model-a"', '"model-z"')
        self.assertIn("│ Cost          │ unknown", report("-", stdin=run))


class Comparison(unittest.TestCase):
    def setUp(self):
        self.out = report(str(FIXTURES / "run-a.json"), str(FIXTURES / "run-b.json"))

    def test_puts_the_runs_side_by_side_with_the_change(self):
        # run-b costs 0.10 + 0.40 + 0.12 + 0.40 = $1.02; +$0.10 on $0.92 is +11%.
        for line in [
            "│               │ renovate-prs 1234abcd │ after paging │    Change     │",
            "│ Wall clock    │ 116 s                 │ 115 s        │ -1 s (-1%)    │",
            "│ Turns         │ 10                    │ 7            │ -3 (-30%)     │",
            "│ Input tokens  │ 100k                  │ 100k         │ same          │",
            "│ Output tokens │ 50k                   │ 40k          │ -10k (-20%)   │",
            "│ Cache write   │ 100k                  │ 200k         │ +100k (+100%) │",
            "│ Subagents     │ 0                     │ 1            │ +1            │",
            "│ Cost          │ $0.92                 │ $1.02        │ +$0.10 (+11%) │",
        ]:
            self.assertIn(line + "\n", self.out)

    def test_long_time_changes_read_in_minutes(self):
        slow = (FIXTURES / "run-b.json").read_text().replace('"wallClockSeconds": 115', '"wallClockSeconds": 1022')
        out = report(str(FIXTURES / "run-a.json"), "-", stdin=slow)
        # 1022 - 116 = 906 s
        self.assertIn("│ 17m 02s      │ +15m 06s (+781%) │", out)

    def test_gives_each_run_its_own_breakdown(self):
        self.assertIn("renovate-prs 1234abcd\nTool calls", self.out)
        self.assertIn("after paging\nTool calls", self.out)


class Save(unittest.TestCase):
    def test_saves_the_newest_run_under_its_label(self):
        with tempfile.TemporaryDirectory() as folder:
            out = report(str(FIXTURES / "run-b.json"), str(FIXTURES / "run-a.json"),
                         "--save", "Before paging", "--dir", folder)
            saved = Path(folder) / "before-paging.json"
            run = json.loads(saved.read_text())
            self.assertEqual(run["label"], "Before paging")
            self.assertEqual(run["sessionId"], "1234abcd-0000-0000-0000-000000000000")
            self.assertIn("createdAt", run)
            self.assertIn(str(saved), out)

    def test_records_the_cost_at_save_time(self):
        with tempfile.TemporaryDirectory() as folder:
            report(str(FIXTURES / "run-a.json"), "--save", "base", "--dir", folder)
            run = json.loads((Path(folder) / "base.json").read_text())
            self.assertEqual(run["costAtSave"], 0.92)
            self.assertEqual(run["pricingAsOf"], "2026-01-01")


class Repriced(unittest.TestCase):
    def test_notes_a_baseline_whose_cost_has_changed_since_it_was_saved(self):
        old = (FIXTURES / "run-b.json").read_text().replace(
            '"label": "after paging",', '"label": "after paging", "costAtSave": 1.5, "pricingAsOf": "2025-06-01",')
        out = report("-", str(FIXTURES / "run-a.json"), stdin=old)
        self.assertIn("after paging cost $1.50 at 2025-06-01 prices; the table uses 2026-01-01 prices.", out)

    def test_says_nothing_when_the_cost_is_unchanged(self):
        same = (FIXTURES / "run-b.json").read_text().replace(
            '"label": "after paging",', '"label": "after paging", "costAtSave": 1.02, "pricingAsOf": "2025-06-01",')
        self.assertNotIn("prices;", report("-", str(FIXTURES / "run-a.json"), stdin=same))


class PriceCheckHint(unittest.TestCase):
    """Fixture pricing.json is dated 2026-01-01."""

    def test_offers_a_check_when_prices_are_over_30_days_old(self):
        out = report(str(FIXTURES / "run-a.json"), "--today", "2026-03-01")
        self.assertIn("Prices are 59 days old (2026-01-01); check with: update_pricing.py model-a", out)

    def test_stays_quiet_when_prices_are_recent(self):
        out = report(str(FIXTURES / "run-a.json"), "--today", "2026-01-15")
        self.assertNotIn("update_pricing.py", out)

    def test_offers_a_check_for_a_model_with_no_price(self):
        run = (FIXTURES / "run-a.json").read_text().replace('"model-a"', '"model-z"')
        out = report("-", "--today", "2026-01-15", stdin=run)
        self.assertIn("No price for model-z; check with: update_pricing.py model-z", out)


if __name__ == "__main__":
    unittest.main()
