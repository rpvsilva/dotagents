import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from report import table

HERE = Path(__file__).parent
FIXTURES = HERE / "fixtures"
PRICING = str(FIXTURES / "pricing.json")
RUN = str(FIXTURES / "run.json")


def report(*args, stdin=None, cwd=None):
    proc = subprocess.run(
        [sys.executable, str(HERE / "report.py"), "--pricing", PRICING, *args],
        capture_output=True, encoding="utf-8", input=stdin, cwd=cwd,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def one_span(run_text):
    run = json.loads(run_text)
    run["spans"] = run["spans"][:1]
    run["wallClockSeconds"] = run["spans"][0]["wallClockSeconds"]
    return json.dumps(run)


def unpriced(run_text):
    return run_text.replace('"model-a"', '"model-z"')


class Table(unittest.TestCase):
    """run.json: grill-with-docs then renovate-prs, priced at model-a's $1 / $10 / $0.1 / $2 (1h write)."""

    @classmethod
    def setUpClass(cls):
        cls.out = report(RUN)

    def test_one_row_per_span_and_a_total_row(self):
        # grill-with-docs: 0.10 input + 0.50 output + 0.12 cache read + 0.20 cache write = $0.92
        # renovate-prs:    0.10 + 0.40 + 0.12 + 0.40 = $1.02 (its subagents' tokens are included)
        # Total time is the run's own wall clock, since a background subagent can outlast its span.
        expected = "\n".join([
            "┌─────────────────┬────────┬───────┬───────┬──────┬─────┬─────────┬─────────┬────────┬───────┐",
            "│ Span            │ Time   │ Turns │ Tools │ In   │ Out │ Cache R │ Cache W │ Agents │ Cost  │",
            "├─────────────────┼────────┼───────┼───────┼──────┼─────┼─────────┼─────────┼────────┼───────┤",
            "│ grill-with-docs │ 116 s  │ 10    │ 15    │ 100k │ 50k │ 1.2M    │ 100k    │ 0      │ $0.92 │",
            "│ renovate-prs    │ 60 s   │ 7     │ 12    │ 100k │ 40k │ 1.2M    │ 200k    │ 2      │ $1.02 │",
            "├─────────────────┼────────┼───────┼───────┼──────┼─────┼─────────┼─────────┼────────┼───────┤",
            "│ Total           │ 3m 00s │ 17    │ 27    │ 200k │ 90k │ 2.4M    │ 300k    │ 2      │ $1.94 │",
            "└─────────────────┴────────┴───────┴───────┴──────┴─────┴─────────┴─────────┴────────┴───────┘",
            "Tools = tool calls · In/Out = input/output tokens · Cache R/W = cache read/write · Agents = subagents",
        ])
        self.assertTrue(self.out.startswith(expected + "\n"), self.out)

    def test_a_single_span_has_no_total_row(self):
        out = report("-", stdin=one_span(Path(RUN).read_text()))
        self.assertIn("│ grill-with-docs │", out)
        self.assertNotIn("Total", out)

    def test_cost_is_unknown_when_a_model_has_no_price(self):
        out = report("-", stdin=unpriced(Path(RUN).read_text()))
        self.assertIn("│ unknown │", out)


class Breakdowns(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = report(RUN)

    def test_lists_each_spans_tool_calls_with_mcp_tools_grouped_by_server(self):
        self.assertIn("grill-with-docs\n"
                      "  Tool calls: Bash 12, datadog-mcp (MCP) 3 (search_logs 2, get_trace 1)\n"
                      "  Subagents: none\n", self.out)

    def test_lists_each_spans_subagents_by_cost(self):
        # Find lock files: 30k output x $10/MTok = $0.30; Fix PR 731: 10k = $0.10.
        self.assertIn("renovate-prs\n"
                      "  Tool calls: Bash 9, Agent 2, Read 1\n"
                      "  Subagents:\n"
                      "    Explore · Find lock files · 1 turns · 0 tool calls · $0.30\n"
                      "    general-purpose · Fix PR 731 · 2 turns · 1 tool calls · $0.10\n", self.out)


class Save(unittest.TestCase):
    def test_saves_the_run_with_its_label_cost_and_price_date(self):
        with tempfile.TemporaryDirectory() as folder:
            out = report(RUN, "--save", "Before paging", "--dir", folder)
            saved = Path(folder) / "before-paging.json"
            run = json.loads(saved.read_text())
            self.assertEqual(run["label"], "Before paging")
            self.assertEqual([s["name"] for s in run["spans"]], ["grill-with-docs", "renovate-prs"])
            self.assertEqual(run["cost"], 1.94)
            self.assertEqual(run["pricingAsOf"], "2026-01-01")
            self.assertIn("createdAt", run)
            self.assertIn(f"Saved {saved}", out)

    def test_saves_at_the_repo_root_from_any_folder(self):
        with tempfile.TemporaryDirectory() as repo:
            subprocess.run(["git", "init", "-q", repo], check=True)
            (Path(repo) / "src").mkdir()
            report(RUN, "--save", "base", cwd=Path(repo) / "src")
            self.assertTrue((Path(repo) / ".agents" / "telemetry" / "base.json").is_file())

    def test_saved_runs_stay_out_of_git(self):
        with tempfile.TemporaryDirectory() as repo:
            subprocess.run(["git", "init", "-q", repo], check=True)
            report(RUN, "--save", "base", cwd=repo)
            status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=repo,
                                    capture_output=True, encoding="utf-8", check=True).stdout
            self.assertTrue((Path(repo) / ".agents" / "telemetry" / "base.json").is_file())
            self.assertEqual(status, "")

    def test_an_existing_gitignore_in_the_folder_is_kept(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / ".gitignore").write_text("mine\n")
            report(RUN, "--save", "base", "--dir", folder)
            self.assertEqual((Path(folder) / ".gitignore").read_text(), "mine\n")


RUN_STEPS = str(FIXTURES / "run-steps.json")
EXTRACTOR = HERE.parents[1] / "adapters" / "claude" / "telemetry"


def extracted_run():
    """The run the Claude Code extractor writes for its steps fixture."""
    proc = subprocess.run([sys.executable, str(EXTRACTOR / "extract.py"), "--session", str(EXTRACTOR / "fixtures" / "steps.jsonl")],
                          capture_output=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


class RunWithSteps(unittest.TestCase):
    """run-steps.json: a version 4 run with steps, shaped like the extractor's output, plus repo, date and
    whole, which ticket 01's extractor adds (the extractor on this branch may not write them yet)."""

    def test_fixture_has_steps_in_the_extractor_shape(self):
        # Tied to the extractor's real output, so a field it adds, drops or renames fails here.
        run, real = json.loads(Path(RUN_STEPS).read_text()), extracted_run()
        self.assertEqual(run["schemaVersion"], real["schemaVersion"])
        self.assertLessEqual(set(real), set(run))
        span_keys = {k for s in real["spans"] for k in s}
        for span in run["spans"]:
            self.assertEqual(set(span), span_keys, span["name"])
        step_keys = {k for s in real["steps"] for k in s} - {"background"}
        span_names = {s["name"] for s in run["spans"]}
        self.assertGreaterEqual(len(span_names), 2)
        for step in run["steps"]:
            self.assertEqual(set(step) - {"background"}, step_keys, step)
            self.assertIn(step["span"], span_names)
        self.assertEqual(run["steps"], sorted(run["steps"], key=lambda s: s["start"]))
        self.assertTrue(any(s["subagent"] for s in run["steps"]))
        self.assertTrue(any(s["failed"] for s in run["steps"]))

    def test_renders_the_span_table(self):
        # grill-with-docs: 0.05 + 0.20 + 0.06 + 0.10 = $0.41; renovate-prs: 0.06 + 0.30 + 0.08 + 0.20 = $0.64
        out = report(RUN_STEPS)
        self.assertIn("│ grill-with-docs │ 90 s   │ 6     │ 4     │ 50k  │ 20k │ 600k    │ 50k     │ 0      │ $0.41 │", out)
        self.assertIn("│ renovate-prs    │ 90 s   │ 5     │ 4     │ 60k  │ 30k │ 800k    │ 100k    │ 1      │ $0.64 │", out)
        self.assertIn("│ Total           │ 3m 00s │ 11    │ 8     │ 110k │ 50k │ 1.4M    │ 150k    │ 1      │ $1.05 │", out)

    def test_save_keeps_steps_repo_date_and_whole(self):
        source = json.loads(Path(RUN_STEPS).read_text())
        with tempfile.TemporaryDirectory() as folder:
            report(RUN_STEPS, "--save", "x", "--dir", folder)
            run = json.loads((Path(folder) / "x.json").read_text())
        for key in ("steps", "repo", "date", "whole", "schemaVersion"):
            self.assertEqual(run[key], source[key], key)


RUN_ROLES = str(FIXTURES / "run-roles.json")


def role_rows(out):
    """The by-role table's rows as {role: [runs, turns, cost, avg context]}, in printed order."""
    rows = {}
    for line in out.splitlines():
        cells = [c.strip() for c in line.split("│")[1:-1]]
        if cells and cells[0] != "Role":
            rows[cells[0]] = cells[1:]
    return rows


def schema_3(run_text, descriptions):
    """The run as schema 3 saved it: no role or cost per subagent, free-text descriptions."""
    run = json.loads(run_text)
    run["schemaVersion"] = 3
    del run["pricingAsOf"]
    agents = [a for span in run["spans"] for a in span["subagents"]]
    assert len(agents) == len(descriptions)
    for agent, description in zip(agents, descriptions):
        del agent["role"], agent["cost"]
        agent["description"] = description
    return json.dumps(run)


class ByRole(unittest.TestCase):
    """run-roles.json: build-epic (4 subagents) then ship-pr (1), priced at model-a's $1 / $10 / $0.1 / $2 (1h write)."""

    def test_one_row_per_role_after_the_orchestrator_by_cost(self):
        # implement: 0.58 + 0.32; review: 0.01 + 0.05 + 0.05 + 0.10; other: 0.01 + 0.05 + 0.02; fix: 0.01 + 0.02 + 0.02
        rows = role_rows(report(RUN_ROLES, "--by-role"))
        self.assertEqual(list(rows), ["orchestrator", "implement", "review", "other", "fix", "Total"])
        self.assertEqual(rows["implement"], ["2", "50", "$0.90", ""])
        self.assertEqual(rows["review"], ["1", "10", "$0.21", ""])
        self.assertEqual(rows["other"], ["1", "5", "$0.08", ""])
        self.assertEqual(rows["fix"], ["1", "5", "$0.05", ""])

    def test_the_orchestrator_is_each_span_minus_its_subagents(self):
        # Turns 100 - 65 + 15 - 5 = 45; input 25k + 5k, output 10k + 3k, cache read 2.8M + 0.5M, write 100k:
        # 0.03 + 0.13 + 0.33 + 0.20 = $0.69. Average context: 3.3M cache read / 45 turns = 73,333.
        rows = role_rows(report(RUN_ROLES, "--by-role"))
        self.assertEqual(rows["orchestrator"], ["1", "45", "$0.69", "73k"])
        self.assertEqual(rows["Total"], ["6", "115", "$1.93", ""])

    def test_the_total_adds_remote_runs_and_counts_those_without_cost(self):
        # fixtures/remote: one run at $1.25 and 40 turns, one lost (nulls), one failed with 12 turns and no cost.
        out = report(RUN_ROLES, "--by-role", "--run-folder", str(FIXTURES))
        rows = role_rows(out)
        self.assertEqual(rows["remote"], ["3", "52", "$1.25", ""])
        self.assertEqual(rows["Total"], ["9", "167", "$3.18", ""])
        self.assertEqual(list(rows)[-2:], ["remote", "Total"])
        self.assertIn("\n2 remote runs without cost\n", out)

    def test_a_run_folder_without_remote_runs_adds_no_remote_row(self):
        with tempfile.TemporaryDirectory() as folder:
            out = report(RUN_ROLES, "--by-role", "--run-folder", folder)
        self.assertNotIn("remote", role_rows(out))
        self.assertNotIn("without cost", out)

    def test_an_unreadable_remote_result_counts_as_a_run_without_cost(self):
        with tempfile.TemporaryDirectory() as folder:
            remote = Path(folder) / "remote"
            remote.mkdir()
            (remote / "01-01.json").write_text("{not json")
            (remote / "01-02.json").write_text('["a list"]')
            (remote / "01-03.json").write_text('{"num_turns": 3, "total_cost_usd": 0.5}')
            out = report(RUN_ROLES, "--by-role", "--run-folder", folder)
        self.assertEqual(role_rows(out)["remote"], ["3", "3", "$0.50", ""])
        self.assertIn("\n2 remote runs without cost\n", out)

    def test_a_schema_3_run_without_roles_falls_back_to_type_and_keywords(self):
        run = schema_3(Path(RUN_ROLES).read_text(),
                       ["Implement 01-01", "Implement 01-02", "Re-review PR 12", "Look around", "Fix PR 12"])
        rows = role_rows(report("-", "--by-role", stdin=run))
        self.assertEqual(rows["implement"], ["2", "50", "$0.90", ""])
        self.assertEqual(rows["re-review"], ["1", "10", "$0.21", ""])
        self.assertEqual(rows["other"], ["1", "5", "$0.08", ""])
        self.assertEqual(rows["fix"], ["1", "5", "$0.05", ""])
        self.assertNotIn("review", rows)

    def test_old_names_map_onto_todays_roles(self):
        # Ticket and tuner reviews, conflict resolvers and security reviews, as gh-35 described them.
        run = schema_3(Path(RUN_ROLES).read_text(),
                       ["Ticket review 01", "Review tuner PR 9", "Resolve conflicts in PR 3", "Security review PR 4", "x"])
        run = run.replace('"Explore"', '"security-reviewer"').replace('"pr-fixer"', '"epic-planner"')
        run = run.replace('"epic-implementer"', '"pr-reviewer"')
        rows = role_rows(report("-", "--by-role", stdin=run))
        self.assertEqual([r for r in rows if r not in ("orchestrator", "Total")],
                         ["ticket-review", "review", "resolve", "security", "plan"])

    def test_a_schema_3_agent_type_wins_over_description_keywords(self):
        # gh-35 named its security reviews "Ticket NN security review": the type says security, not ticket-review.
        run = schema_3(Path(RUN_ROLES).read_text(), ["Implement 01-01", "Implement 01-02", "Ticket 03 security review",
                                                     "Look around", "Fix PR 12"])
        run = run.replace('"pr-reviewer"', '"security-reviewer"')
        rows = role_rows(report("-", "--by-role", stdin=run))
        self.assertEqual(rows["security"][:2], ["1", "10"])
        self.assertNotIn("ticket-review", rows)

    def test_a_role_written_by_the_extractor_is_kept_over_the_fallback(self):
        run = json.loads(Path(RUN_ROLES).read_text())
        run["spans"][0]["subagents"][0]["role"] = "other"
        rows = role_rows(report("-", "--by-role", stdin=json.dumps(run)))
        self.assertEqual(rows["implement"][:2], ["1", "20"])
        self.assertEqual(rows["other"][:2], ["2", "35"])

    def test_cost_is_unknown_when_a_model_has_no_price(self):
        rows = role_rows(report("-", "--by-role", stdin=unpriced(Path(RUN_ROLES).read_text())))
        self.assertEqual(rows["implement"][2], "unknown")
        self.assertEqual(rows["Total"][2], "unknown")

    def test_run_folder_needs_by_role(self):
        proc = subprocess.run([sys.executable, str(HERE / "report.py"), RUN_ROLES, "--run-folder", str(FIXTURES)],
                              capture_output=True, encoding="utf-8")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--run-folder needs --by-role", proc.stderr)


class PriceCheckHint(unittest.TestCase):
    """Fixture pricing.json is dated 2026-01-01."""

    def test_offers_a_check_when_prices_are_over_30_days_old(self):
        out = report(RUN, "--today", "2026-03-01")
        self.assertIn("Prices are 59 days old (2026-01-01); check with: update_pricing.py model-a", out)

    def test_stays_quiet_when_prices_are_recent(self):
        self.assertNotIn("update_pricing.py", report(RUN, "--today", "2026-01-15"))

    def test_offers_a_check_when_prices_have_no_date(self):
        undated = json.loads(Path(PRICING).read_text())
        del undated["asOf"]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "pricing.json"
            path.write_text(json.dumps(undated))
            out = report(RUN, "--today", "2026-01-15", "--pricing", str(path))
        self.assertIn("Prices have no date; check with: update_pricing.py model-a", out)

    def test_offers_a_check_for_a_model_with_no_price(self):
        out = report("-", "--today", "2026-01-15", stdin=unpriced(Path(RUN).read_text()))
        self.assertIn("No price for model-z; check with: update_pricing.py model-z", out)


class TableHeaders(unittest.TestCase):
    def test_given_headers_replace_the_default_ones(self):
        out = table([["renovate-prs", "3"]], headers=["Skill", "Runs"]).splitlines()
        self.assertEqual(out[1], "│ Skill        │ Runs │")
        self.assertEqual(out[3], "│ renovate-prs │ 3    │")
        self.assertNotIn("Span", out[1])

    def test_control_characters_in_any_cell_are_shown_escaped(self):
        out = table([["a\x1b[2J", "\x9b1"]], total=["\x07", "\x7f"], headers=["S\x00", "R"])
        self.assertFalse(any(c in out for c in "\x1b\x9b\x07\x7f\x00"))
        for shown in ("a\\x1b[2J", "\\x9b1", "\\x07", "\\x7f", "S\\x00"):
            self.assertIn(shown, out)


if __name__ == "__main__":
    unittest.main()
