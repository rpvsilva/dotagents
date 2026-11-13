"""Tests for `run.py`: each case run N times against dotagents at a ref, with a fake `claude` per outcome on `PATH`,
and one saved result per run."""
import hashlib
import io
import json
import re
import os
import secrets
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
import judge  # noqa: E402
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
        self.roles = self.tmp / "claude-roles"

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

    def fake_repo(self, install_sh, parts_sh='shift; printf "%s\\n" "$@"\n'):
        """A dotagents repo whose one commit holds `install_sh`, `capped` and `parts_sh` (default: echoing the parts)."""
        repo = self.tmp / "dotagents"
        (repo / "scripts").mkdir(parents=True)
        shutil.copy(REPO / "scripts" / "capped", repo / "scripts" / "capped")
        (repo / "scripts" / "parts.sh").write_text(parts_sh)
        (repo / "install.sh").write_text(install_sh)
        git(repo, "init", "-q")
        git(repo, "add", "-A")
        git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "commit", "-qm", "x")
        return repo

    def judging_claude(self, answer, stream="pass.jsonl", usage=None):
        """A fake `claude` that, given a judge prompt, records `judge` and answers `answer` (a JSON-able value),
        reporting `usage` as its `modelUsage` when given; given any other prompt, records `case` and passes the
        minimal case."""
        judge = self.tmp / "judge-stream.jsonl"
        event = {"type": "result", "subtype": "success", "is_error": False, "result": json.dumps(answer)}
        if usage is not None:
            event["modelUsage"] = usage
        judge.write_text(json.dumps(event) + "\n")
        self.fake_bin("claude", f'''case "$*" in
*"You are grading one run"*) echo judge >>"{self.roles}"; cat "{judge}"; exit 0 ;;
esac
echo case >>"{self.roles}"
mkdir -p .agents && echo '{{}}' >.agents/settings.json
cat "{STREAMS / stream}"
''')

    def rubric_case(self, name="configure/quick", **fields):
        case = self.case(name, **fields)
        (case / "rubric.md").write_text(RUBRIC)
        return case

    def roles_called(self):
        return self.roles.read_text().split() if self.roles.exists() else []

    def call_count(self):
        return len(self.calls.read_text().splitlines()) if self.calls.exists() else 0

    def cli(self, *args):
        return subprocess.run([sys.executable, "-I", str(HERE / "run.py"), *args], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, env=os.environ.copy())

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

    def test_more_turns_than_the_turns_cap_after_the_run_is_saved_as_a_turns_cap(self):
        self.passing_claude()
        _, res = self.saved(self.run_cases([self.case(caps={"turns": 1, "secs": 600, "cost_usd": 1})]))
        self.assertEqual((res["turns"], res["capped"], res["error"]), (2, "turns", None))
        self.assertTrue(all(res["checks"].values()))
        self.assertFalse(res["passed"])

    def test_a_cost_over_the_cost_cap_after_the_run_is_saved_as_a_cost_cap(self):
        cap = priced(PASS_TOKENS) / 2
        self.passing_claude()
        _, res = self.saved(self.run_cases([self.case(caps={"turns": 20, "secs": 600, "cost_usd": cap})]))
        self.assertEqual((res["cost_usd"], res["capped"], res["error"]), (priced(PASS_TOKENS), "cost", None))
        self.assertFalse(res["passed"])
        self.assertIn("cap cost", self.printed())

    def test_a_run_at_its_turns_and_cost_caps_is_not_capped(self):
        self.passing_claude()
        _, res = self.saved(self.run_cases([self.case(caps={"turns": 2, "secs": 600,
                                                             "cost_usd": priced(PASS_TOKENS)})]))
        self.assertIsNone(res["capped"])
        self.assertTrue(res["passed"])

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

    def config_token(self, mode=0o600):
        """A remote.env in the sandbox's config folder holding a Claude token; the env's auth vars unset."""
        token = "config-secret-" + secrets.token_hex(8)
        conf = self.home / ".config" / "dotagents"
        conf.mkdir(parents=True, exist_ok=True)
        (conf / "remote.env").write_text(f"export CLAUDE_CODE_OAUTH_TOKEN='{token}'\nOTHER=1\n")
        (conf / "remote.env").chmod(mode)
        os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
        os.environ.pop("ANTHROPIC_API_KEY", None)
        return token

    def test_a_local_run_with_no_auth_var_takes_the_token_from_remote_env(self):
        token = self.config_token()
        seen = self.tmp / "seen"
        self.passing_claude(extra=f'echo "$CLAUDE_CODE_OAUTH_TOKEN" >"{seen}"; echo "$CLAUDE_CODE_OAUTH_TOKEN" >leak.txt\n')
        d, res = self.saved(self.run_cases([self.case()]))
        self.assertTrue(res["passed"])
        self.assertEqual(seen.read_text().strip(), token)
        self.assertIn("auth: CLAUDE_CODE_OAUTH_TOKEN from ", self.printed())
        self.assertNotIn(token, self.printed())
        for path in d.rglob("*"):
            if path.is_file():
                self.assertNotIn(token.encode(), path.read_bytes(), path)

    def test_an_exported_auth_var_wins_over_remote_env(self):
        token = self.config_token()
        os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = self.tokens["CLAUDE_CODE_OAUTH_TOKEN"]
        seen = self.tmp / "seen"
        self.passing_claude(extra=f'echo "$CLAUDE_CODE_OAUTH_TOKEN" >"{seen}"\n')
        self.run_cases([self.case()])
        self.assertEqual(seen.read_text().strip(), self.tokens["CLAUDE_CODE_OAUTH_TOKEN"])
        self.assertNotIn(token, seen.read_text())
        self.assertNotIn("auth:", self.printed())

    def test_a_remote_env_others_may_read_is_not_read(self):
        token = self.config_token(mode=0o644)
        seen = self.tmp / "seen"
        self.passing_claude(extra=f'echo "x$CLAUDE_CODE_OAUTH_TOKEN" >"{seen}"\n')
        self.run_cases([self.case()])
        self.assertEqual(seen.read_text().strip(), "x")
        self.assertIn("is not private to you (chmod 600 it); not read", self.printed())
        self.assertNotIn(token, self.printed())

    def test_prints_one_line_per_run(self):
        self.passing_claude()
        self.run_cases([self.case()], runs=2)
        lines = self.printed().splitlines()
        self.assertEqual(len(lines), 2)
        cost = f"${priced(PASS_TOKENS):.4f}"
        for n, line in enumerate(lines, 1):
            self.assertRegex(line, rf"^configure/quick +run {n} +pass +{re.escape(cost)} +turns 2 +12\.5s +cap -$")


JUDGE = "claude-haiku-5-5"
JUDGE_TOKENS = {JUDGE: {"input": 200_000, "output": 20_000, "cacheRead": 0, "cacheWrite5m": 0, "cacheWrite1h": 0}}
JUDGE_USAGE = {JUDGE: {"inputTokens": 200_000, "outputTokens": 20_000, "cacheReadInputTokens": 0,
                       "cacheCreationInputTokens": 0}}
RUBRIC = "# Rubric\n\n- correctness: the setting holds the asked value.\n"
SCORES = {"correctness": {"score": 4, "reason": "It is set."}}


class JudgeTest(RunSandbox):
    def test_a_case_with_a_rubric_is_judged_after_its_checks(self):
        self.judging_claude(SCORES)
        _, res = self.saved(self.run_cases([self.rubric_case(judge_model="claude-opus-5-5")]))
        self.assertEqual(self.roles_called(), ["case", "judge"])
        self.assertEqual(res["rubric"], {"scores": SCORES, "judge_model": "claude-opus-5-5",
                                         "rubric_hash": hashlib.sha256(RUBRIC.encode()).hexdigest(),
                                         "cost_usd": None})
        self.assertTrue(res["passed"])
        self.assertEqual(res["checks"], {"settings-written": True, "no-github": True})

    def test_a_bad_judge_answer_is_saved_as_a_rubric_error_and_leaves_passed_alone(self):
        self.judging_claude({"correctness": {"score": 9, "reason": "too high"}})
        _, res = self.saved(self.run_cases([self.rubric_case()]))
        self.assertNotIn("scores", res["rubric"])
        self.assertIn("correctness", res["rubric"]["error"])
        self.assertEqual(res["rubric"]["judge_model"], "claude-haiku-5-5")
        self.assertTrue(res["passed"])

    def test_a_case_without_a_rubric_is_not_judged(self):
        self.judging_claude(SCORES)
        _, res = self.saved(self.run_cases([self.case()]))
        self.assertEqual(self.roles_called(), ["case"])
        self.assertIsNone(res["rubric"])

    def test_an_install_error_is_not_judged(self):
        repo = self.fake_repo("exit 1\n")
        self.judging_claude(SCORES)
        with open(self.out, "w") as out:
            got = run.run_cases("claude", "HEAD", [self.rubric_case()], runs=1, repo=repo, out=out)
        self.assertEqual(self.roles_called(), [])
        self.assertIsNone(self.saved(got)[1]["rubric"])

    def test_a_reused_run_is_not_judged_again(self):
        self.judging_claude(SCORES)
        case = self.rubric_case()
        first = self.run_cases([case])
        again = self.run_cases([case])
        self.assertEqual(self.roles_called(), ["case", "judge"])
        self.assertEqual(again["runs"][0]["result"]["rubric"], first["runs"][0]["result"]["rubric"])

    def test_the_judges_cost_is_saved_with_its_rubric(self):
        self.judging_claude(SCORES, usage=JUDGE_USAGE)
        _, res = self.saved(self.run_cases([self.rubric_case()]))
        self.assertEqual(res["rubric"]["cost_usd"], priced(JUDGE_TOKENS))
        self.assertEqual(res["cost_usd"], priced(PASS_TOKENS))

    def test_a_judge_that_tips_the_budget_stops_the_next_run(self):
        # The case alone stays under the budget; with its judge's cost it reaches it.
        self.judging_claude(SCORES, usage=JUDGE_USAGE)
        budget = priced(PASS_TOKENS) + priced(JUDGE_TOKENS) * 0.5
        got = self.run_cases([self.rubric_case()], runs=2, budget_usd=budget)
        self.assertEqual(self.roles_called(), ["case", "judge"])
        self.assertEqual(got["skipped"], [("configure/quick", 2)])
        self.assertAlmostEqual(got["spent_usd"], priced(PASS_TOKENS) + priced(JUDGE_TOKENS))

    def test_a_judge_of_unknown_cost_counts_as_the_judges_cost_cap(self):
        self.judging_claude(SCORES)
        got = self.run_cases([self.rubric_case()], runs=1)
        self.assertAlmostEqual(got["spent_usd"], priced(PASS_TOKENS) + judge.CAPS["cost_usd"])


class ReuseRegradeTest(RunSandbox):
    """A saved run whose checks or rubric changed since is re-graded on reuse, as `grade.py` does: the case's tool is
    never called again."""

    def test_an_edited_check_regrades_the_reused_run_with_no_tool_call(self):
        self.judging_claude(SCORES)
        case = self.rubric_case()
        first = self.run_cases([case])
        (case / "checks" / "settings-written.sh").write_text("#!/usr/bin/env bash\nexit 1\n")
        again = self.run_cases([case])
        self.assertEqual(self.roles_called(), ["case", "judge"])
        entry = again["runs"][0]
        self.assertTrue(entry["reused"])
        self.assertEqual(entry["dir"], first["runs"][0]["dir"])
        self.assertEqual(entry["result"]["checks"], {"settings-written": False, "no-github": True})
        self.assertFalse(entry["result"]["passed"])
        self.assertNotEqual(entry["result"]["checks_hash"], first["runs"][0]["result"]["checks_hash"])
        self.assertEqual(entry["result"]["rubric"], first["runs"][0]["result"]["rubric"])
        self.assertEqual(results.read(entry["dir"]), entry["result"])
        self.assertEqual(again["spent_usd"], 0)

    def test_an_edited_rubric_rejudges_the_reused_run_calling_only_the_judge(self):
        self.judging_claude(SCORES)
        case = self.rubric_case()
        first = self.run_cases([case])
        new = RUBRIC + "- clarity: the answer says what changed.\n"
        (case / "rubric.md").write_text(new)
        scores = {**SCORES, "clarity": {"score": 2, "reason": "Terse."}}
        self.judging_claude(scores)
        again = self.run_cases([case])
        self.assertEqual(self.roles_called(), ["case", "judge", "judge"])
        entry = again["runs"][0]
        self.assertTrue(entry["reused"])
        self.assertEqual(entry["dir"], first["runs"][0]["dir"])
        self.assertEqual(entry["result"]["rubric"]["scores"], scores)
        self.assertEqual(entry["result"]["rubric"]["rubric_hash"], hashlib.sha256(new.encode()).hexdigest())
        self.assertAlmostEqual(again["spent_usd"], judge.CAPS["cost_usd"])

    def test_a_changed_judge_model_rejudges_the_reused_run_calling_only_the_judge(self):
        self.judging_claude(SCORES)
        case = self.rubric_case()
        self.run_cases([case])
        data = json.loads((case / "case.json").read_text())
        (case / "case.json").write_text(json.dumps({**data, "judge_model": "claude-opus-5-5"}))
        again = self.run_cases([case])
        self.assertEqual(self.roles_called(), ["case", "judge", "judge"])
        self.assertTrue(again["runs"][0]["reused"])
        self.assertEqual(again["runs"][0]["result"]["rubric"]["judge_model"], "claude-opus-5-5")

    def test_a_removed_rubric_drops_the_reused_runs_rubric_with_no_tool_call(self):
        self.judging_claude(SCORES)
        case = self.rubric_case()
        first = self.run_cases([case])
        self.assertIn("scores", first["runs"][0]["result"]["rubric"])
        (case / "rubric.md").unlink()
        again = self.run_cases([case])
        self.assertEqual(self.roles_called(), ["case", "judge"])
        entry = again["runs"][0]
        self.assertTrue(entry["reused"])
        self.assertIsNone(entry["result"]["rubric"])
        self.assertIsNone(results.read(entry["dir"])["rubric"])

    def test_a_rejudge_on_reuse_counts_against_the_budget(self):
        self.judging_claude(SCORES)
        case = self.rubric_case()
        self.run_cases([case], runs=2)
        (case / "rubric.md").write_text(RUBRIC + "- clarity: the answer says what changed.\n")
        self.judging_claude({**SCORES, "clarity": {"score": 2, "reason": "Terse."}})
        got = self.run_cases([case], runs=2, budget_usd=judge.CAPS["cost_usd"] / 2)
        self.assertEqual(self.roles_called(), ["case", "judge", "case", "judge", "judge"])
        self.assertEqual(got["skipped"], [("configure/quick", 2)])
        self.assertIn("budget", self.printed().splitlines()[-1])


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

    def test_a_saved_run_with_an_error_is_made_again(self):
        self.claude()
        case = self.case()
        first = self.run_cases([case])
        self.assertEqual(first["runs"][0]["result"]["error"], "tool")
        self.passing_claude()
        again = self.run_cases([case])
        self.assertEqual(self.call_count(), 2)
        self.assertFalse(again["runs"][0]["reused"])
        self.assertIsNone(self.saved(again)[1]["error"])
        self.assertNotIn("saved", self.printed().splitlines()[-1])

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

    def zero_stream(self):
        """`pass.jsonl` with the result event reporting a cost of 0 and every usage count 0."""
        events = [json.loads(l) for l in (STREAMS / "pass.jsonl").read_text().splitlines()]
        for e in events:
            if e.get("type") == "result":
                e["total_cost_usd"] = 0
                e["usage"] = {k: 0 for k in e.get("usage", {"input_tokens": 0})}
                e["modelUsage"] = {}
        stream = self.tmp / "zero.jsonl"
        stream.write_text("".join(json.dumps(e) + "\n" for e in events))
        return stream

    def test_a_reported_zero_cost_is_saved_as_zero_and_spends_nothing(self):
        self.passing_claude(self.zero_stream())
        got = self.run_cases([self.case(caps={"turns": 5, "secs": 60, "cost_usd": 1})], runs=2, budget_usd=0.5)
        _, res = self.saved(got)
        self.assertEqual((res["cost_usd"], res["cost_basis"]), (0.0, "reported"))
        self.assertEqual((self.call_count(), got["skipped"], got["spent_usd"]), (2, [], 0.0))
        self.assertIn(" $0.0000 ", self.printed().splitlines()[0])

    def auth_claude(self):
        self.claude("auth-failed.jsonl")

    def test_an_auth_failure_is_saved_unjudged_and_stops_the_command(self):
        self.auth_claude()
        a, b = self.rubric_case("configure/a"), self.case("configure/b")
        got = self.run_cases([a, b], runs=2)
        self.assertEqual(self.call_count(), 1)
        self.assertEqual(got["stopped"], "auth")
        self.assertEqual(got["skipped"], [])
        _, res = self.saved(got)
        self.assertEqual((res["error"], res["passed"], res["cost_usd"]), ("auth", False, 0.0))
        self.assertIsNone(res["rubric"])
        lines = self.printed().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("error auth", lines[0])
        self.assertEqual(lines[1], "auth failed for claude: renew CLAUDE_CODE_OAUTH_TOKEN, ANTHROPIC_API_KEY ("
                                   f"{adapters.load('claude').RENEW}); stopped before 3 more run(s)")
        for path in got["runs"][0]["dir"].rglob("*"):
            if path.is_file():
                for token in self.tokens.values():
                    self.assertNotIn(token.encode(), path.read_bytes(), path)
        for token in self.tokens.values():
            self.assertNotIn(token, self.printed())

    def test_an_auth_failure_skips_the_judge(self):
        self.judging_claude({"verdict": "pass"}, stream="auth-failed.jsonl")
        got = self.run_cases([self.rubric_case()])
        self.assertEqual(self.roles_called(), ["case"])
        self.assertEqual(got["stopped"], "auth")

    def test_the_next_invocation_retries_a_saved_auth_run(self):
        self.auth_claude()
        case = self.case()
        self.run_cases([case])
        got = self.run_cases([case])
        self.assertEqual(self.call_count(), 2)
        self.assertFalse(got["runs"][0]["reused"])

    def test_a_run_without_an_auth_failure_is_not_stopped(self):
        self.passing_claude()
        self.assertIsNone(self.run_cases([self.case()], runs=2)["stopped"])

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

    def test_a_prompt_the_tool_would_read_as_an_option_is_refused_before_any_run(self):
        self.passing_claude()
        good, bad = self.case("configure/a"), self.case("configure/b")
        (bad / "prompt.md").write_text("--help me\n")
        with self.assertRaises(run.RunError) as caught:
            self.run_cases([good, bad])
        self.assertIn("configure/b", str(caught.exception))
        self.assertEqual(self.call_count(), 0)
        self.assertEqual(list(self.results.iterdir()), [])

    def test_parts_whose_closure_pulls_in_global_are_refused_before_any_run(self):
        repo = self.fake_repo("exit 0\n", parts_sh='echo configure; echo global\n')
        self.passing_claude()
        with open(self.out, "w") as out, self.assertRaises(run.RunError) as caught:
            run.run_cases("claude", "HEAD", [self.case()], runs=1, repo=repo, out=out)
        self.assertIn("global", str(caught.exception))
        self.assertEqual(self.call_count(), 0)
        self.assertEqual(list(self.results.iterdir()), [])

    def test_fresh_keeps_the_saved_run_when_the_new_one_cannot_start(self):
        self.passing_claude()
        case = self.case()
        d, saved = self.saved(self.run_cases([case]))
        with mock.patch.object(run.home, "fill_prompt", lambda text, paths: "-" + text):
            with self.assertRaises(run.RunError):
                self.run_cases([case], fresh=True)
        with mock.patch.object(adapters.load("claude"), "parse", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.run_cases([case], fresh=True)
        self.assertEqual(results.read(d), saved)
        self.assertTrue((d / "transcript.jsonl").is_file())

    def test_a_failed_closure_is_saved_as_an_install_error_not_refused(self):
        repo = self.fake_repo("exit 0\n", parts_sh="echo closure broken >&2; exit 2\n")
        self.passing_claude()
        with open(self.out, "w") as out:
            got = run.run_cases("claude", "HEAD", [self.case()], runs=1, repo=repo, out=out)
        d, res = self.saved(got)
        self.assertEqual(res["error"], "install")
        self.assertIn("closure broken", (d / "stderr.log").read_text())
        self.assertEqual(self.call_count(), 0)

    def test_find_cases_refuses_a_tooling_folder_a_dot_folder_and_a_name_not_skill_slash_case(self):
        self.case("configure/a")
        shutil.copytree(MINIMAL, self.evals / "_runner" / "x")
        shutil.copytree(MINIMAL, self.evals / ".hidden" / "x")
        self.assertEqual(run.find_cases(self.evals, ["configure/a"]), [self.evals / "configure" / "a"])
        for name in ("_runner/x", ".hidden/x", "configure", "configure/a/checks", "configure/.."):
            with self.subTest(name=name), self.assertRaises(run.RunError) as caught:
                run.find_cases(self.evals, [name])
            self.assertIn(name, str(caught.exception))

    def test_find_cases_all_skips_tooling_and_dot_folders(self):
        self.case("configure/a")
        shutil.copytree(MINIMAL, self.evals / "_runner" / "x")
        shutil.copytree(MINIMAL, self.evals / ".hidden" / "x")
        (self.evals / "configure" / "notes").mkdir()
        self.assertEqual(run.find_cases(self.evals, [], every=True), [self.evals / "configure" / "a"])

    def test_a_model_alias_is_refused(self):
        with self.assertRaises(run.RunError) as caught:
            self.run_cases([self.case()], model="sonnet")
        self.assertIn("alias", str(caught.exception))


class RemoteTest(RunSandbox):
    """`--remote`: the case runs through a stub `remote-agent` (`EVALS_REMOTE_AGENT`) that answers `ready` with
    `docker`, logs its argv, env and stdin, writes a change into the worktree it is given and a transcript where the
    real one keeps it."""

    def setUp(self):
        super().setUp()
        self.ra_calls = self.tmp / "ra-calls"
        self.ra_argv = self.tmp / "ra-argv"
        self.ra_env = self.tmp / "ra-env"
        self.ra_stdin = self.tmp / "ra-stdin"
        self.ra_cwd = self.tmp / "ra-cwd"

    def remote_agent(self, work_script="mkdir -p .agents && echo '{}' >.agents/settings.json", ready=None,
                     status=0, stream="pass.jsonl"):
        """Install the stub: `work_script` runs in the worktree it is given, before the transcript is written."""
        ready = ready or "echo docker; exit 0"
        body = f'''if [ "$1" = ready ]; then pwd >"{self.ra_cwd}"; {ready}; fi
echo call >>"{self.ra_calls}"
printf "%s\\n" "$*" >"{self.ra_argv}"
env >"{self.ra_env}"
cat >"{self.ra_stdin}"
for a; do w="$a"; done
cd "$w" || exit 1
{work_script}
mkdir -p .git/remote-agent/run-a
cat "{STREAMS / stream}" >.git/remote-agent/run-a/transcript.jsonl
exit {status}
'''
        path = self.fake_bin("remote-agent", body)
        os.environ["EVALS_REMOTE_AGENT"] = str(path)
        return path

    def calls_of_remote_agent(self):
        return self.ra_calls.read_text().split() if self.ra_calls.exists() else []

    def remote(self, cases, **kw):
        return self.run_cases(cases, remote=True, **kw)

    def test_a_remote_run_passes_on_the_returned_change_and_saves_where_it_ran(self):
        self.remote_agent()
        self.claude()  # a local claude is never called
        got = self.remote([self.case()])
        d, res = self.saved(got)
        self.assertTrue(res["passed"], res)
        self.assertEqual(res["checks"], {"settings-written": True, "no-github": True})
        self.assertEqual((res["remote"], res["backend"], res["unenforced"]), (True, "docker", ["turns", "cost_usd"]))
        self.assertEqual((res["tool"], res["model"], res["turns"], res["tool_version"], res["error"]),
                         ("claude", MODEL, 2, "2.1.319", None))
        self.assertEqual(res["tokens"], PASS_TOKENS)
        self.assertEqual(res["cost_usd"], priced(PASS_TOKENS))
        self.assertEqual(d.relative_to(self.results).parts[0], "claude@docker")
        self.assertEqual((d / "transcript.jsonl").read_text(), (STREAMS / "pass.jsonl").read_text())
        self.assertTrue((d / "work" / ".agents" / "settings.json").is_file())
        self.assertEqual(self.call_count(), 0)
        self.assertEqual(self.calls_of_remote_agent(), ["call"])

    def test_the_run_line_ends_with_remote_and_the_backend_only_for_a_remote_run(self):
        self.remote_agent()
        self.remote([self.case()])
        self.assertRegex(self.printed().strip(), r"  cap -  remote docker$")
        self.out.unlink()
        self.passing_claude()
        self.run_cases([self.case("configure/other")])
        self.assertNotIn("remote", self.printed())

    def test_remote_agent_gets_home_model_name_the_prompt_on_stdin_and_the_real_env(self):
        self.remote_agent()
        case = self.case()
        (case / "prompt.md").write_text("Work in {work}, scratch {scratch}, input {input}.\n")
        got = self.remote([case])
        d, _ = self.saved(got)
        argv = self.ra_argv.read_text().split()
        self.assertEqual(argv[:2], ["--home", argv[1]])
        self.assertTrue(Path(argv[1]).name == "home" and argv[1] != str(self.home))
        self.assertEqual(argv[argv.index("--model") + 1], MODEL)
        self.assertEqual(argv[argv.index("--name") + 1], "quick-run-1")
        self.assertNotIn("--attach", argv)
        self.assertEqual(argv[-2], "-")
        self.assertEqual(Path(argv[-1]).name, "work")
        self.assertEqual(self.ra_stdin.read_text(),
                         "Work in ~/work, scratch ~/work/.eval-scratch, input ~/attached/input.\n")
        env = dict(line.split("=", 1) for line in self.ra_env.read_text().splitlines() if "=" in line)
        self.assertEqual((env["HOME"], env["XDG_CONFIG_HOME"]), (str(self.home), str(self.home / ".config")))
        self.assertNotIn(self.ra_cwd.read_text().strip(), (str(REPO), ""))

    def test_a_case_with_input_attaches_it(self):
        self.remote_agent()
        case = self.case()
        (case / "input").mkdir()
        (case / "input" / "ticket.md").write_text("t\n")
        self.remote([case])
        argv = self.ra_argv.read_text().split()
        self.assertEqual(Path(argv[argv.index("--attach") + 1]).name, "input")

    def test_ready_runs_once_from_a_folder_outside_any_repo_before_any_run(self):
        self.remote_agent()
        self.remote([self.case("configure/a"), self.case("configure/b")])
        self.assertEqual(self.calls_of_remote_agent(), ["call", "call"])
        cwd = Path(self.ra_cwd.read_text().strip())
        self.assertFalse(cwd.exists())  # a temp folder, gone after
        self.assertNotIn(str(REPO), str(cwd))

    def test_a_ready_failure_is_a_usage_error_quoting_its_line_and_runs_nothing(self):
        self.remote_agent(ready='echo "cluster backend not reachable: refused"; echo more; exit 1')
        case = self.case()
        with self.assertRaises(run.RunError) as err:
            self.remote([case])
        self.assertIn("cluster backend not reachable: refused", str(err.exception))
        self.assertEqual(self.calls_of_remote_agent(), [])
        out = self.cli("--tool", "claude", "--ref", "HEAD", "--remote", "--results", str(self.tmp / "s"),
                       "--evals", str(self.evals), "configure/quick")
        self.assertEqual(out.returncode, 2)
        self.assertIn("cluster backend not reachable: refused", out.stderr)

    def test_a_local_run_never_calls_remote_agent_and_saves_no_remote_fields(self):
        self.remote_agent()
        self.passing_claude()
        got = self.run_cases([self.case()])
        d, res = self.saved(got)
        self.assertEqual(self.calls_of_remote_agent(), [])
        self.assertFalse(self.ra_cwd.exists())
        self.assertEqual((res["remote"], res["backend"], res["unenforced"]), (False, None, None))
        self.assertEqual(d.relative_to(self.results).parts[0], "claude")

    def test_a_local_saved_run_is_not_reused_for_a_remote_one_nor_the_reverse(self):
        self.remote_agent()
        self.passing_claude()
        case = self.case()
        self.run_cases([case])
        self.assertEqual(self.call_count(), 1)
        got = self.remote([case])
        self.assertFalse(got["runs"][0]["reused"])
        self.assertEqual(self.calls_of_remote_agent(), ["call"])
        got = self.remote([case])
        self.assertTrue(got["runs"][0]["reused"])
        self.assertEqual(self.calls_of_remote_agent(), ["call"])
        # the reverse: a remote run saved first, then a local one
        other = self.case("configure/other")
        self.remote([other])
        self.assertEqual(self.calls_of_remote_agent(), ["call", "call"])
        got = self.run_cases([other])
        self.assertFalse(got["runs"][0]["reused"])
        self.assertEqual(self.call_count(), 2)

    def test_exit_3_is_a_normal_run(self):
        self.remote_agent(work_script="true", status=3)
        _, res = self.saved(self.remote([self.case()]))
        self.assertIsNone(res["error"])
        self.assertFalse(res["checks"]["settings-written"])
        self.assertFalse(res["passed"])

    def test_exit_4_saves_a_patch_error_and_is_made_again(self):
        self.remote_agent(status=4)
        case = self.case()
        _, res = self.saved(self.remote([case]))
        self.assertEqual(res["error"], "patch")
        self.assertFalse(res["passed"])
        self.remote_agent()
        got = self.remote([case])
        self.assertFalse(got["runs"][0]["reused"])
        self.assertTrue(got["runs"][0]["result"]["passed"])

    def scratch_case(self, name="configure/scratch"):
        case = self.case(name)
        (case / "prompt.md").write_text("Write {scratch}/areas.md in {work}\n")
        (case / "checks" / "settings-written.sh").unlink()
        (case / "checks" / "scratch-read.sh").write_text('''#!/usr/bin/env bash
[ "$(cat "$EVAL_SCRATCH/areas.md")" = areas ]
''')
        (case / "checks" / "work-unchanged.sh").write_text('''#!/usr/bin/env bash
set -euo pipefail
status="$(git -C "$EVAL_WORK" status --porcelain --untracked-files=all)"
[ -z "$status" ]
''')
        for f in (case / "checks").iterdir():
            f.chmod(0o755)
        return case

    def test_scratch_written_in_work_moves_to_the_runs_scratch_before_the_checks(self):
        self.remote_agent(work_script="mkdir -p .eval-scratch && echo areas >.eval-scratch/areas.md")
        got = self.remote([self.scratch_case()])
        d, res = self.saved(got)
        self.assertEqual(res["checks"], {"scratch-read": True, "work-unchanged": True, "no-github": True})
        self.assertTrue(res["passed"])
        self.assertEqual((d / "scratch" / "areas.md").read_text(), "areas\n")
        self.assertFalse((d / "work" / ".eval-scratch").exists())
        self.assertEqual(git(d / "work", "status", "--porcelain", "--untracked-files=all"), "")
        self.assertEqual(git(d / "work", "log", "--format=%s"), "fixture")
        self.assertFalse((d / "work" / ".git" / "remote-agent").exists())

    def test_grade_regrades_a_remote_scratch_run_to_the_same_checks(self):
        self.remote_agent(work_script="mkdir -p .eval-scratch && echo areas >.eval-scratch/areas.md")
        case = self.scratch_case()
        d, _ = self.saved(self.remote([case]))
        (case / "checks" / "extra.sh").write_text("#!/usr/bin/env bash\n[ -f \"$EVAL_SCRATCH/areas.md\" ]\n")
        (case / "checks" / "extra.sh").chmod(0o755)
        out = subprocess.run([sys.executable, "-I", str(HERE / "grade.py"), "--evals", str(self.evals), "--all"],
                             capture_output=True, text=True, stdin=subprocess.DEVNULL, env=os.environ.copy())
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        res = results.read(d)
        self.assertEqual(res["checks"], {"extra": True, "scratch-read": True, "work-unchanged": True,
                                         "no-github": True})
        self.assertTrue(res["passed"])
        self.assertEqual((res["remote"], res["backend"]), (True, "docker"))

    def test_a_fixture_holding_eval_scratch_is_refused_before_the_stub_is_called(self):
        self.remote_agent()
        case = self.case()
        (case / "fixture" / ".eval-scratch").mkdir()
        (case / "fixture" / ".eval-scratch" / "x").write_text("x")
        with self.assertRaises(run.RunError) as err:
            self.remote([case])
        self.assertIn(".eval-scratch", str(err.exception))
        self.assertIn("configure/quick", str(err.exception))
        self.assertFalse(self.ra_cwd.exists())
        self.assertEqual(self.calls_of_remote_agent(), [])
        out = self.cli("--tool", "claude", "--ref", "HEAD", "--remote", "--evals", str(self.evals), "configure/quick")
        self.assertEqual(out.returncode, 2)
        self.assertIn(".eval-scratch", out.stderr)
        self.assertFalse(self.ra_cwd.exists())

    def test_remote_runs_claude_only(self):
        self.remote_agent()
        with self.assertRaises(run.RunError) as err:
            run.run_cases("codex", "HEAD", [self.case()], remote=True, model=MODEL, out=io.StringIO())
        self.assertEqual(str(err.exception),
                         "--remote needs --tool claude: the remote sandbox runs Claude Code only")
        self.assertFalse(self.ra_cwd.exists())
        out = self.cli("--tool", "codex", "--ref", "HEAD", "--remote", "--model", MODEL, "--evals", str(self.evals),
                       "configure/quick")
        self.assertEqual(out.returncode, 2)
        self.assertIn("the remote sandbox runs Claude Code only", out.stderr)
        self.assertEqual(self.calls_of_remote_agent(), [])

    def test_remote_needs_names_the_configure_cases_and_none_of_the_build_epic_ones(self):
        import cases as case_loader
        evals = REPO / "evals"
        named = {}
        for d in sorted(evals.glob("*/*")):
            if (d / "case.json").is_file():
                named[f"{d.parent.name}/{d.name}"] = run.remote_needs(case_loader.load(d))
        configure = {k for k, v in named.items() if k.startswith("configure/")}
        self.assertEqual(len(configure), 4)
        for k in configure:
            self.assertIn("EVAL_HOME", named[k], k)
        build = {k: v for k, v in named.items() if k.startswith("build-epic/")}
        self.assertEqual(len(build), 8)
        self.assertEqual(set(build.values()), {None})

    def test_a_home_case_is_named_and_skipped_while_a_scratch_case_runs(self):
        self.remote_agent(work_script="mkdir -p .eval-scratch && echo areas >.eval-scratch/areas.md")
        scratch = self.scratch_case("configure/scratch")
        home_case = self.case("configure/home")
        (home_case / "prompt.md").write_text("Look in {home}\n")
        got = self.remote([home_case, scratch])
        self.assertEqual([r["case"] for r in got["runs"]], ["configure/scratch"])
        self.assertIn("configure/home: cannot run remotely ({home} in its prompt)", self.printed())
        self.assertEqual(self.calls_of_remote_agent(), ["call"])
        self.assertEqual(len(list(self.results.rglob(results.RESULT))), 1)

    def test_a_check_naming_eval_home_is_skipped(self):
        self.remote_agent()
        case = self.case("configure/homecheck")
        (case / "checks" / "h.sh").write_text('#!/usr/bin/env bash\ntest -d "$EVAL_HOME"\n')
        (case / "checks" / "h.sh").chmod(0o755)
        self.remote([case, self.case("configure/ok")])
        self.assertIn("configure/homecheck: cannot run remotely (EVAL_HOME in checks/h.sh)", self.printed())

    def test_only_home_cases_exit_2_before_any_call_and_save_nothing(self):
        self.remote_agent()
        case = self.case("configure/home")
        (case / "prompt.md").write_text("Look in {home}\n")
        out = self.cli("--tool", "claude", "--ref", "HEAD", "--remote", "--results", str(self.tmp / "s"),
                       "--evals", str(self.evals), "configure/home")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("configure/home: cannot run remotely", out.stdout)
        self.assertFalse(self.ra_cwd.exists())
        self.assertEqual(self.calls_of_remote_agent(), [])
        self.assertFalse((self.tmp / "s").exists() and list((self.tmp / "s").rglob(results.RESULT)))

    def test_a_remote_auth_failure_names_remote_agent_setup_and_exits_3(self):
        token = "remote-secret-" + "q" * 12
        conf = self.home / ".config" / "dotagents"
        conf.mkdir(parents=True)
        (conf / "remote.env").write_text(f"CLAUDE_CODE_OAUTH_TOKEN={token}\n")
        self.remote_agent(stream="auth-failed.jsonl")
        self.case("configure/a")
        out = self.cli("--tool", "claude", "--ref", "HEAD", "--runs", "2", "--remote", "--evals", str(self.evals),
                       "configure/a")
        self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
        self.assertIn("auth failed for claude: run `remote-agent setup` (the token in remote.env); "
                      "stopped before 1 more run(s)", out.stdout)
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", out.stdout.split("auth failed", 1)[1])
        self.assertEqual(self.calls_of_remote_agent(), ["call"])
        self.assertNotIn(token, out.stdout + out.stderr)

    def test_a_token_in_returned_files_and_records_reaches_no_file_of_the_store(self):
        token = "remote-secret-" + "y" * 12
        conf = self.home / ".config" / "dotagents"
        conf.mkdir(parents=True)
        (conf / "remote.env").write_text(f"CLAUDE_CODE_OAUTH_TOKEN={token}\n")
        os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
        self.remote_agent(work_script=f"echo {token} >leak.txt; mkdir -p .eval-scratch; echo {token} "
                          f">.eval-scratch/s.md; mkdir -p .git/remote-agent/run-a; "
                          f"echo {token} >.git/remote-agent/run-a/changes.patch; "
                          f"echo {token} >.git/remote-agent/run-a/stderr.log; echo {token} >&2")
        d, _ = self.saved(self.remote([self.case()]))
        files = [p for p in self.results.rglob("*") if p.is_file()]
        self.assertTrue(files)
        for p in files:
            self.assertNotIn(token.encode(), p.read_bytes(), p)
        self.assertEqual((d / "scratch" / "s.md").read_text(), "[redacted]\n")

    def test_the_remote_token_reaches_no_kept_file(self):
        token = "remote-secret-" + "z" * 12
        conf = self.home / ".config" / "dotagents"
        conf.mkdir(parents=True)
        (conf / "remote.env").write_text(f"CLAUDE_CODE_OAUTH_TOKEN={token}\n")
        os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN")
        self.remote_agent(work_script=f"echo {token} >leak.txt; echo {token} >&2")
        d, _ = self.saved(self.remote([self.case()]))
        for p in d.rglob("*"):
            if p.is_file():
                self.assertNotIn(token.encode(), p.read_bytes(), p)
        self.assertEqual((d / "work" / "leak.txt").read_text(), "[redacted]\n")

    def test_cli_remote_flag_runs_through_remote_agent(self):
        self.remote_agent()
        out = self.cli("--tool", "claude", "--ref", "HEAD", "--runs", "1", "--remote", "--evals", str(self.evals),
                       *[str(self.case("configure/a").relative_to(self.evals))])
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertTrue(out.stdout.strip().endswith("remote docker"), out.stdout)
        self.assertEqual(len(list((self.results / "claude@docker").rglob(results.RESULT))), 1)


class CliTest(RunSandbox):
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

    def test_cli_exits_2_on_a_prompt_starting_with_a_dash(self):
        self.passing_claude()
        (self.case("configure/a") / "prompt.md").write_text("-x\n")
        out = self.cli("--tool", "claude", "--ref", "HEAD", "--evals", str(self.evals), "configure/a")
        self.assertEqual(out.returncode, 2, out.stderr)
        self.assertIn("configure/a", out.stderr)
        self.assertNotIn("Traceback", out.stderr)
        self.assertEqual(self.call_count(), 0)

    def test_cli_exits_1_and_names_the_runs_the_budget_skipped(self):
        self.passing_claude()
        self.case("configure/a")
        out = self.cli("--tool", "claude", "--ref", "HEAD", "--runs", "2", "--budget-usd", "0.000001",
                       "--evals", str(self.evals), "configure/a")
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("skipped configure/a run 2", out.stdout)

    def test_cli_exits_3_on_an_auth_stop(self):
        self.claude("auth-failed.jsonl")
        self.case("configure/a")
        out = self.cli("--tool", "claude", "--ref", "HEAD", "--runs", "2", "--evals", str(self.evals), "configure/a")
        self.assertEqual(out.returncode, 3, out.stderr)
        self.assertIn("auth failed for claude", out.stdout)
        self.assertEqual(self.call_count(), 1)

    def test_cli_exits_2_on_a_usage_error_before_any_run(self):
        self.passing_claude()
        self.case("configure/a")
        base = ("--tool", "claude", "--ref", "HEAD", "--evals", str(self.evals))
        for args in (("--all", "configure/a"), (), ("--runs", "0", "configure/a"),
                     ("--budget-usd", "0", "configure/a")):
            with self.subTest(args=args):
                out = self.cli(*base, *args)
                self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
                self.assertIn("usage:", out.stderr)
        self.assertEqual(self.call_count(), 0)
        self.assertEqual(list(self.results.iterdir()), [])

    def test_cli_results_flag_picks_the_store(self):
        self.passing_claude()
        self.case("configure/a")
        store = self.tmp / "store"
        out = self.cli("--tool", "claude", "--ref", "HEAD", "--runs", "1", "--results", str(store),
                       "--evals", str(self.evals), "configure/a")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(len(list(store.rglob(results.RESULT))), 1)
        self.assertEqual(list(self.results.iterdir()), [])

    def test_every_flag_of_help_is_documented_in_the_run_cases_section(self):
        out = self.cli("--help")
        flags = set(re.findall(r"(?<![\w-])--?[a-z][\w-]*", out.stdout))
        self.assertIn("--budget-usd", flags)
        doc = (REPO / "docs" / "evals.md").read_text()
        section = doc.split("\n## Run cases\n", 1)[1].split("\n## ", 1)[0]
        self.assertEqual(sorted(f for f in flags if f"`{f}" not in section), [])

if __name__ == "__main__":
    unittest.main()
