"""Tests for `compare.py`: two refs run through `run.py`'s `run_cases` with saved runs reused, priced with the
current price table, and reported in Markdown (and HTML), with a fake `claude` on `PATH` that logs each call."""
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "_runner"))

import compare  # noqa: E402
import results  # noqa: E402
from test_run import MODEL, PASS_TOKENS, RUBRIC, SCORES, RunSandbox, git, pricing, priced  # noqa: E402


class CompareSandbox(RunSandbox):
    def setUp(self):
        super().setUp()
        self.passing_claude()
        # Two commits with the same files: two refs whose runs are saved apart.
        self.repo = self.fake_repo("exit 0\n")
        git(self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "commit", "-q",
            "--allow-empty", "-m", "head")
        self.report = self.tmp / "report.md"

    def compare(self, cases, **kw):
        kw.setdefault("runs", 1)
        kw.setdefault("out_md", self.report)
        with open(self.out, "a") as out:
            return compare.compare("claude", "HEAD~1", "HEAD", cases, repo=self.repo, out=out, **kw)

    def side_calls(self, got, side):
        return [r for r in got[side]["runs"] if not r["reused"]]


class ReuseTest(CompareSandbox):
    def test_a_second_compare_with_the_same_base_makes_no_tool_call_for_the_base(self):
        case = self.case()
        first = self.compare([case], runs=2)
        self.assertEqual(self.call_count(), 4)
        self.assertEqual(len(self.side_calls(first, "base")), 2)
        again = self.compare([case], runs=2)
        self.assertEqual(self.call_count(), 4)
        self.assertEqual(self.side_calls(again, "base"), [])
        self.assertTrue(all(r["reused"] for r in again["base"]["runs"]))

    def test_a_new_head_runs_only_the_head(self):
        case = self.case()
        self.compare([case])
        git(self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "commit", "-q",
            "--allow-empty", "-m", "newer head")
        got = self.compare([case])
        self.assertEqual(self.call_count(), 3)
        self.assertEqual(self.side_calls(got, "base"), [])
        self.assertEqual(len(self.side_calls(got, "head")), 1)

    def test_a_changed_case_file_runs_that_case_again(self):
        a, b = self.case("configure/a"), self.case("configure/b")
        self.compare([a, b])
        self.assertEqual(self.call_count(), 4)
        (a / "prompt.md").write_text("Something else\n")
        got = self.compare([a, b])
        self.assertEqual(self.call_count(), 6)
        made = sorted((side, r["case"]) for side in ("base", "head") for r in self.side_calls(got, side))
        self.assertEqual(made, [("base", "configure/a"), ("head", "configure/a")])

    def test_an_edited_check_or_rubric_reuses_both_refs_with_no_tool_call_for_the_case(self):
        self.judging_claude(SCORES)
        case = self.rubric_case()
        self.compare([case])
        self.assertEqual(self.roles_called(), ["case", "judge", "case", "judge"])
        (case / "checks" / "settings-written.sh").write_text("#!/usr/bin/env bash\nexit 1\n")
        got = self.compare([case])
        self.assertEqual(self.roles_called(), ["case", "judge", "case", "judge"])
        for side in ("base", "head"):
            self.assertEqual(self.side_calls(got, side), [])
            self.assertFalse(got[side]["runs"][0]["result"]["passed"])
        (case / "rubric.md").write_text(RUBRIC + "- clarity: the answer says what changed.\n")
        self.judging_claude({**SCORES, "clarity": {"score": 2, "reason": "Terse."}})
        got = self.compare([case])
        self.assertEqual(self.roles_called().count("case"), 2)
        self.assertEqual(self.roles_called().count("judge"), 4)
        for side in ("base", "head"):
            self.assertEqual(self.side_calls(got, side), [])
            self.assertIn("clarity", got[side]["runs"][0]["result"]["rubric"]["scores"])

    def test_the_same_ref_on_both_sides_runs_once(self):
        case = self.case()
        with open(self.out, "a") as out:
            compare.compare("claude", "HEAD", "HEAD", [case], runs=1, repo=self.repo, out=out, out_md=self.report)
        self.assertEqual(self.call_count(), 1)


class PricingTest(CompareSandbox):
    def test_both_sides_are_priced_with_the_current_table_and_its_date_is_in_the_report(self):
        case = self.case()
        first = self.compare([case])
        for side in ("base", "head"):
            d = first[side]["runs"][0]["dir"]
            saved = json.loads((d / results.RESULT).read_text())
            saved.update(cost_usd=99.0, pricing_as_of="1999-01-01")
            (d / results.RESULT).write_text(json.dumps(saved))
        got = self.compare([case])
        self.assertEqual(self.call_count(), 2)
        for side in ("base", "head"):
            res = got[side]["runs"][0]["result"]
            self.assertEqual(res["cost_usd"], priced(PASS_TOKENS))
            self.assertEqual((res["cost_basis"], res["pricing_as_of"]), ("pricing", pricing()["asOf"]))
        self.assertEqual(got["comparison"]["pricing_as_of"], pricing()["asOf"])
        md = self.report.read_text()
        self.assertIn(f"Prices as of: {pricing()['asOf']}", md)
        self.assertNotIn("1999-01-01", md)
        self.assertNotIn("$99", md)
        self.assertIn(f"${priced(PASS_TOKENS):.2f}", md)

    def test_a_run_with_no_tokens_costs_unknown_not_zero(self):
        self.claude(None, "mkdir -p .agents && echo '{}' >.agents/settings.json\n")
        got = self.compare([self.case()])
        res = got["base"]["runs"][0]["result"]
        self.assertIsNone(res["cost_usd"])
        self.assertEqual(res["cost_basis"], "unknown")
        self.assertIn("unknown", self.report.read_text())


class ReportTest(CompareSandbox):
    def test_the_comparison_names_tool_model_refs_shas_runs_and_verdicts(self):
        got = self.compare([self.case()], runs=2)
        c = got["comparison"]
        self.assertEqual((c["tool"], c["model"], c["runs"]), ("claude", MODEL, 2))
        self.assertEqual(c["base"], {"ref": "HEAD~1", "sha": git(self.repo, "rev-parse", "HEAD~1")})
        self.assertEqual(c["head"], {"ref": "HEAD", "sha": git(self.repo, "rev-parse", "HEAD")})
        self.assertEqual(c["verdict"]["cases"]["configure/quick"]["verdict"], "noise")
        self.assertEqual(c["verdict"]["cases"]["configure/quick"]["metrics"]["pass_rate"]["base"], 1.0)

    def test_the_markdown_is_printed_and_written_to_out(self):
        self.compare([self.case()])
        md = self.report.read_text()
        self.assertTrue(md.startswith("# Eval comparison"))
        self.assertIn("| configure/quick |", md)
        self.assertIn(md, self.printed())

    def test_html_writes_the_page_too(self):
        page = self.tmp / "report.html"
        self.compare([self.case()], out_html=page)
        self.assertTrue(page.read_text().startswith("<!DOCTYPE html>"))
        self.assertIn("configure/quick", page.read_text())
        self.assertTrue(self.report.is_file())

    def test_the_default_report_is_under_the_results_root(self):
        got = self.compare([self.case()], out_md=None)
        path = Path(got["out_md"])
        self.assertTrue(path.is_relative_to(self.results), path)
        self.assertTrue(path.read_text().startswith("# Eval comparison"))

    def test_a_report_path_inside_the_repo_is_refused_before_any_run(self):
        case = self.case()
        for kw in ({"out_md": REPO / "report.md"}, {"out_html": REPO / "evals" / "r.html"}):
            with self.subTest(**{k: str(v) for k, v in kw.items()}), self.assertRaises(compare.run.RunError):
                self.compare([case], **kw)
        self.assertEqual(self.call_count(), 0)
        self.assertFalse((REPO / "report.md").exists())

    def test_a_bad_case_on_the_head_only_is_refused_before_any_run(self):
        (self.repo / "scripts" / "parts.sh").write_text("echo configure; echo global\n")
        git(self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "commit", "-qam",
            "head pulls in global")
        with self.assertRaises(compare.run.RunError) as caught:
            self.compare([self.case()])
        self.assertIn("global", str(caught.exception))
        self.assertEqual(self.call_count(), 0)
        self.assertFalse(self.report.exists())

    def test_different_models_per_ref_are_named_per_side(self):
        got = self.compare([self.case()], base_model="claude-opus-5-5", head_model=MODEL)
        c = got["comparison"]
        self.assertEqual((c["base"]["model"], c["head"]["model"]), ("claude-opus-5-5", MODEL))
        self.assertEqual(re.findall(r"--model (\S+)", self.argv.read_text()), ["claude-opus-5-5", MODEL])
        self.assertIn("model claude-opus-5-5", self.report.read_text())

    def test_model_sets_both_sides(self):
        got = self.compare([self.case()], model="claude-opus-5-5")
        self.assertEqual(got["comparison"]["model"], "claude-opus-5-5")
        self.assertNotIn("model", got["comparison"]["base"])
        self.assertEqual(re.findall(r"--model (\S+)", self.argv.read_text()), ["claude-opus-5-5"] * 2)


class BudgetTest(CompareSandbox):
    def test_one_budget_covers_both_refs(self):
        got = self.compare([self.case()], budget_usd=priced(PASS_TOKENS) * 0.5)
        self.assertEqual(self.call_count(), 1)
        self.assertEqual(len(got["base"]["runs"]), 1)
        self.assertEqual(got["head"]["skipped"], [("configure/quick", 1)])
        self.assertTrue(self.report.is_file())
        self.assertIn("unknown", self.report.read_text())

    def test_a_budget_for_both_runs_both(self):
        got = self.compare([self.case()], budget_usd=priced(PASS_TOKENS) * 1.5)
        self.assertEqual(self.call_count(), 2)
        self.assertEqual(got["head"]["skipped"], [])


class CliTest(RunSandbox):
    def cli(self, *args):
        return subprocess.run([sys.executable, "-I", str(HERE / "compare.py"), *args], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, env=os.environ.copy())

    def test_cli_prints_the_report_and_writes_out_and_html(self):
        self.passing_claude()
        self.case("configure/a")
        md, page = self.tmp / "r.md", self.tmp / "r.html"
        out = self.cli("--tool", "claude", "--base", "HEAD", "--head", "HEAD", "--runs", "1", "--out", str(md),
                       "--html", str(page), "--evals", str(self.evals), "configure/a")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout, md.read_text())
        self.assertIn("| configure/a |", out.stdout)
        self.assertTrue(page.read_text().startswith("<!DOCTYPE html>"))
        self.assertEqual(self.call_count(), 1)

    def test_cli_all_writes_the_default_report_under_results(self):
        self.passing_claude()
        self.case("configure/a")
        self.case("configure/b")
        out = self.cli("--tool", "claude", "--base", "HEAD", "--head", "HEAD", "--runs", "1",
                       "--evals", str(self.evals), "--all")
        self.assertEqual(out.returncode, 0, out.stderr)
        reports = list((self.results / "reports").rglob("*.md"))
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0].read_text(), out.stdout)
        self.assertIn(str(reports[0]), out.stderr)

    def test_cli_exits_1_when_the_budget_skipped_runs(self):
        self.passing_claude()
        self.case("configure/a")
        out = self.cli("--tool", "claude", "--base", "HEAD", "--head", "HEAD", "--runs", "2", "--budget-usd",
                       "0.000001", "--evals", str(self.evals), "configure/a")
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("skipped configure/a run 2", out.stderr)
        self.assertTrue(out.stdout.startswith("# Eval comparison"))

    def test_every_flag_of_help_is_documented_in_the_compare_section(self):
        out = self.cli("--help")
        flags = set(re.findall(r"(?<![\w-])--?[a-z][\w-]*", out.stdout))
        self.assertIn("--head-model", flags)
        doc = (REPO / "docs" / "evals.md").read_text()
        self.assertTrue("\n## Compare two refs\n" in doc, "docs/evals.md has no Compare two refs section")
        section = doc.split("\n## Compare two refs\n", 1)[1].split("\n## ", 1)[0]
        self.assertEqual(sorted(f for f in flags if f"`{f}" not in section), [])

    def test_cli_exits_2_on_a_usage_error_before_any_run(self):
        self.passing_claude()
        self.case("configure/a")
        base = ("--tool", "claude", "--evals", str(self.evals))
        for args in (("--base", "HEAD", "--head", "HEAD", "--all", "configure/a"),
                     ("--base", "HEAD", "--head", "HEAD"),
                     ("--head", "HEAD", "configure/a"),
                     ("--base", "HEAD", "--head", "HEAD", "--runs", "0", "configure/a"),
                     ("--base", "HEAD", "--head", "HEAD", "--budget-usd", "0", "configure/a")):
            with self.subTest(args=args):
                out = self.cli(*base, *args)
                self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
                self.assertIn("usage:", out.stderr)
        for args in (("--base", "HEAD", "--head", "HEAD", "configure/nope"),
                     ("--base", "no-such-ref", "--head", "HEAD", "configure/a"),
                     ("--base", "HEAD", "--head", "HEAD", "--model", "sonnet", "configure/a"),
                     ("--base", "HEAD", "--head", "HEAD", "--out", str(REPO / "r.md"), "configure/a")):
            with self.subTest(args=args):
                out = self.cli(*base, *args)
                self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
                self.assertIn("compare.py:", out.stderr)
                self.assertNotIn("Traceback", out.stderr)
        self.assertEqual(self.call_count(), 0)
        self.assertFalse((REPO / "r.md").exists())


if __name__ == "__main__":
    unittest.main()
