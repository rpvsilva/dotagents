"""Tests for `run.py`: each case run N times against dotagents at a ref, with a fake `claude` per outcome on `PATH`,
and one saved result per run."""
import json
import re
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
RUNNER = HERE / "_runner"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(RUNNER))

import adapters  # noqa: E402
import results  # noqa: E402
import run  # noqa: E402
from testlib import EvalSandbox  # noqa: E402

STREAMS = RUNNER / "adapters" / "fixtures" / "claude"
MINIMAL = RUNNER / "fixtures" / "cases" / "good" / "configure" / "minimal"
MODEL = "claude-sonnet-5-5"
# What `pass.jsonl`'s result event reports, priced by pricing.json.
PASS_TOKENS = {MODEL: {"input": 7, "output": 70, "cacheRead": 700, "cacheWrite5m": 7000, "cacheWrite1h": 0}}


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True,
                          stdin=subprocess.DEVNULL).stdout.strip()


def pricing():
    return json.loads((REPO / "skills" / "telemetry" / "pricing.json").read_text())


def priced(tokens):
    prices = pricing()["models"]
    return round(sum(n * prices[m][k] for m, counts in tokens.items() for k, n in counts.items()) / 1_000_000, 6)


class RunSandbox(EvalSandbox):
    def setUp(self):
        super().setUp()
        self.evals = self.tmp / "evals"
        self.calls = self.tmp / "claude-calls"
        self.argv = self.tmp / "claude-argv"
        self.out = self.tmp / "out.txt"

    def case(self, name="configure/quick", **fields):
        """A copy of the minimal good case under this test's evals folder, with `fields` set in its case.json."""
        dest = self.evals / name
        shutil.copytree(MINIMAL, dest)
        data = json.loads((dest / "case.json").read_text())
        data.update(fields)
        (dest / "case.json").write_text(json.dumps(data))
        return dest

    def claude(self, stream=None, before=""):
        """A fake `claude` that records its argv, runs `before` in its cwd, then prints `stream` (a fixture name)."""
        body = f'echo call >>"{self.calls}"\nprintf "%s\\n" "$*" >>"{self.argv}"\n{before}\n'
        if stream:
            body += f'cat "{STREAMS / stream}"\n'
        self.fake_bin("claude", body)

    def passing_claude(self, stream="pass.jsonl", extra=""):
        self.claude(stream, "mkdir -p .agents && echo '{}' >.agents/settings.json\n" + extra)

    def fake_repo(self, install_sh):
        """A dotagents repo whose one commit holds `install_sh`, `capped` and a `parts.sh` echoing the parts."""
        repo = self.tmp / "dotagents"
        (repo / "scripts").mkdir(parents=True)
        shutil.copy(REPO / "scripts" / "capped", repo / "scripts" / "capped")
        (repo / "scripts" / "parts.sh").write_text('shift; printf "%s\\n" "$@"\n')
        (repo / "install.sh").write_text(install_sh)
        git(repo, "init", "-q")
        git(repo, "add", "-A")
        git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "commit", "-qm", "x")
        return repo

    def call_count(self):
        return len(self.calls.read_text().splitlines()) if self.calls.exists() else 0

    def run_cases(self, cases, **kw):
        kw.setdefault("runs", 1)
        with open(self.out, "a") as out:
            got = run.run_cases("claude", "HEAD", cases, out=out, **kw)
        return got

    def printed(self):
        return self.out.read_text() if self.out.exists() else ""

    def saved(self, got, i=0):
        entry = got["runs"][i]
        return entry["dir"], results.read(entry["dir"])


class RunTest(RunSandbox):
    def test_a_passing_run_saves_every_field_of_its_result(self):
        self.passing_claude()
        got = self.run_cases([self.case()])
        d, res = self.saved(got)
        self.assertEqual(d.relative_to(self.results).parts[:4], ("claude", git(REPO, "rev-parse", "HEAD"), MODEL,
                                                                  "configure"))
        self.assertEqual(d.name, "run-1")
        self.assertTrue(res["passed"])
        self.assertEqual(res["checks"], {"settings-written": True, "no-github": True})
        self.assertEqual((res["tool"], res["model"], res["ref"], res["case"], res["run"]),
                         ("claude", MODEL, "HEAD", "configure/quick", 1))
        self.assertEqual(res["ref_sha"], git(REPO, "rev-parse", "HEAD"))
        self.assertEqual(res["case_hash"], results.case_hash(self.evals / "configure" / "quick"))
        self.assertIsInstance(res["checks_hash"], str)
        self.assertEqual(res["tool_version"], "2.1.319")
        self.assertEqual(res["tokens"], PASS_TOKENS)
        self.assertEqual(res["cost_usd"], priced(PASS_TOKENS))
        self.assertEqual(res["cost_basis"], "pricing")
        self.assertEqual(res["pricing_as_of"], pricing()["asOf"])
        self.assertEqual((res["turns"], res["duration_s"], res["capped"], res["error"]), (2, 12.5, None, None))
        self.assertEqual(res["subagents"], "none")
        self.assertEqual(res["transcript"], "transcript.jsonl")
        self.assertEqual((d / "transcript.jsonl").read_text(), (STREAMS / "pass.jsonl").read_text())
        self.assertTrue((d / "work" / ".agents" / "settings.json").is_file())

    def test_the_tool_runs_in_the_fixture_repo_with_the_installed_skill_in_a_throwaway_home(self):
        seen = self.tmp / "seen"
        self.passing_claude(extra=f'{{ pwd; echo "$HOME"; git log --format=%s; '
                                  f'ls "$HOME/.claude/skills"; }} >"{seen}"\n')
        self.run_cases([self.case()])
        work, home, subject, *skills = seen.read_text().splitlines()
        self.assertEqual(Path(work).name, "work")
        self.assertEqual(subject, "fixture")
        self.assertEqual(Path(home).name, "home")
        self.assertNotEqual(Path(home), self.home)
        self.assertIn("configure", skills)
        self.assertFalse(Path(work).resolve().is_relative_to(REPO))

    def test_the_tool_gets_the_filled_prompt_and_the_case_caps(self):
        case = self.case(caps={"turns": 7, "secs": 30, "cost_usd": 0.5})
        (case / "prompt.md").write_text("Edit {work}/x please\n")
        self.passing_claude()
        self.run_cases([case])
        argv = self.argv.read_text()
        self.assertIn("--max-turns 7", argv)
        self.assertIn("--max-budget-usd 0.5", argv)
        self.assertIn(f"--model {MODEL}", argv)
        self.assertRegex(argv, r"-p Edit /\S+/work/x please")

    def test_a_failing_check_fails_the_run(self):
        self.claude("pass.jsonl")
        _, res = self.saved(self.run_cases([self.case()]))
        self.assertEqual(res["checks"], {"settings-written": False, "no-github": True})
        self.assertFalse(res["passed"])
        self.assertIsNone(res["capped"])

    def test_a_gh_call_fails_the_no_github_check(self):
        self.passing_claude(extra="gh pr list || true\n")
        d, res = self.saved(self.run_cases([self.case()]))
        self.assertEqual(res["checks"], {"settings-written": True, "no-github": False})
        self.assertFalse(res["passed"])
        self.assertEqual((d / "gh-calls").read_text(), "pr list\n")

    def test_max_turns_is_saved_as_a_turns_cap(self):
        self.passing_claude("max-turns.jsonl")
        _, res = self.saved(self.run_cases([self.case()]))
        self.assertEqual(res["capped"], "turns")
        self.assertFalse(res["passed"])
        self.assertIsNone(res["error"])

    def test_the_cost_cap_is_saved_as_a_cost_cap(self):
        self.passing_claude("over-budget.jsonl")
        _, res = self.saved(self.run_cases([self.case()]))
        self.assertEqual(res["capped"], "cost")
        self.assertFalse(res["passed"])

    def test_a_hang_is_killed_at_the_time_cap_and_saved_as_a_time_cap(self):
        case = self.case(caps={"turns": 5, "secs": 1, "cost_usd": 1})
        self.claude(None, f'head -1 "{STREAMS / "pass.jsonl"}"\nexec sleep 30\n')
        got = self.run_cases([case])
        d, res = self.saved(got)
        self.assertEqual(res["capped"], "time")
        self.assertFalse(res["passed"])
        self.assertIsNone(res["error"])
        self.assertLess(res["duration_s"], 20)
        self.assertEqual((d / "transcript.jsonl").read_text(), (STREAMS / "pass.jsonl").read_text().splitlines()[0]
                         + "\n")
        self.assertIn("cap time", self.printed())

    def test_a_timeout_exit_code_alone_is_not_a_time_cap(self):
        self.passing_claude(extra=f'cat "{STREAMS / "pass.jsonl"}"; exit 124\n')
        _, res = self.saved(self.run_cases([self.case()]))
        self.assertIsNone(res["capped"])

    def test_the_adapter_prepares_the_home_before_the_install(self):
        repo = self.fake_repo('[ -d "$HOME/.claude" ] || { echo "no ~/.claude" >&2; exit 1; }\n')
        self.passing_claude()
        with open(self.out, "w") as out:
            got = run.run_cases("claude", "HEAD", [self.case()], runs=1, repo=repo, out=out)
        self.assertIsNone(got["runs"][0]["result"]["error"])
        self.assertEqual(self.call_count(), 1)

    def test_an_install_failure_at_a_bad_ref_is_saved_as_an_install_error(self):
        repo = self.fake_repo("echo broken >&2; exit 1\n")
        self.passing_claude()
        with open(self.out, "w") as out:
            got = run.run_cases("claude", "HEAD", [self.case()], runs=2, repo=repo, out=out)
        self.assertEqual(self.call_count(), 0)
        self.assertEqual(len(got["runs"]), 2)
        for i in (0, 1):
            _, res = self.saved(got, i)
            self.assertEqual(res["error"], "install")
            self.assertFalse(res["passed"])
            self.assertIsNone(res["cost_usd"])
            self.assertIsNone(res["tokens"])
        self.assertIn("error install", self.printed())

    def test_subagents_used_when_the_transcript_starts_one(self):
        self.passing_claude("subagent.jsonl")
        _, res = self.saved(self.run_cases([self.case()]))
        self.assertEqual(res["subagents"], "used")

    def test_subagents_fallback_when_the_skill_lists_agents_and_none_started(self):
        self.passing_claude()
        case = self.case("build-epic/plan", install=["configure"])
        _, res = self.saved(self.run_cases([case]))
        self.assertEqual(res["subagents"], "fallback")

    def test_no_token_reaches_a_kept_file(self):
        self.passing_claude(extra='echo "$CLAUDE_CODE_OAUTH_TOKEN" >leak.txt; echo "token $ANTHROPIC_API_KEY"\n'
                                  'echo "$CLAUDE_CODE_OAUTH_TOKEN" >"$HOME/leak.txt"; echo "$ANTHROPIC_API_KEY" >&2\n')
        d, _ = self.saved(self.run_cases([self.case()]))
        self.assertIn("[redacted]", (d / "work" / "leak.txt").read_text())
        for path in d.rglob("*"):
            if path.is_file():
                for token in self.tokens.values():
                    self.assertNotIn(token.encode(), path.read_bytes(), path)

    def test_prints_one_line_per_run(self):
        self.passing_claude()
        self.run_cases([self.case()], runs=2)
        lines = self.printed().splitlines()
        self.assertEqual(len(lines), 2)
        cost = f"${priced(PASS_TOKENS):.4f}"
        for n, line in enumerate(lines, 1):
            self.assertRegex(line, rf"^configure/quick +run {n} +pass +{re.escape(cost)} +turns 2 +12\.5s +cap -$")


class RunsAndReuseTest(RunSandbox):
    def test_each_case_runs_its_runs_count_unless_runs_is_given(self):
        self.passing_claude()
        a, b = self.case("configure/a", runs=2), self.case("configure/b", runs=1)
        got = self.run_cases([a, b], runs=None)
        self.assertEqual([(r["case"], r["run"]) for r in got["runs"]],
                         [("configure/a", 1), ("configure/a", 2), ("configure/b", 1)])
        self.assertEqual(self.call_count(), 3)
        self.run_cases([a], runs=3)
        self.assertEqual(self.call_count(), 4)

    def test_saved_runs_are_reused_and_fresh_runs_them_again(self):
        self.passing_claude()
        case = self.case()
        first = self.run_cases([case], runs=2)
        self.assertEqual(self.call_count(), 2)
        again = self.run_cases([case], runs=2)
        self.assertEqual(self.call_count(), 2)
        self.assertTrue(all(r["reused"] for r in again["runs"]))
        self.assertEqual([r["result"] for r in again["runs"]], [r["result"] for r in first["runs"]])
        self.assertIn("saved", self.printed().splitlines()[-1])
        self.run_cases([case], runs=2, fresh=True)
        self.assertEqual(self.call_count(), 4)

    def test_a_changed_case_or_model_is_not_reused(self):
        self.passing_claude()
        case = self.case()
        self.run_cases([case])
        (case / "prompt.md").write_text("Something else\n")
        self.run_cases([case])
        self.assertEqual(self.call_count(), 2)
        self.run_cases([case], model="claude-opus-5-5")
        self.assertEqual(self.call_count(), 3)
        self.assertEqual(self.argv.read_text().split("--model ")[-1].split()[0], "claude-opus-5-5")

    def test_no_new_run_starts_once_the_budget_is_reached(self):
        self.passing_claude()
        a, b = self.case("configure/a"), self.case("configure/b")
        got = self.run_cases([a, b], runs=2, budget_usd=priced(PASS_TOKENS) * 1.5)
        self.assertEqual(self.call_count(), 2)
        self.assertEqual([(r["case"], r["run"]) for r in got["runs"]], [("configure/a", 1), ("configure/a", 2)])
        self.assertEqual(got["skipped"], [("configure/b", 1), ("configure/b", 2)])
        self.assertIn("configure/b run 1, configure/b run 2", self.printed())
        for entry in got["runs"]:
            self.assertTrue((entry["dir"] / results.RESULT).is_file())

    def test_a_reused_run_costs_nothing_against_the_budget(self):
        self.passing_claude()
        case = self.case()
        self.run_cases([case])
        got = self.run_cases([case], runs=2, budget_usd=priced(PASS_TOKENS) * 0.5)
        self.assertEqual(self.call_count(), 2)
        self.assertEqual(got["skipped"], [])

    def test_install_failures_cost_nothing_against_the_budget(self):
        repo = self.fake_repo("exit 1\n")
        with open(self.out, "w") as out:
            got = run.run_cases("claude", "HEAD", [self.case()], runs=3, budget_usd=0.5, repo=repo, out=out)
        self.assertEqual((len(got["runs"]), got["skipped"]), (3, []))

    def test_a_missing_price_gives_unknown_cost_counted_as_the_cost_cap(self):
        stream = self.tmp / "unpriced.jsonl"
        stream.write_text((STREAMS / "pass.jsonl").read_text().replace(f'"modelUsage": {{"{MODEL}"',
                                                                       '"modelUsage": {"claude-unpriced-9"'))
        self.passing_claude(stream)
        got = self.run_cases([self.case(caps={"turns": 5, "secs": 60, "cost_usd": 1})], runs=3, budget_usd=1.5)
        _, res = self.saved(got)
        self.assertIsNone(res["cost_usd"])
        self.assertEqual(res["cost_basis"], "unknown")
        self.assertEqual(res["pricing_as_of"], pricing()["asOf"])
        self.assertEqual(list(res["tokens"]), ["claude-unpriced-9"])
        self.assertIn(" unknown ", self.printed().splitlines()[0])
        self.assertEqual(got["skipped"], [("configure/quick", 3)])

    def test_a_tool_not_using_the_case_model_needs_model(self):
        self.passing_claude()
        claude = adapters.load("claude")
        with mock.patch.object(claude, "USES_CASE_MODEL", False):
            with self.assertRaises(run.RunError) as caught:
                self.run_cases([self.case()])
            self.assertIn("--model", str(caught.exception))
            self.assertEqual(self.call_count(), 0)
            self.assertEqual(list(self.results.iterdir()), [])
            got = self.run_cases([self.case("configure/other")], model="claude-opus-5-5")
        self.assertEqual(got["runs"][0]["result"]["model"], "claude-opus-5-5")

    def test_a_model_alias_is_refused(self):
        with self.assertRaises(run.RunError) as caught:
            self.run_cases([self.case()], model="sonnet")
        self.assertIn("alias", str(caught.exception))


class CliTest(RunSandbox):
    def cli(self, *args):
        return subprocess.run([sys.executable, "-I", str(HERE / "run.py"), *args], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, env=os.environ.copy())

    def test_cli_runs_the_named_cases_and_all(self):
        self.passing_claude()
        self.case("configure/a")
        self.case("configure/b")
        out = self.cli("--tool", "claude", "--ref", "HEAD", "--runs", "1", "--evals", str(self.evals), "configure/a")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertRegex(out.stdout, r"^configure/a +run 1 +pass ")
        self.assertEqual(self.call_count(), 1)
        out = self.cli("--tool", "claude", "--ref", "HEAD", "--runs", "1", "--evals", str(self.evals), "--all")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual([line.split()[0] for line in out.stdout.splitlines()], ["configure/a", "configure/b"])
        self.assertEqual(self.call_count(), 2)

    def test_cli_refuses_an_unknown_case_or_tool_before_any_run(self):
        self.passing_claude()
        self.case("configure/a")
        out = self.cli("--tool", "claude", "--ref", "HEAD", "--evals", str(self.evals), "configure/a", "configure/nope")
        self.assertEqual(out.returncode, 2)
        self.assertIn("configure/nope", out.stderr)
        out = self.cli("--tool", "nope", "--ref", "HEAD", "--evals", str(self.evals), "configure/a")
        self.assertEqual(out.returncode, 2)
        self.assertIn("adapters found", out.stderr)
        self.assertEqual(self.call_count(), 0)

    def test_cli_exits_1_and_names_the_runs_the_budget_skipped(self):
        self.passing_claude()
        self.case("configure/a")
        out = self.cli("--tool", "claude", "--ref", "HEAD", "--runs", "2", "--budget-usd", "0.000001",
                       "--evals", str(self.evals), "configure/a")
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("skipped configure/a run 2", out.stdout)


if __name__ == "__main__":
    unittest.main()
