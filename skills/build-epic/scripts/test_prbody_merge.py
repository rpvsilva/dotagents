"""Tests for the `## Merge` section `steplib/prbody.py` renders from `<run>/decisions.md` (spec behaviour 28)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import decisions, prbody  # noqa: E402
from testkit import Kit  # noqa: E402

TICKET = "# Alpha\nStatus: building\nBranch: feat/alpha\nIssue: #42\n"
TASK = "# 01-02: JSON export\nStatus: in-progress\nTicket: 01\n\n## What to build\nExports JSON.\n\n## Check\n`t`.\n"
QUOTE = 'Rui: "Merge each PR, use the cli | like it was me"'


class MergeSectionTest(unittest.TestCase):
    def setUp(self):
        self.kit = Kit()
        self.addCleanup(self.kit.close)
        self.kit.make_origin()
        self.kit.init_run()
        run = self.kit.run_dir
        (run / "tickets").mkdir()
        (run / "tasks").mkdir()
        self.ticket = run / "tickets" / "01-alpha.md"
        self.ticket.write_text(TICKET)
        self.task = run / "tasks" / "01-02-json-export.md"
        self.task.write_text(TASK)

    def decide(self, verb, *args):
        """What `decide.py <run> set|clause ...` records (its writing is tested in test_decisions.py), in-process."""
        d = decisions.load(self.kit.run_dir) or decisions.Decisions()
        if verb == "set":
            decisions.check_value(*args)
            d.headers[args[0]] = args[1]
        else:
            d.authorizations.append(decisions.Authorization(*args))
        decisions.save(self.kit.run_dir, d)

    def task_body(self):
        return prbody.task_body(self.kit.run_dir, "01-02", self.task, self.ticket)

    def ticket_body(self):
        return prbody.ticket_body(self.kit.run_dir, self.ticket, "## Testing\nRan it.")

    def merge(self, body):
        return prbody.section(body, "Merge")

    def human_run(self):
        self.decide("set", "merge_identity", "human")
        self.decide("set", "human", "rpvsilva")
        self.decide("set", "ci", "off")
        self.decide("clause", "Leave merges to the human", "yes", QUOTE)

    def test_human_merge_names_the_login_and_quotes_the_authorization(self):
        self.human_run()
        for body in (self.task_body(), self.ticket_body()):
            merge = self.merge(body)
            self.assertIn("`rpvsilva`", merge)
            self.assertIn("own login", merge)
            self.assertIn(f"Leave merges to the human (waived: yes): {QUOTE}", merge)

    def test_human_merge_with_no_recorded_human_names_no_login(self):
        self.decide("set", "merge_identity", "human")
        self.assertIn("Merges as the human's own login, with `GH_TOKEN` unset, into `feat/alpha`",
                      self.merge(self.task_body()))

    def test_task_pr_merges_into_its_ticket_branch_and_ticket_pr_into_the_base(self):
        self.human_run()
        self.assertIn("into `feat/alpha`", self.merge(self.task_body()))
        self.assertIn("into `main`", self.merge(self.ticket_body()))
        self.decide("set", "base", "feat/integration")
        self.assertIn("into `feat/integration`", self.merge(self.ticket_body()))

    def test_ticket_pr_proof_follows_ci_and_review_decisions(self):
        self.human_run()
        self.assertIn("a green local run (CI is off for this run)", self.merge(self.ticket_body()))
        self.decide("set", "ci", "on")
        self.decide("set", "review_before_agent_merge", "yes")
        merge = self.merge(self.ticket_body())
        self.assertIn("a review and green CI", merge)
        self.assertNotIn("local run", merge)

    def test_task_pr_proof_is_the_local_run_of_the_checks_whatever_ci_and_review_say(self):
        self.human_run()
        for ci, review in (("off", "no"), ("on", "yes"), ("on", "no")):
            self.decide("set", "ci", ci)
            self.decide("set", "review_before_agent_merge", review)
            merge = self.merge(self.task_body())
            self.assertIn("after a green local run of map.md's Checks.", merge)
            self.assertNotIn("green CI", merge)
            self.assertNotIn("a review", merge)

    def test_app_merge_says_the_app_merges(self):
        self.decide("set", "merge_identity", "app")
        merge = self.merge(self.task_body())
        self.assertIn("the App", merge)
        self.assertNotIn("own login", merge)
        self.assertIn("Authorizations: none recorded.", merge)

    def test_merge_section_sits_before_the_issue_refs(self):
        self.human_run()
        task, ticket = self.task_body(), self.ticket_body()
        self.assertTrue(task.rstrip().endswith("Part of #42"), task)
        self.assertTrue(ticket.rstrip().endswith("Closes #42"), ticket)
        self.assertLess(ticket.index("## Tasks"), ticket.index("## Merge"))

    def test_no_decisions_file_renders_the_body_without_the_section(self):
        self.assertFalse((self.kit.run_dir / "decisions.md").exists())
        (self.kit.run_dir / "run.md").unlink()
        for body in (self.task_body(), self.ticket_body()):
            self.assertNotIn("## Merge", body)
            self.assertIn("#42", body)


if __name__ == "__main__":
    unittest.main()
