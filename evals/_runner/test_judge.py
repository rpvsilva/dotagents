"""Tests for `judge.py`: a saved run scored against its case's rubric by a fake `claude` judge on `PATH`, printing a
good answer, an out-of-range score, prose or nothing."""
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

import adapters  # noqa: E402
import cases  # noqa: E402
import home  # noqa: E402
import judge  # noqa: E402
import results  # noqa: E402
from testlib import EvalSandbox  # noqa: E402

MINIMAL = HERE / "fixtures" / "cases" / "good" / "configure" / "minimal"
RUBRIC = """# Rubric

- correctness: the setting holds the asked value.
- **clarity**: the final answer says what changed.
"""
GOOD = {"correctness": {"score": 5, "reason": "max_parallel is 2."},
        "clarity": {"score": 3, "reason": "Says the file,\nnot the value."}}


class JudgeSandbox(EvalSandbox):
    def setUp(self):
        super().setUp()
        self.argv = self.tmp / "judge-argv"
        self.seen_home = self.tmp / "judge-home"
        self.stream = self.tmp / "judge-stream.jsonl"
        self.run_dir = self.make_run()

    def case(self, rubric=RUBRIC, **fields):
        dest = self.tmp / "evals" / "configure" / "quick"
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(MINIMAL, dest)
        data = json.loads((dest / "case.json").read_text())
        data.update(fields)
        (dest / "case.json").write_text(json.dumps(data))
        if rubric is not None:
            (dest / "rubric.md").write_text(rubric)
        return cases.load(dest)

    def make_run(self):
        """A kept run: the fixture repo changed (edited, committed and untracked files), scratch files and the
        case's transcript ending in its final answer."""
        run_dir = self.tmp / "run-1"
        work = home.make_work(MINIMAL / "fixture", run_dir / "work")
        (work / "README.md").write_text("EDITED-README\n")
        (work / "untracked.txt").write_text("UNTRACKED-FILE\n")
        (work / "committed.txt").write_text("COMMITTED-FILE\n")
        env = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "PATH": "/usr/bin:/bin"}
        home._git(work, "add", "committed.txt", env=env)
        home._git(work, "-c", "user.name=a", "-c", "user.email=a@a", "-c", "commit.gpgsign=false", "commit", "-qm",
                  "agent", env=env)
        (run_dir / "scratch" / "notes").mkdir(parents=True)
        (run_dir / "scratch" / "notes" / "plan.md").write_text("SCRATCH-PLAN\n")
        (run_dir / adapters.TRANSCRIPT).write_text(json.dumps(
            {"type": "result", "subtype": "success", "is_error": False, "result": "FINAL-ANSWER-TEXT"}) + "\n")
        results.write(run_dir, {"tool": "claude", "model": "claude-sonnet-5-5"})
        return run_dir

    def judge_says(self, text=None):
        """The fake judge: records its argv (NUL-separated) and `$HOME`, then prints a stream whose final answer is
        `text` (no stream at all when None)."""
        if text is not None:
            self.stream.write_text(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                                               "result": text}) + "\n")
        body = (f'for a in "$@"; do printf "%s\\0" "$a"; done >"{self.argv}"\n'
                f'printf "%s" "$HOME" >"{self.seen_home}"\n')
        if text is not None:
            body += f'cat "{self.stream}"\n'
        self.fake_bin("claude", body)

    def judge(self, case=None):
        return judge.judge(self.run_dir, case or self.case(), REPO)

    def args(self):
        return self.argv.read_bytes().decode().split("\0")[:-1]

    def prompt(self):
        argv = self.args()
        return argv[argv.index("-p") + 1]


class PromptTest(JudgeSandbox):
    def test_the_prompt_holds_rubric_case_prompt_answer_diff_and_scratch(self):
        self.judge_says(json.dumps(GOOD))
        self.judge()
        prompt = self.prompt()
        self.assertIn("- **clarity**: the final answer says what changed.", prompt)
        self.assertIn((MINIMAL / "prompt.md").read_text().strip(), prompt)
        self.assertIn("FINAL-ANSWER-TEXT", prompt)
        for line in ("-# Tiny repo", "+EDITED-README", "+COMMITTED-FILE", "+UNTRACKED-FILE"):
            self.assertIn(line, prompt)
        self.assertIn("notes/plan.md", prompt)
        self.assertIn("SCRATCH-PLAN", prompt)

    def test_scratch_over_the_size_cap_is_cut(self):
        (self.run_dir / "scratch" / "huge.txt").write_text("H" * (judge.SCRATCH_CAP * 3))
        self.judge_says(json.dumps(GOOD))
        self.judge()
        prompt = self.prompt()
        self.assertIn("huge.txt", prompt)
        self.assertLess(prompt.count("H" * 100) * 100, judge.SCRATCH_CAP + 100)
        self.assertLess(len(prompt.encode()), 128 * 1024)

    def test_the_judge_runs_on_the_judge_model_with_small_caps_in_a_throwaway_home(self):
        self.judge_says(json.dumps(GOOD))
        got = self.judge(self.case(judge_model="claude-sonnet-5-5"))
        argv = self.args()
        self.assertEqual(argv[0], "-p")
        self.assertEqual(argv[argv.index("--model") + 1], "claude-sonnet-5-5")
        self.assertEqual(argv[argv.index("--max-turns") + 1], str(judge.CAPS["turns"]))
        self.assertEqual(argv[argv.index("--max-budget-usd") + 1], str(judge.CAPS["cost_usd"]))
        self.assertEqual(got["judge_model"], "claude-sonnet-5-5")
        seen = Path(self.seen_home.read_text())
        self.assertNotEqual(seen, self.home)
        self.assertFalse(seen.exists(), "the judge's home outlived it")
        for token in self.tokens.values():
            self.assertNotIn(token, "\n".join(argv))


class AnswerTest(JudgeSandbox):
    def test_a_good_answer_gives_a_score_and_reason_per_criterion(self):
        self.judge_says(json.dumps(GOOD))
        got = self.judge()
        self.assertEqual(got["scores"], {"correctness": {"score": 5, "reason": "max_parallel is 2."},
                                         "clarity": {"score": 3, "reason": "Says the file, not the value."}})
        self.assertNotIn("error", got)

    def test_a_fenced_json_answer_is_read(self):
        self.judge_says("```json\n" + json.dumps(GOOD) + "\n```")
        self.assertEqual(set(self.judge()["scores"]), {"correctness", "clarity"})

    def test_the_result_records_judge_model_and_rubric_hash(self):
        self.judge_says(json.dumps(GOOD))
        got = self.judge()
        self.assertEqual(got["judge_model"], "claude-haiku-5-5")
        self.assertEqual(got["rubric_hash"], judge.rubric_hash(self.case()))
        self.assertRegex(got["rubric_hash"], r"^[0-9a-f]{64}$")
        self.assertNotEqual(judge.rubric_hash(self.case(rubric=RUBRIC + "- speed: fast.\n")), got["rubric_hash"])

    def assert_error(self, text):
        self.judge_says(text)
        got = self.judge()
        self.assertNotIn("scores", got)
        self.assertIsInstance(got["error"], str)
        self.assertTrue(got["error"])
        self.assertEqual(got["judge_model"], "claude-haiku-5-5")
        return got

    def test_an_out_of_range_score_gives_an_error_never_a_score(self):
        for score in (0, 6, 3.5, "4", True, None):
            bad = {**GOOD, "clarity": {"score": score, "reason": "x"}}
            with self.subTest(score=score):
                self.assertIn("clarity", self.assert_error(json.dumps(bad))["error"])

    def test_prose_gives_an_error(self):
        self.assertIn("JSON", self.assert_error("Correctness: 5. Clarity: 3. Nice work overall!")["error"])

    def test_a_missing_criterion_gives_an_error(self):
        self.assertIn("clarity", self.assert_error(json.dumps({"correctness": GOOD["correctness"]}))["error"])

    def test_an_empty_reason_gives_an_error(self):
        self.assert_error(json.dumps({**GOOD, "correctness": {"score": 4, "reason": "  "}}))

    def test_an_answer_that_is_not_an_object_gives_an_error(self):
        self.assert_error(json.dumps([GOOD]))

    def test_no_answer_from_the_judge_gives_an_error(self):
        self.assert_error(None)


class NoRubricTest(JudgeSandbox):
    def test_a_case_without_a_rubric_is_not_judged(self):
        self.judge_says(json.dumps(GOOD))
        self.assertIsNone(self.judge(self.case(rubric=None)))
        self.assertFalse(self.argv.exists())

    def test_a_rubric_with_no_criteria_gives_an_error(self):
        self.judge_says(json.dumps(GOOD))
        got = self.judge(self.case(rubric="# Rubric\n\nJust be good.\n"))
        self.assertIn("criteria", got["error"])
        self.assertFalse(self.argv.exists())
