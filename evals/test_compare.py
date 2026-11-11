"""Tests for `compare.py`: two refs run through `run.py`'s `run_cases` with saved runs reused, priced with the
current price table, and reported in Markdown (and HTML), with a fake `claude` on `PATH` that logs each call."""
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "_runner"))

import compare  # noqa: E402
import results  # noqa: E402
import test_run  # noqa: E402
from test_run import MODEL, STREAMS, PASS_TOKENS, RUBRIC, SCORES, RunSandbox, git, pricing, priced  # noqa: E402


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


class OrderTest(CompareSandbox):
    def test_the_refs_take_turns_one_run_number_at_a_time(self):
        made, real = [], compare.run.run_once

        def record(case, n, adapter, tool, model, co, *rest):
            made.append((co.sha, n))
            return real(case, n, adapter, tool, model, co, *rest)

        base, head = (git(self.repo, "rev-parse", r) for r in ("HEAD~1", "HEAD"))
        with mock.patch.object(compare.run, "run_once", record):
            got = self.compare([self.case("configure/a", runs=2), self.case("configure/b", runs=1)], runs=None)
        self.assertEqual(made,
                         [(base, 1), (base, 1), (head, 1), (head, 1), (base, 2), (head, 2)])
        self.assertEqual([r["run"] for r in got["base"]["runs"]], [1, 2, 1])
        self.assertEqual(got["base"]["sha"], base)


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

    def test_a_reported_zero_cost_run_stays_zero_not_unknown(self):
        events = [json.loads(l) for l in (STREAMS / "pass.jsonl").read_text().splitlines()]
        for e in events:
            if e.get("type") == "result":
                e["total_cost_usd"] = 0
                e["usage"] = {k: 0 for k in e.get("usage", {"input_tokens": 0})}
                e["modelUsage"] = {}
        stream = self.tmp / "zero.jsonl"
        stream.write_text("".join(json.dumps(e) + "\n" for e in events))
        self.passing_claude(stream)
        got = self.compare([self.case()])
        res = got["base"]["runs"][0]["result"]
        self.assertEqual((res["cost_usd"], res["cost_basis"]), (0.0, "reported"))
        self.assertNotIn("unknown", self.report.read_text())


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


    def test_without_runs_each_case_runs_its_own_count_and_the_report_names_both(self):
        got = self.compare([self.case("configure/a", runs=1), self.case("configure/b", runs=2)], runs=None)
        self.assertEqual(self.call_count(), 6)
        self.assertEqual(got["comparison"]["runs"], "1, 2")


class RefusedTest(CompareSandbox):
    """Each refusal comes before the base's runs are paid for, even when only the head's side is wrong."""

    def test_an_alias_head_model_is_refused_before_any_run(self):
        with self.assertRaises(compare.run.RunError) as caught:
            self.compare([self.case()], base_model=MODEL, head_model="sonnet")
        self.assertIn("--head-model 'sonnet'", str(caught.exception))
        self.assertEqual(self.call_count(), 0)
        self.assertFalse(self.report.exists())

    def test_a_tool_needing_model_with_only_a_base_model_is_refused_before_any_run(self):
        self.fake_bin("codex", f'echo call >>"{self.calls}"\n')
        with mock.patch.dict(os.environ, {"CODEX_API_KEY": "test"}), \
                self.assertRaises(compare.run.RunError) as caught:
            with open(self.out, "a") as out:
                compare.compare("codex", "HEAD~1", "HEAD", [self.case()], runs=1, base_model="gpt-5.5",
                                repo=self.repo, out=out, out_md=self.report)
        self.assertIn("give --model", str(caught.exception))
        self.assertEqual(self.call_count(), 0)
        self.assertFalse(self.report.exists())

    def test_an_unknown_tool_is_refused_as_a_run_error(self):
        with self.assertRaises(compare.run.RunError):
            with open(self.out, "a") as out:
                compare.compare("nope", "HEAD~1", "HEAD", [self.case()], runs=1, repo=self.repo, out=out,
                                out_md=self.report)
        self.assertFalse(self.report.exists())

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


class AuthStopTest(CompareSandbox):
    def reports(self):
        return list((self.results / "reports").rglob("*.md")) + ([self.report] if self.report.exists() else [])

    def test_an_auth_stop_on_the_base_never_runs_the_head_and_writes_no_report(self):
        self.claude("auth-failed.jsonl")
        got = self.compare([self.case()])
        self.assertEqual(got["base"]["stopped"], "auth")
        self.assertEqual(self.call_count(), 1)
        self.assertEqual(self.reports(), [])
        self.assertIsNone(got["out_md"])
        self.assertIn("auth failed for claude", self.printed())

    def test_an_auth_stop_on_the_head_writes_no_report(self):
        # The first call (the base) passes, the second (the head) fails on auth.
        marker = self.tmp / "base-done"
        self.fake_bin("claude", f'echo call >>"{self.calls}"\n'
                                f'if [ -e "{marker}" ]; then cat "{STREAMS / "auth-failed.jsonl"}"; exit 0; fi\n'
                                f'touch "{marker}"\nmkdir -p .agents && echo "{{}}" >.agents/settings.json\n'
                                f'cat "{STREAMS / "pass.jsonl"}"\n')
        got = self.compare([self.case()])
        self.assertEqual((got["base"]["stopped"], got["head"]["stopped"]), (None, "auth"))
        self.assertEqual(self.call_count(), 2)
        self.assertEqual(self.reports(), [])
        self.assertIsNone(got["out_md"])

    def test_an_auth_stop_on_the_head_of_run_1_never_starts_run_2_of_the_base(self):
        marker = self.tmp / "base-done"
        self.fake_bin("claude", f'echo call >>"{self.calls}"\n'
                                f'if [ -e "{marker}" ]; then cat "{STREAMS / "auth-failed.jsonl"}"; exit 0; fi\n'
                                f'touch "{marker}"\nmkdir -p .agents && echo "{{}}" >.agents/settings.json\n'
                                f'cat "{STREAMS / "pass.jsonl"}"\n')
        got = self.compare([self.case(runs=2)], runs=None)
        self.assertEqual((got["base"]["stopped"], got["head"]["stopped"]), (None, "auth"))
        self.assertEqual(self.call_count(), 2)
        self.assertEqual([r["run"] for r in got["base"]["runs"]], [1])
        self.assertEqual(self.reports(), [])
        self.assertIsNone(got["comparison"])


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

    def test_cli_exits_3_on_an_auth_stop_with_no_report(self):
        self.claude("auth-failed.jsonl")
        self.case("configure/a")
        out = self.cli("--tool", "claude", "--base", "HEAD", "--head", "HEAD", "--runs", "1",
                       "--evals", str(self.evals), "configure/a")
        self.assertEqual(out.returncode, 3, out.stderr)
        self.assertIn("auth failed for claude", out.stderr)
        self.assertEqual(out.stdout, "")
        self.assertEqual(list((self.results / "reports").rglob("*.md")), [])

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


class RemoteCompareTest(RunSandbox):
    """`--base-remote`, `--head-remote` and `--remote`: a side runs through the stub `remote-agent` of `RemoteTest`."""
    remote_agent = test_run.RemoteTest.remote_agent
    calls_of_remote_agent = test_run.RemoteTest.calls_of_remote_agent

    def setUp(self):
        super().setUp()
        self.ra_calls = self.tmp / "ra-calls"
        self.ra_argv = self.tmp / "ra-argv"
        self.ra_env = self.tmp / "ra-env"
        self.ra_stdin = self.tmp / "ra-stdin"
        self.ra_cwd = self.tmp / "ra-cwd"
        self.passing_claude()
        self.remote_agent()
        self.repo = self.fake_repo("exit 0\n")
        self.report = self.tmp / "report.md"

    def cli(self, *args):
        return subprocess.run([sys.executable, "-I", str(HERE / "compare.py"), *args], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, env=os.environ.copy())

    def compare(self, cases, base="HEAD", head="HEAD", **kw):
        kw.setdefault("runs", 1)
        kw.setdefault("out_md", self.report)
        with open(self.out, "a") as out:
            return compare.compare("claude", base, head, cases, repo=self.repo, out=out, **kw)

    def test_the_same_ref_with_head_remote_calls_remote_agent_once_and_claude_once(self):
        got = self.compare([self.case()], head_remote=True)
        self.assertEqual(self.calls_of_remote_agent(), ["call"])
        self.assertEqual(self.call_count(), 1)
        self.assertEqual(got["base"]["runs"][0]["result"]["remote"], False)
        self.assertEqual(got["head"]["runs"][0]["result"]["backend"], "docker")

    def test_a_second_compare_reuses_both_the_local_and_the_remote_run(self):
        case = self.case()
        self.compare([case], head_remote=True)
        again = self.compare([case], head_remote=True)
        self.assertEqual(self.calls_of_remote_agent(), ["call"])
        self.assertEqual(self.call_count(), 1)
        self.assertTrue(all(r["reused"] for side in ("base", "head") for r in again[side]["runs"]))

    def test_each_side_is_labelled_local_or_remote_in_both_formats(self):
        html = self.tmp / "report.html"
        got = self.compare([self.case()], head_remote=True, out_html=html)
        self.assertEqual((got["comparison"]["base"]["where"], got["comparison"]["head"]["where"]),
                         ("local", "remote (docker)"))
        md = self.report.read_text()
        self.assertRegex(md, r"- Base: .*, local\n")
        self.assertRegex(md, r"- Head: .*, remote \(docker\)\n")
        self.assertIn("remote (docker)", html.read_text())
        self.assertIn(", local<", html.read_text())

    def test_remote_for_both_sides_labels_both_remote(self):
        got = self.compare([self.case()], remote=True)
        self.assertEqual(self.calls_of_remote_agent(), ["call"])
        self.assertEqual([got["comparison"][s]["where"] for s in ("base", "head")], ["remote (docker)"] * 2)

    def test_the_default_report_path_gains_the_backend_only_for_a_remote_side(self):
        case = self.case()
        local = self.compare([case], out_md=None, results_root=self.tmp / "s")["out_md"]
        remote = self.compare([case], out_md=None, results_root=self.tmp / "s", head_remote=True)["out_md"]
        sha = git(self.repo, "rev-parse", "HEAD")[:12]
        self.assertEqual(local.name, f"claude-{sha}-{sha}.md")
        self.assertEqual(remote.name, f"claude-{sha}-{sha}@docker.md")
        base_side = self.compare([case], out_md=None, results_root=self.tmp / "s", base_remote=True)["out_md"]
        self.assertEqual(base_side.name, f"claude-{sha}@docker-{sha}.md")

    def test_an_all_local_comparison_carries_no_where(self):
        got = self.compare([self.case()])
        self.assertNotIn("where", got["comparison"]["base"])
        self.assertNotIn("local", self.report.read_text().split("|")[0])

    def test_remote_with_another_tool_is_refused_before_the_base_runs(self):
        case = self.case()
        for kw in ({"remote": True}, {"base_remote": True}, {"head_remote": True}):
            with self.subTest(kw=kw):
                with open(self.out, "a") as out, mock.patch.object(compare.run, "run_cases") as ran, \
                        self.assertRaises(compare.run.RunError) as caught:
                    compare.compare("codex", "HEAD", "HEAD", [case], model=MODEL, repo=self.repo, out=out,
                                    out_md=self.report, **kw)
                self.assertIn("claude", str(caught.exception))
                ran.assert_not_called()
        self.assertEqual(self.call_count(), 0)
        self.assertEqual(self.calls_of_remote_agent(), [])

    def refused_before_any_run(self, case, message, **kw):
        with self.assertRaises(compare.run.RunError) as caught:
            self.compare([case], **kw)
        self.assertIn(message, str(caught.exception))
        self.assertEqual(self.call_count(), 0)
        self.assertEqual(self.calls_of_remote_agent(), [])

    def test_a_remote_side_with_a_backend_that_is_not_ready_is_refused_before_the_base_runs(self):
        self.remote_agent(ready="echo 'no token' >&2; exit 1")
        case = self.case()
        for kw in ({"head_remote": True}, {"base_remote": True}, {"remote": True}):
            with self.subTest(kw=kw):
                self.refused_before_any_run(case, "remote-agent is not ready: no token", **kw)

    def test_a_case_set_the_head_cannot_run_remotely_is_refused_before_the_base_runs(self):
        case = self.case()
        (case / "prompt.md").write_text("Look in {home}\n")
        self.refused_before_any_run(case, "no case can run remotely", head_remote=True)

    def test_a_fixture_holding_eval_scratch_is_refused_for_a_remote_head_before_the_base_runs(self):
        case = self.case()
        (case / "fixture" / ".eval-scratch").mkdir()
        (case / "fixture" / ".eval-scratch" / "x").write_text("x")
        self.refused_before_any_run(case, ".eval-scratch", head_remote=True)

    def test_the_cli_exits_2_for_a_remote_head_whose_backend_is_not_ready(self):
        self.remote_agent(ready="echo 'no token' >&2; exit 1")
        self.case("configure/a")
        out = self.cli("--tool", "claude", "--head-remote", "--runs", "1", "--base", "HEAD", "--head", "HEAD",
                       "--evals", str(self.evals), "--results", str(self.tmp / "s"), "configure/a")
        self.assertEqual(out.returncode, 2, out.stderr)
        self.assertIn("compare.py: remote-agent is not ready", out.stderr)
        self.assertEqual(self.call_count(), 0)

    def test_the_cli_takes_the_three_flags_and_exits_2_for_codex(self):
        self.case("configure/a")
        base = ("--base", "HEAD", "--head", "HEAD", "--evals", str(self.evals), "--results", str(self.tmp / "s"))
        out = self.cli("--tool", "codex", "--model", MODEL, "--head-remote", *base, "configure/a")
        self.assertEqual(out.returncode, 2, out.stderr)
        self.assertIn("compare.py: --remote needs --tool claude", out.stderr)
        out = self.cli("--tool", "claude", "--head-remote", "--runs", "1", *base, "configure/a")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("remote (docker)", out.stdout)


if __name__ == "__main__":
    unittest.main()
