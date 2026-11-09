"""Tests for `cases.py`: the eval case format, through its `check` CLI and `load()`."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "cases.py"
CASES = HERE / "fixtures" / "cases"
GOOD = CASES / "good"
BAD = CASES / "bad"

sys.path.insert(0, str(HERE))

import cases  # noqa: E402


def check(evals_dir):
    out = subprocess.run([sys.executable, "-I", str(SCRIPT), "check", str(evals_dir)], capture_output=True, text=True,
                         stdin=subprocess.DEVNULL)
    return out.returncode, out.stdout.splitlines(), out.stderr


class GoodCases(unittest.TestCase):
    def test_check_prints_ok_for_valid_cases(self):
        code, lines, err = check(GOOD)
        self.assertEqual((code, lines, err), (0, ["ok"], ""))

    def test_check_skips_tooling_folders(self):
        # good/_tooling holds no case.json at all: a `_` folder is never read as a skill.
        self.assertTrue((GOOD / "_tooling").is_dir())
        self.assertEqual(check(GOOD)[1], ["ok"])

    def test_check_prints_ok_for_an_evals_dir_with_no_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(check(tmp)[:2], (0, ["ok"]))

    def test_load_fills_in_defaults(self):
        case = cases.load(GOOD / "configure" / "minimal")
        self.assertEqual(case["skill"], "configure")
        self.assertEqual(case["case"], "minimal")
        self.assertEqual(case["runs"], 3)
        self.assertEqual(case["judge_model"], "claude-haiku-5-5")
        self.assertEqual(case["install"], ["configure"])
        self.assertEqual(case["prompt"], "prompt.md")
        self.assertEqual(case["settings"], {})
        self.assertIsNone(case["rubric"])
        self.assertIsNone(case["input"])
        self.assertEqual(case["model"], "claude-sonnet-5-5")
        self.assertEqual(case["caps"], {"turns": 20, "secs": 600, "cost_usd": 1})
        self.assertEqual(sorted(case["checks"]), ["settings-written"])
        self.assertEqual(case["fixture"], GOOD / "configure" / "minimal" / "fixture")

    def test_load_keeps_given_fields(self):
        d = GOOD / "configure" / "full"
        case = cases.load(d)
        self.assertEqual(case["prompt"], "ask.md")
        self.assertEqual(case["runs"], 5)
        self.assertEqual(case["judge_model"], "claude-sonnet-5-5")
        self.assertEqual(case["install"], ["configure", "remember"])
        self.assertEqual(case["settings"], {"build-epic": {"max_parallel": 2}})
        self.assertEqual(case["rubric"], "criteria.md")
        self.assertEqual(case["input"], d / "input")
        self.assertEqual(sorted(case["checks"]), ["first", "second"])
        self.assertEqual(case["checks"]["first"], d / "checks" / "first.sh")

    def test_load_takes_rubric_md_when_present(self):
        self.assertEqual(cases.load(GOOD / "configure" / "with-rubric")["rubric"], "rubric.md")

    def test_load_refuses_a_malformed_case(self):
        with self.assertRaises(cases.CaseError) as err:
            cases.load(BAD / "configure" / "model-alias")
        self.assertIn("alias", str(err.exception))


class BadCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code, cls.lines, cls.err = check(BAD)

    def problems(self, case):
        prefix = f"{BAD / case}: "
        return [line[len(prefix):] for line in self.lines if line.startswith(prefix)]

    def assertProblem(self, case, words):
        found = self.problems(case)
        self.assertTrue(any(words in p for p in found), f"{case}: no problem saying {words!r} in {found}")

    def test_check_exits_1(self):
        self.assertEqual(self.code, 1)
        self.assertNotIn("ok", self.lines)
        self.assertEqual(self.err, "")

    def test_a_folder_naming_no_skill_fails(self):
        self.assertTrue(any(line.startswith(f"{BAD / 'no-such-skill'}: ") and "skills/no-such-skill/SKILL.md" in line
                            for line in self.lines), self.lines)

    def test_tooling_folders_are_skipped(self):
        self.assertFalse([line for line in self.lines if "_tooling" in line])

    def test_a_missing_prompt_fails(self):
        self.assertProblem("configure/no-prompt", "prompt file prompt.md missing")

    def test_an_empty_prompt_fails(self):
        self.assertProblem("configure/empty-prompt", "prompt file prompt.md is empty")

    def test_a_missing_named_prompt_fails(self):
        self.assertProblem("configure/named-prompt-missing", "prompt file ask.md missing")

    def test_checks_with_no_executable_fail(self):
        self.assertProblem("configure/no-executable-check", "no executable in checks/")

    def test_a_missing_checks_folder_fails(self):
        self.assertProblem("configure/no-checks", "no executable in checks/")

    def test_a_model_alias_fails(self):
        self.assertProblem("configure/model-alias", "model 'sonnet' is an alias")

    def test_a_model_ending_in_latest_fails(self):
        self.assertProblem("configure/model-latest", "model 'gpt-5-latest' is an alias")

    def test_a_claude_model_not_in_pricing_fails(self):
        self.assertProblem("configure/model-unpriced", "model 'claude-sonnet-9-9' is an alias")

    def test_a_judge_model_alias_fails(self):
        self.assertProblem("configure/judge-alias", "judge_model 'haiku' is an alias")

    def test_a_missing_model_fails(self):
        self.assertProblem("configure/no-model", "model missing")

    def test_a_missing_fixture_fails(self):
        self.assertProblem("configure/no-fixture", "fixture/ missing")

    def test_invalid_json_fails(self):
        self.assertProblem("configure/bad-json", "case.json is not valid JSON")

    def test_a_missing_case_json_fails(self):
        self.assertProblem("configure/no-case-json", "case.json missing")

    def test_an_unknown_key_fails(self):
        self.assertProblem("configure/unknown-key", "unknown key 'modle'")

    def test_an_unknown_cap_fails(self):
        self.assertProblem("configure/unknown-cap", "unknown key 'caps.tokens'")

    def test_a_cap_that_is_not_positive_fails(self):
        self.assertProblem("configure/zero-cap", "caps.turns must be a positive")

    def test_a_missing_cap_fails(self):
        self.assertProblem("configure/missing-cap", "caps.cost_usd missing")

    def test_runs_that_are_not_positive_fail(self):
        self.assertProblem("configure/zero-runs", "runs must be a positive integer")

    def test_settings_that_are_not_an_object_fail(self):
        self.assertProblem("configure/bad-settings", "settings must be an object")

    def test_install_that_is_not_a_list_of_names_fails(self):
        self.assertProblem("configure/bad-install", "install must be a list of part names")

    def test_a_missing_named_rubric_fails(self):
        self.assertProblem("configure/rubric-missing", "rubric file criteria.md missing")

    def test_each_malformed_case_reports_only_its_own_problem(self):
        for case in sorted((BAD / "configure").iterdir()):
            with self.subTest(case=case.name):
                self.assertEqual(len(self.problems(f"configure/{case.name}")), 1, self.lines)


class FixtureRules(unittest.TestCase):
    """Fixtures holding `.git` or `test_*.py` are built here: committed, they would break the repo itself."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.case = self.tmp / "configure" / "case"
        shutil.copytree(GOOD / "configure" / "minimal", self.case, symlinks=True)

    def test_a_fixture_holding_git_fails(self):
        (self.case / "fixture" / ".git").mkdir()
        self.assertEqual(check(self.tmp)[:2], (1, [f"{self.case}: fixture/ holds .git"]))

    def test_a_fixture_holding_a_python_test_fails(self):
        (self.case / "fixture" / "pkg").mkdir()
        (self.case / "fixture" / "pkg" / "test_thing.py").write_text("")
        self.assertEqual(check(self.tmp)[:2], (1, [f"{self.case}: fixture/ holds pkg/test_thing.py"]))

    def test_an_empty_fixture_is_fine(self):
        shutil.rmtree(self.case / "fixture")
        (self.case / "fixture").mkdir()
        self.assertEqual(check(self.tmp)[:2], (0, ["ok"]))

    def test_a_fixture_that_is_a_file_fails(self):
        shutil.rmtree(self.case / "fixture")
        (self.case / "fixture").write_text("")
        self.assertEqual(check(self.tmp)[:2], (1, [f"{self.case}: fixture/ missing"]))

    def test_a_case_json_that_is_not_an_object_fails(self):
        (self.case / "case.json").write_text(json.dumps(["model"]))
        self.assertEqual(check(self.tmp)[:2], (1, [f"{self.case}: case.json must be an object"]))

    def test_a_cap_given_as_a_boolean_fails(self):
        data = json.loads((self.case / "case.json").read_text())
        data["caps"]["turns"] = True
        (self.case / "case.json").write_text(json.dumps(data))
        self.assertEqual(check(self.tmp)[1], [f"{self.case}: caps.turns must be a positive integer"])

    def test_a_fractional_turn_cap_fails_and_a_fractional_cost_cap_is_fine(self):
        data = json.loads((self.case / "case.json").read_text())
        data["caps"].update(turns=1.5, cost_usd=0.25)
        (self.case / "case.json").write_text(json.dumps(data))
        self.assertEqual(check(self.tmp)[1], [f"{self.case}: caps.turns must be a positive integer"])


if __name__ == "__main__":
    unittest.main()
