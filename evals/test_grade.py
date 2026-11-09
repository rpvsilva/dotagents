"""Tests for `grade.py`: saved runs re-checked with the current checks and re-judged only when the rubric changed or
`--rejudge`, with a fake `claude` on `PATH` that logs whether it got the case's prompt or a judge prompt."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "_runner"))

import grade  # noqa: E402
import results  # noqa: E402
from run import run_cases  # noqa: E402
from test_run import RUBRIC, SCORES, RunSandbox, git  # noqa: E402

FAILING = "#!/usr/bin/env bash\nexit 1\n"


class GradeSandbox(RunSandbox):
    def ran(self, case, **kw):
        """Run `case` once with the judging fake and forget its calls: what follows is the grade's."""
        got = self.run_cases([case], **kw)
        self.roles.unlink(missing_ok=True)
        return got["runs"][0]["dir"]

    def grade(self, *names, **kw):
        kw.setdefault("evals", self.evals)
        with open(self.out, "a") as out:
            return grade.grade(list(names), out=out, **kw)

    def rubric_case(self, name="configure/quick", **fields):
        case = self.case(name, **fields)
        (case / "rubric.md").write_text(RUBRIC)
        return case


class RecheckTest(GradeSandbox):
    def test_a_changed_check_flips_checks_and_passed_and_updates_checks_hash(self):
        self.judging_claude(SCORES)
        case = self.case()
        d = self.ran(case)
        before = results.read(d)
        self.assertTrue(before["passed"])
        (case / "checks" / "settings-written.sh").write_text(FAILING)
        self.grade("configure/quick")
        after = results.read(d)
        self.assertEqual(after["checks"], {"settings-written": False, "no-github": True})
        self.assertFalse(after["passed"])
        self.assertNotEqual(after["checks_hash"], before["checks_hash"])
        for field in results.FIELDS:
            if field not in ("checks", "checks_hash", "passed"):
                self.assertEqual(after[field], before[field], field)
        self.assertEqual(self.roles_called(), [])

    def test_a_check_added_after_the_run_is_run_on_the_saved_state(self):
        self.judging_claude(SCORES)
        case = self.case()
        d = self.ran(case)
        new = case / "checks" / "added.sh"
        new.write_text("#!/usr/bin/env bash\n[ -f .agents/settings.json ]\n")
        new.chmod(0o755)
        self.grade("configure/quick")
        self.assertEqual(results.read(d)["checks"], {"added": True, "settings-written": True, "no-github": True})

    def test_checks_run_on_the_kept_state_with_the_checkout_at_the_runs_commit(self):
        repo = self.fake_repo("exit 0\n")
        (repo / "marker").write_text("first\n")
        git(repo, "add", "-A")
        git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "commit", "-qm", "1")
        self.judging_claude(SCORES)
        case = self.case()
        with open(self.out, "w") as out:
            got = run_cases("claude", "HEAD", [case], runs=1, repo=repo, out=out)
        d = got["runs"][0]["dir"]
        (d / "home" / "home-file").write_text("x")
        (d / "scratch" / "scratch-file").write_text("x")
        (repo / "marker").write_text("second\n")
        git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "commit", "-qam", "2")
        seen = self.tmp / "seen"
        (case / "checks" / "settings-written.sh").write_text(
            f'#!/usr/bin/env bash\nprintf "%s\\n" "$PWD" "$EVAL_WORK" "$EVAL_HOME" "$EVAL_SCRATCH" "$EVAL_CHECKOUT" '
            f'"$(cat "$EVAL_CHECKOUT/marker")" >"{seen}"\n'
            '[ -f "$EVAL_HOME/home-file" ] && [ -f "$EVAL_SCRATCH/scratch-file" ]\n')
        self.grade("configure/quick", repo=repo)
        cwd, work, home, scratch, checkout, marker = seen.read_text().splitlines()
        self.assertEqual((Path(cwd), Path(work), Path(home), Path(scratch)),
                         (d / "work", d / "work", d / "home", d / "scratch"))
        self.assertEqual(marker, "first")
        self.assertNotEqual(Path(checkout), repo)
        self.assertFalse(Path(checkout).exists())
        self.assertTrue(results.read(d)["checks"]["settings-written"])

    def test_a_kept_gh_call_still_fails_no_github(self):
        self.judging_claude(SCORES)
        d = self.ran(self.case())
        (d / "gh-calls").write_text("pr list\n")
        self.grade("configure/quick")
        self.assertEqual(results.read(d)["checks"]["no-github"], False)
        self.assertFalse(results.read(d)["passed"])

    def test_a_capped_run_stays_failed_whatever_its_checks(self):
        self.judging_claude(SCORES, stream="max-turns.jsonl")
        d = self.ran(self.case())
        self.grade("configure/quick")
        res = results.read(d)
        self.assertTrue(all(res["checks"].values()))
        self.assertEqual(res["capped"], "turns")
        self.assertFalse(res["passed"])

    def test_an_install_error_run_is_left_as_it_is(self):
        repo = self.fake_repo("exit 1\n")
        self.judging_claude(SCORES)
        with open(self.out, "w") as out:
            got = run_cases("claude", "HEAD", [self.case()], runs=1, repo=repo, out=out)
        d = got["runs"][0]["dir"]
        before = (d / results.RESULT).read_text()
        self.grade("configure/quick", repo=repo)
        self.assertEqual((d / results.RESULT).read_text(), before)
        self.assertIn("error install", self.printed())


class NoToolTest(GradeSandbox):
    def test_with_the_rubric_unchanged_no_tool_binary_is_called(self):
        self.judging_claude(SCORES)
        case = self.rubric_case()
        d = self.ran(case)
        before = results.read(d)["rubric"]
        self.assertIn("scores", before)
        # Any tool call now would be logged and fail.
        for tool in ("claude", "codex", "cursor-agent"):
            self.fake_bin(tool, f'echo {tool} >>"{self.calls}"\nexit 1\n')
        self.calls.unlink(missing_ok=True)
        (case / "checks" / "settings-written.sh").write_text(FAILING)
        self.grade("configure/quick")
        self.assertEqual(self.call_count(), 0)
        self.assertEqual(results.read(d)["rubric"], before)
        self.assertFalse(results.read(d)["passed"])

    def test_a_changed_rubric_calls_only_the_judge(self):
        self.judging_claude(SCORES)
        case = self.rubric_case()
        d = self.ran(case)
        new = RUBRIC + "- clarity: the answer says what changed.\n"
        (case / "rubric.md").write_text(new)
        scores = {**SCORES, "clarity": {"score": 2, "reason": "Terse."}}
        self.judging_claude(scores)
        self.grade("configure/quick")
        self.assertEqual(self.roles_called(), ["judge"])
        self.assertEqual(results.read(d)["rubric"], {"scores": scores, "judge_model": "claude-haiku-5-5",
                                                     "rubric_hash": hashlib.sha256(new.encode()).hexdigest()})

    def test_a_changed_judge_model_calls_only_the_judge_on_the_new_model(self):
        self.judging_claude(SCORES)
        case = self.rubric_case()
        d = self.ran(case)
        data = json.loads((case / "case.json").read_text())
        (case / "case.json").write_text(json.dumps({**data, "judge_model": "claude-opus-5-5"}))
        self.grade("configure/quick")
        self.assertEqual(self.roles_called(), ["judge"])
        self.assertEqual(results.read(d)["rubric"]["judge_model"], "claude-opus-5-5")

    def test_rejudge_calls_only_the_judge_even_with_the_rubric_unchanged(self):
        self.judging_claude(SCORES)
        d = self.ran(self.rubric_case(), runs=2)
        self.grade("configure/quick", rejudge=True)
        self.assertEqual(self.roles_called(), ["judge", "judge"])
        self.assertIn("scores", results.read(d)["rubric"])

    def test_a_rubric_added_after_the_run_is_judged_and_one_removed_is_dropped(self):
        self.judging_claude(SCORES)
        case = self.case()
        d = self.ran(case)
        self.assertIsNone(results.read(d)["rubric"])
        (case / "rubric.md").write_text(RUBRIC)
        self.grade("configure/quick")
        self.assertEqual(self.roles_called(), ["judge"])
        self.assertIn("scores", results.read(d)["rubric"])
        (case / "rubric.md").unlink()
        self.grade("configure/quick")
        self.assertEqual(self.roles_called(), ["judge"])
        self.assertIsNone(results.read(d)["rubric"])


class PickTest(GradeSandbox):
    def test_only_the_named_cases_are_graded_and_all_grades_every_case(self):
        self.judging_claude(SCORES)
        a, b = self.case("configure/a"), self.case("configure/b")
        da, db = self.ran(a), self.ran(b)
        for case in (a, b):
            (case / "checks" / "settings-written.sh").write_text(FAILING)
        self.grade("configure/a")
        self.assertEqual((results.read(da)["passed"], results.read(db)["passed"]), (False, True))
        self.grade(every=True)
        self.assertFalse(results.read(db)["passed"])

    def test_ref_grades_only_the_runs_at_that_commit(self):
        repo = self.fake_repo("exit 0\n")
        self.judging_claude(SCORES)
        case = self.case()
        with open(self.out, "w") as out:
            first = run_cases("claude", "HEAD", [case], runs=1, repo=repo, out=out)["runs"][0]["dir"]
        git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "commit", "-q",
            "--allow-empty", "-m", "2")
        with open(self.out, "w") as out:
            second = run_cases("claude", "HEAD", [case], runs=1, repo=repo, out=out)["runs"][0]["dir"]
        (case / "checks" / "settings-written.sh").write_text(FAILING)
        self.grade("configure/quick", ref="HEAD~1", repo=repo)
        self.assertEqual((results.read(first)["passed"], results.read(second)["passed"]), (False, True))

    def test_a_named_case_with_no_saved_run_is_refused(self):
        self.case()
        with self.assertRaises(grade.GradeError) as caught:
            self.grade("configure/quick")
        self.assertIn("configure/quick", str(caught.exception))

    def test_a_saved_run_whose_case_is_gone_is_named_and_skipped(self):
        self.judging_claude(SCORES)
        case = self.case()
        d = self.ran(case)
        before = (d / results.RESULT).read_text()
        shutil.rmtree(case)
        got = self.grade(every=True)
        self.assertEqual((d / results.RESULT).read_text(), before)
        self.assertEqual(len(got["skipped"]), 1)
        self.assertIn("no case", self.printed())


    def test_a_result_json_kept_inside_a_runs_state_is_not_taken_as_a_run(self):
        self.judging_claude(SCORES)
        case = self.case()
        d = self.ran(case)
        for sub in ("work", "home", "scratch"):
            (d / sub).mkdir(exist_ok=True)
            (d / sub / results.RESULT).write_text("[1, 2]\n")
        (case / "checks" / "settings-written.sh").write_text(FAILING)
        got = self.grade("configure/quick")
        self.assertEqual([r for r, _ in got["graded"]], [d])
        self.assertEqual(got["skipped"], [])
        self.assertFalse(results.read(d)["passed"])

    def test_a_saved_result_that_does_not_read_is_named_and_skipped(self):
        self.judging_claude(SCORES)
        a, b = self.case("configure/a"), self.case("configure/b")
        da, db = self.ran(a), self.ran(b)
        (db / results.RESULT).write_text("not json\n")
        (a / "checks" / "settings-written.sh").write_text(FAILING)
        got = self.grade(every=True)
        self.assertEqual([r for r, _ in got["graded"]], [da])
        self.assertEqual([r for r, _ in got["skipped"]], [db])
        self.assertFalse(results.read(da)["passed"])
        self.assertIn(str(db / results.RESULT), self.printed())


class CliTest(GradeSandbox):
    def cli(self, *args):
        return subprocess.run([sys.executable, "-I", str(HERE / "grade.py"), *args], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, env=os.environ.copy())

    def test_cli_regrades_into_the_results_store_it_is_given(self):
        self.judging_claude(SCORES)
        case = self.case()
        store = self.tmp / "store"
        d = self.ran(case, results_root=store)
        (case / "checks" / "settings-written.sh").write_text(FAILING)
        out = self.cli("--results", str(store), "--evals", str(self.evals), "configure/quick")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertRegex(out.stdout, r"^configure/quick +run 1 .* fail")
        self.assertFalse(results.read(d)["passed"])
        self.assertEqual(self.roles_called(), [])

    def test_cli_exits_2_on_a_usage_error_or_a_case_with_no_saved_run(self):
        self.case()
        for args in ((), ("--all", "configure/quick"), ("configure/quick",)):
            with self.subTest(args=args):
                out = self.cli("--evals", str(self.evals), *args)
                self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
                self.assertNotIn("Traceback", out.stderr)


if __name__ == "__main__":
    unittest.main()
