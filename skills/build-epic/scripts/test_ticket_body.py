"""Tests for the ticket PR body `steplib/prbody.py` generates and `ship_ticket.py open` sends (spec behaviours 54, 55).

The fixture run folder is `fixtures/ticket-body/run`, its PR template `fixtures/ticket-body/template.md`, and the body
it must render `fixtures/ticket-body/expected.md`.
"""
import json
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import prbody, runfolder  # noqa: E402
from test_ship import SHIP, Base  # noqa: E402
from testkit import SCRIPTS, Kit  # noqa: E402

FIXTURE = SCRIPTS / "fixtures" / "ticket-body"
REPO_TEMPLATE = SCRIPTS.parents[2] / ".github" / "pull_request_template.md"


class FixtureRun(unittest.TestCase):
    def setUp(self):
        self.kit = Kit()
        self.addCleanup(self.kit.close)
        self.run_dir = self.kit.tmp / "fixture-run"
        shutil.copytree(FIXTURE / "run", self.run_dir)
        self.ticket = self.run_dir / "tickets" / "07-ticket-body.md"

    def body(self, template):
        return prbody.ticket_body(self.run_dir, self.ticket, template=template)


class TicketBodyTest(FixtureRun):
    def test_the_fixture_run_folder_renders_the_expected_body(self):
        body = self.body((FIXTURE / "template.md").read_text(encoding="utf-8"))
        self.assertEqual(body, (FIXTURE / "expected.md").read_text(encoding="utf-8"))

    def test_each_heading_of_the_repos_template_is_filled_and_testing_holds_the_saved_suite_results(self):
        template = REPO_TEMPLATE.read_text(encoding="utf-8")
        body = self.body(template)
        self.assertEqual(prbody.empty_sections(template, body), [])
        testing = prbody.section(body, "Testing")
        self.assertIn("- `tests/run.sh`: passed in 30.2s (12 passed, 0 failed, 1 skipped)", testing)
        self.assertIn("- `tests/run.sh`: passed in 28s", testing)
        self.assertIn("- `tests/run.sh` (07-01): zsh not installed", testing)
        self.assertNotIn("never-shown", body)

    def test_a_template_heading_the_run_cannot_fill_is_left_empty(self):
        template = REPO_TEMPLATE.read_text(encoding="utf-8") + "\n## Screenshots\n"
        self.assertEqual(prbody.empty_sections(template, self.body(template)), ["Screenshots"])

    def test_with_no_saved_suite_results_testing_is_left_empty(self):
        shutil.rmtree(self.run_dir / "reports")
        template = REPO_TEMPLATE.read_text(encoding="utf-8")
        self.assertEqual(prbody.empty_sections(template, self.body(template)), ["Testing"])

    def test_without_a_template_the_body_has_a_summary_and_testing(self):
        body = self.body(None)
        self.assertTrue(body.startswith("## Summary\n\nTicket PR bodies generated from the plan"), body)
        self.assertIn("- `tests/run.sh`: passed in 28s", prbody.section(body, "Testing"))
        shutil.rmtree(self.run_dir / "reports")
        self.assertEqual(prbody.section(self.body(None), "Testing"), prbody.NO_RESULT)

    def test_the_body_lists_each_task_pr_and_its_acceptance_criteria_then_closes_the_issues(self):
        body = self.body(None)
        self.assertEqual(prbody.section(body, "Tasks").splitlines(), [
            "- Bodies from the run folder: https://github.com/o/r/pull/41",
            "- Shipping takes no prose: https://github.com/o/r/pull/42",
            "- Docs for the new body: no PR",
        ])
        criteria = prbody.section(body, "Acceptance criteria")
        self.assertIn("- `ship_ticket.py` accepts no body argument.", criteria)
        self.assertNotIn("[ ]", criteria)
        self.assertNotIn("Never shows up", body)
        self.assertTrue(body.endswith("Closes #191\nCloses #192\n"), body)


class EmptySectionsTest(unittest.TestCase):
    FILLED = ("## Summary\n\nCuts the JSON step in two.\n\n## Testing\n\ntests/run.sh: all ok\n\n"
              "## Risk\n\nLow: scripts only.\n\n## How to revert\n\nRevert the squash commit.\n")

    def test_a_section_missing_blank_todo_na_a_comment_or_an_unchecked_box_counts_as_empty(self):
        template = REPO_TEMPLATE.read_text(encoding="utf-8")
        self.assertEqual(prbody.empty_sections(template, self.FILLED), [])
        cases = {
            "missing": (self.FILLED.split("## How to revert")[0], "How to revert"),
            "blank": (self.FILLED.replace("Low: scripts only.", ""), "Risk"),
            "TODO": (self.FILLED.replace("tests/run.sh: all ok", "TODO"), "Testing"),
            "N/A": (self.FILLED.replace("Revert the squash commit.", "N/A"), "How to revert"),
            "a comment": (self.FILLED.replace("Low: scripts only.", "<!-- say the risk -->"), "Risk"),
            "an unchecked box": (self.FILLED.replace("tests/run.sh: all ok", "- [ ] ran the tests"), "Testing"),
        }
        for name, (body, empty) in cases.items():
            with self.subTest(name):
                self.assertEqual(prbody.empty_sections(template, body), [empty])


class SuiteResultTest(FixtureRun):
    def save(self, ident, suites):
        (self.run_dir / "reports" / f"{ident}.checks.json").write_text(json.dumps(suites), encoding="utf-8")

    def testing_section(self):
        return prbody.section(self.body(None), "Testing")

    def test_a_suite_is_passed_or_failed_by_its_exit_code_whatever_its_passed_count(self):
        self.save("07-01", [{"command": "red", "rc": 1, "passed": 5, "failed": 1},
                            {"command": "none-ran", "rc": 0, "passed": 0}])
        testing = self.testing_section()
        self.assertIn("- `red`: failed (5 passed, 1 failed)", testing)
        self.assertIn("- `none-ran`: passed (0 passed)", testing)

    def test_an_old_result_with_a_boolean_passed_still_reads(self):
        self.save("07-01", [{"command": "old-red", "passed": False}, {"command": "old-green", "passed": True}])
        testing = self.testing_section()
        self.assertIn("- `old-red`: failed\n", testing)
        self.assertIn("- `old-green`: passed\n", testing)

    def test_the_task_body_reads_the_new_shape_too(self):
        self.save("07-01", [{"command": "red", "rc": 2, "passed": 3, "failed": 0}])
        task = self.run_dir / "tasks" / "07-01-prbody.md"
        body = prbody.task_body(self.run_dir, "07-01", task, self.ticket)
        self.assertIn("- `red`: failed (3 passed, 0 failed)", prbody.section(body, "Result"))


class ShipOpenBodyTest(Base):
    def open(self, *args):
        return self.one_line(self.kit.step(SHIP, self.kit.run_dir, "01", "open", *args))

    def test_ship_ticket_accepts_no_summary_or_body_argument(self):
        body = self.kit.tmp / "body.md"
        body.write_text("## Summary\n\nWritten by hand.\n", encoding="utf-8")
        for args in (["--body-file", str(body)], ["--body", "x"], ["--summary", "x"], [str(body)]):
            with self.subTest(args):
                self.assertRegex(self.open(*args), r"^FAIL 01 usage: ")
        self.assertEqual(self.kit.gh_calls(), [])

    def test_open_sends_the_generated_body_filling_the_repos_template(self):
        self.commit_to_base(".github/pull_request_template.md", REPO_TEMPLATE.read_text(encoding="utf-8"))
        (self.kit.run_dir / "reports").mkdir()
        (self.kit.run_dir / "reports" / "01-01.checks.json").write_text(
            json.dumps([{"command": "tests/run.sh", "rc": 0, "passed": 4, "failed": 0, "seconds": 2}]),
encoding="utf-8")
        runfolder.set_header(self.ticket, "PR", "https://github.com/o/r/pull/20")
        self.assertEqual(self.open(), "OK 01 open pr=20")
        edit = self.calls("pr", "edit")[0]
        sent = Path(edit[edit.index("--body-file") + 1]).read_text(encoding="utf-8")
        self.assertEqual(prbody.empty_sections(REPO_TEMPLATE.read_text(encoding="utf-8"), sent), [])
        self.assertIn("- `tests/run.sh`: passed in 2s (4 passed, 0 failed)", prbody.section(sent, "Testing"))

    def test_open_with_no_saved_suite_results_refuses_an_empty_testing_and_sends_nothing(self):
        self.commit_to_base(".github/pull_request_template.md", REPO_TEMPLATE.read_text(encoding="utf-8"))
        runfolder.set_header(self.ticket, "PR", "https://github.com/o/r/pull/20")
        self.assertRegex(self.open(), r"^FAIL 01 empty-template-section Testing \(log: ")
        self.assertEqual(self.calls("pr", "edit"), [])


if __name__ == "__main__":
    unittest.main()
