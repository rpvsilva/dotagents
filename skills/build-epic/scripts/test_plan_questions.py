"""Tests for `plan_questions.py`: step 5's Field matrix questions and Test isolation approval costs, on gh-35's plan."""
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import runfolder  # noqa: E402
from testkit import SCRIPTS, Kit  # noqa: E402

PLAN_QUESTIONS = SCRIPTS / "plan_questions.py"
GH35 = SCRIPTS / "fixtures" / "gh-35-plan"
CELLS = {"due_time": ("copy",), "done_priority": ("copy", "import", "bulk done"),
         "parent": ("restore", "import", "copy")}
GUARD = "# Rules\n\n```agent-merge-guard\nconftest path **/conftest.py\nhooks path hooks/**\n```\n"


class PlanQuestionsTest(unittest.TestCase):
    def setUp(self):
        self.kit = Kit()
        self.addCleanup(self.kit.close)
        self.repo = self.kit.make_origin()
        self.kit.init_run()
        self.run_dir = self.kit.run_dir

    def rules(self, agents_md):
        """Puts `agents_md` as `AGENTS.md` on origin's `main`, the run's default branch."""
        (self.repo / "AGENTS.md").write_text(agents_md, encoding="utf-8")
        self.kit.git("add", "AGENTS.md", cwd=self.repo)
        self.kit.git("commit", "-qm", "rules", cwd=self.repo)
        self.kit.git("push", "-q", "origin", "main", cwd=self.repo)

    def plan(self, map_md=None):
        """Copies gh-35's plan into the run folder; `map_md` replaces its `map.md`."""
        for sub in ("tickets", "tasks"):
            shutil.copytree(GH35 / sub, self.run_dir / sub)
        (self.run_dir / "map.md").write_text(map_md or (GH35 / "map.md").read_text(encoding="utf-8"),
                                             encoding="utf-8")

    def printed(self, ok=True):
        p = self.kit.step(PLAN_QUESTIONS, self.run_dir)
        self.assertEqual(p.returncode == 0, ok, p.stdout + p.stderr)
        return p.stdout.splitlines()

    def test_gh35_plan_passes_plan_check(self):
        self.assertEqual(runfolder.plan_check.check(GH35)[0], [])

    def test_gh35_named_cells_are_decided_or_asked(self):
        self.rules(GUARD)
        self.plan()
        out = self.printed()
        cells = {(f, c): v for f, c, v in runfolder.plan_check.matrix_cells(
            runfolder.plan_check.section((GH35 / "map.md").read_text(encoding="utf-8"), "Field matrix"))}
        for field, commands in CELLS.items():
            for command in commands:
                value = cells[(field, command)]
                question = f"question: {field} x {command}"
                if value == "?":
                    self.assertIn(question, out)
                else:
                    self.assertIn(value, ("keep", "drop", "reset"), (field, command))
                    self.assertNotIn(question, out)

    def test_prints_one_question_per_undecided_cell_and_a_cost_per_guarded_fixture(self):
        self.rules(GUARD)
        self.plan()
        self.assertEqual(self.printed(), [
            "question: done_priority x import",
            "question: parent x import",
            "question: parent x restore",
            "cost: tests/conftest.py rule conftest",
        ])

    def test_a_fixture_matching_no_path_rule_costs_nothing(self):
        self.rules("# Rules\n\n```agent-merge-guard\nhooks path hooks/**\nexit added sys\\.exit in tests/**\n```\n")
        self.plan()
        self.assertNotIn("cost:", " ".join(self.printed()))

    def test_a_fixture_several_rules_match_costs_once_per_rule(self):
        self.rules(GUARD.replace("hooks path hooks/**", "tests path tests/**"))
        self.plan()
        self.assertEqual([line for line in self.printed() if line.startswith("cost:")],
                         ["cost: tests/conftest.py rule conftest", "cost: tests/conftest.py rule tests"])

    def test_a_fixture_cited_with_a_line_number_still_matches(self):
        self.rules(GUARD)
        self.plan("## Field matrix\n\n| field | copy |\n|---|---|\n| due | keep |\n\n## Test isolation\n\n"
                  "- `TODO_DB`: reset in `tests/conftest.py:12-30`.\n")
        self.assertEqual(self.printed(), ["cost: tests/conftest.py rule conftest"])

    def test_a_decided_plan_without_guarded_fixtures_prints_none(self):
        self.rules("# Rules\n")
        self.plan("## Field matrix\n\nNone: no item model.\n\n## Test isolation\n\n- `TODO_DB`: `tests/conftest.py`.\n")
        self.assertEqual(self.printed(), ["none"])

    def test_a_missing_map_md_fails(self):
        self.assertEqual(self.printed(ok=False), ["FAIL run no-map-md: run the planner's pass B first"])

    def test_a_missing_run_folder_fails(self):
        p = self.kit.step(PLAN_QUESTIONS, self.kit.tmp / "nope")
        self.assertEqual((p.returncode, p.stdout.splitlines()), (1, [f"FAIL run no-run-folder: {self.kit.tmp / 'nope'}"]))

    def test_unreadable_guard_rules_fail(self):
        self.rules("```agent-merge-guard\nconftest conftest.py\n```\n")
        self.plan()
        self.assertEqual(self.printed(ok=False), ["FAIL run bad-rule: line 2: conftest conftest.py"])


if __name__ == "__main__":
    unittest.main()
