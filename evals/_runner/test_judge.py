"""Tests for `judge.py`: a saved run scored against its case's rubric by a fake `claude` judge on `PATH`, printing a
good answer, an out-of-range score, prose or nothing."""
import errno
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

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

    def judge_says(self, text=None, usage=None):
        """The fake judge: records its argv (NUL-separated) and `$HOME`, then prints a stream whose final answer is
        `text` (no stream at all when None), reporting `usage` as its result event's `modelUsage` when given."""
        if text is not None:
            event = {"type": "result", "subtype": "success", "is_error": False, "result": text}
            if usage is not None:
                event["modelUsage"] = usage
            self.stream.write_text(json.dumps(event) + "\n")
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

    def assert_fits_and_scores(self):
        self.judge_says(json.dumps(GOOD))
        got = self.judge()
        self.assertLess(len(self.prompt().encode()), 128 * 1024)
        self.assertNotIn("\0", self.prompt())
        self.assertEqual(set(got.get("scores", {})), {"correctness", "clarity"}, got)

    def test_invalid_utf8_in_scratch_and_work_stays_under_the_argument_limit(self):
        junk = b"\xff" * (judge.DIFF_CAP * 3)
        (self.run_dir / "scratch" / "junk.txt").write_bytes(junk)
        (self.run_dir / "work" / "junk.txt").write_bytes(junk.replace(b"\xff" * 100, b"\xff" * 99 + b"\n"))
        self.assert_fits_and_scores()
        self.assertIn("junk.txt", self.prompt())

    def test_binary_files_in_scratch_and_work_are_named_without_their_content(self):
        blob = (b"BINARY-CONTENT\0" + b"\xff\0" * 1000) * 20
        (self.run_dir / "scratch" / "blob.bin").write_bytes(blob)
        (self.run_dir / "work" / "blob.bin").write_bytes(blob)
        (self.run_dir / "work" / "late.txt").write_bytes(b"x\n" * 10_000 + b"LATE\0NUL\n")
        self.assert_fits_and_scores()
        prompt = self.prompt()
        self.assertIn("blob.bin", prompt)
        self.assertNotIn("BINARY-CONTENT", prompt)

    def test_a_judge_that_cannot_start_gives_an_error(self):
        # E2BIG raised by the start itself: Linux refuses one argument over 128 KiB, macOS only a total over ~1 MiB.
        real_run = subprocess.run

        def run(argv, *args, **kwargs):
            if "--max-turns" in argv:
                raise OSError(errno.E2BIG, "Argument list too long")
            return real_run(argv, *args, **kwargs)

        self.judge_says(json.dumps(GOOD))
        with mock.patch.object(judge.subprocess, "run", side_effect=run):
            got = self.judge()
        self.assertNotIn("scores", got)
        self.assertIn("could not start", got["error"])
        self.assertEqual(got["judge_model"], "claude-haiku-5-5")

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

    def test_an_answer_naming_no_criterion_gives_an_error(self):
        extra = {**GOOD, "speed": {"score": 5, "reason": "Fast."}}
        self.assertIn("speed", self.assert_error(json.dumps(extra))["error"])

    def test_an_answer_that_is_not_an_object_gives_an_error(self):
        self.assert_error(json.dumps([GOOD]))

    def test_no_answer_from_the_judge_gives_an_error(self):
        self.assert_error(None)


class CriteriaTest(unittest.TestCase):
    def test_the_criteria_are_the_top_level_list_items_of_any_kind_each_once(self):
        rubric = ("# Rubric\n\nprose: not an item\n1. **correctness**: right.\n2) `clarity`\n"
                  "   - nested: not top-level\n* correctness: again\n+ speed: fast.\n")
        self.assertEqual(judge.criteria(rubric), ["correctness", "clarity", "speed"])


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


class CostTest(JudgeSandbox):
    USAGE = {"claude-haiku-5-5": {"inputTokens": 1000, "outputTokens": 200, "cacheReadInputTokens": 0,
                                  "cacheCreationInputTokens": 0}}

    def test_the_judges_cost_is_priced_from_its_tokens(self):
        self.judge_says(json.dumps(GOOD), usage=self.USAGE)
        got = self.judge()
        prices = json.loads((REPO / "skills" / "telemetry" / "pricing.json").read_text())["models"]["claude-haiku-5-5"]
        self.assertEqual(got["cost_usd"], round((1000 * prices["input"] + 200 * prices["output"]) / 1_000_000, 6))
        self.assertEqual(judge.spent(got), got["cost_usd"])

    def test_a_judge_reporting_no_tokens_has_unknown_cost_counted_as_its_cost_cap(self):
        self.judge_says(json.dumps(GOOD))
        got = self.judge()
        self.assertIn("cost_usd", got)
        self.assertIsNone(got["cost_usd"])
        self.assertEqual(judge.spent(got), judge.CAPS["cost_usd"])

    def test_a_bad_answer_still_records_what_the_judge_cost(self):
        self.judge_says("not json", usage=self.USAGE)
        got = self.judge()
        self.assertIn("error", got)
        self.assertGreater(got["cost_usd"], 0)

    def test_a_judge_that_never_ran_costs_nothing(self):
        self.assertEqual(judge.spent(None), 0)
        got = self.judge(self.case(rubric="no criteria here\n"))
        self.assertNotIn("cost_usd", got)
        self.assertEqual(judge.spent(got), 0)


if __name__ == "__main__":
    unittest.main()
