"""Tests for the identity check before every merge (`steplib/identity.py`, `steplib/merge.py`), through
`merge_task.py` against a stub `gh`."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_merge_task  # noqa: E402
from steplib import core, identity, merge, runfolder  # noqa: E402
from testkit import Kit  # noqa: E402


class IdentityTest(test_merge_task.MergeTaskTest):
    """The merge_task sandbox; the base class's own tests run from test_merge_task.py only (see the end)."""

    def setUp(self):
        super().setUp()
        self.kit.env["GH_TOKEN"] = "outer-token"

    def decide(self, mode, human="rui", setting=None):
        """`decisions.md` recording `mode` (and `human:`); the `merge_identity` Setting is `setting`, else `mode`."""
        text = f"# Decisions\nmerge_identity: {mode}\n" + (f"human: {human}\n" if human else "")
        (self.kit.run_dir / "decisions.md").write_text(text, encoding="utf-8")
        if setting or mode == "human":
            self.setting("merge_identity", setting or mode)

    def merge_tokens(self):
        """The `GH_TOKEN` each `gh pr merge` call ran with."""
        return self.tokens("pr", "merge")

    def tokens(self, *prefix):
        """The `GH_TOKEN` each `gh <prefix...>` call ran with. The `pr view` wrapper logs calls but no tokens, so
        tokens pair with the other calls only."""
        stub_calls = [c for c in self.kit.gh_calls() if c[:2] != ["pr", "view"]]
        return [t for c, t in zip(stub_calls, self.kit.gh_tokens()) if c[:len(prefix)] == list(prefix)]

    def assert_wrong_identity(self, line):
        self.assertTrue(line.startswith("FAIL 01-01 wrong-identity (log: "), line)
        self.assertEqual(self.calls("pr", "merge"), [])
        self.assertEqual(runfolder.get(self.task, "Status"), "in-progress")

    def assert_no_auth_token(self):
        self.assertEqual([c for c in self.kit.gh_calls() if c[:2] == ["auth", "token"]], [])

    # human

    def test_human_checks_the_login_and_merges_without_gh_token_when_it_is_the_recorded_human(self):
        self.decide("human")
        self.kit.answer("api-user", out="rui\n")
        self.assertRegex(self.merge(), r"^OK 01-01 done ")
        self.assertIn(["api", "user", "--jq", ".login"], self.kit.gh_calls())
        self.assertEqual(self.tokens("api", "user"), [""])
        self.assertEqual(self.merge_tokens(), [""])
        self.assert_no_auth_token()

    def test_human_refuses_a_login_other_than_the_recorded_human(self):
        self.decide("human")
        self.kit.answer("api-user", out="rpvsilva-agent[bot]\n")
        self.assert_wrong_identity(self.merge())
        self.assert_no_auth_token()

    def test_human_with_no_recorded_human_merges_as_the_gh_login(self):
        self.decide("human", human=None)
        self.kit.answer("api-user", out="rui\n")
        self.assertRegex(self.merge(), r"^OK 01-01 done ")
        self.assertEqual(self.merge_tokens(), [""])

    def test_human_compares_the_login_case_insensitively(self):
        self.decide("human", human="Rui")
        self.kit.answer("api-user", out="rUI\n")
        self.assertRegex(self.merge(), r"^OK 01-01 done ")

    def test_human_matches_the_recorded_human_in_a_decisions_file_with_crlf_line_ends(self):
        # A text file written on Windows ends its lines with \r\n.
        (self.kit.run_dir / "decisions.md").write_bytes(b"# Decisions\r\nmerge_identity: human\r\nhuman: rui\r\n")
        self.setting("merge_identity", "human")
        self.kit.answer("api-user", out="rui\n")
        self.assertRegex(self.merge(), r"^OK 01-01 done ")

    def test_human_refuses_a_bot_login(self):
        self.decide("human", human=None)
        self.kit.answer("api-user", out="rpvsilva-agent[bot]\n")
        self.assert_wrong_identity(self.merge())

    # decisions.md only records the Setting

    def test_decisions_saying_human_while_the_setting_is_unset_merges_nothing_as_the_human(self):
        (self.kit.run_dir / "decisions.md").write_text("# Decisions\nmerge_identity: human\nhuman: rui\n",
                                                       encoding="utf-8")
        self.kit.answer("api-user", out="rui\n")
        self.kit.answer("api-installation_repositories", out='{"total_count": 1}\n')
        self.assert_wrong_identity(self.merge())
        self.assertEqual(self.tokens("api", "user"), [])

    def test_decisions_saying_human_while_the_setting_is_app_merges_nothing_as_the_human(self):
        self.decide("human", setting="app")
        self.kit.answer("api-user", out="rui\n")
        self.kit.answer("api-installation_repositories", out='{"total_count": 1}\n')
        self.assert_wrong_identity(self.merge())
        self.assertEqual(self.tokens("api", "user"), [])

    def test_decisions_saying_app_while_the_setting_is_human_is_refused(self):
        self.decide("app", setting="human")
        self.kit.answer("api-user", out="rui\n")
        self.kit.answer("api-installation_repositories", out='{"total_count": 1}\n')
        self.assert_wrong_identity(self.merge())

    def test_human_with_a_failing_login_call_is_refused(self):
        self.decide("human")
        self.kit.answer("api-user", err="gh: not logged in\n", rc=4)
        self.assert_wrong_identity(self.merge())

    # app

    def test_app_merges_when_the_token_answers_installation_repositories(self):
        self.decide("app")
        self.kit.answer("api-installation_repositories", out='{"total_count": 1, "repositories": []}\n')
        self.assertRegex(self.merge(), r"^OK 01-01 done ")
        self.assertIn(["api", "installation/repositories"], self.kit.gh_calls())
        self.assert_no_auth_token()

    def test_app_refuses_a_token_that_is_not_an_installation_token(self):
        self.decide("app")
        self.kit.answer("api-installation_repositories", err="HTTP 403: Resource not accessible by integration\n",
                        rc=1)
        self.assert_wrong_identity(self.merge())
        self.assert_no_auth_token()

    def test_without_a_recorded_identity_the_setting_decides_and_defaults_to_app(self):
        (self.kit.run_dir / "decisions.md").unlink(missing_ok=True)
        self.kit.answer("api-installation_repositories", rc=1, err="HTTP 401: Bad credentials\n")
        self.assert_wrong_identity(self.merge())
        self.setting("merge_identity", "human")
        self.kit.answer("api-user", out="rui\n")
        self.assertRegex(self.merge(), r"^OK 01-01 done ")
        self.assertEqual(self.calls("api", "user")[-1:], [["api", "user", "--jq", ".login"]])


class ModeTest(unittest.TestCase):
    """`identity.mode` on its own: which identity a run merges as, before any `gh` call."""

    def setUp(self):
        self.kit = Kit()
        self.addCleanup(self.kit.close)
        self.file = self.kit.run_dir / "decisions.md"

    def test_with_nothing_recorded_or_set_it_is_the_app(self):
        self.assertEqual(identity.mode(self.kit.run_dir, lambda: None), "app")
        self.file.write_text("# Decisions\nmerge_identity:\n", encoding="utf-8")
        self.assertEqual(identity.mode(self.kit.run_dir, lambda: "human"), "human")

    def test_any_other_setting_fails_bad_value(self):
        with self.assertRaises(core.Fail) as caught:
            identity.mode(self.kit.run_dir, lambda: "bot")
        self.assertEqual(caught.exception.reason, "bad-value: merge_identity is one of app|human")
        self.file.write_text("# Decisions\nmerge_identity: rui\n", encoding="utf-8")  # decisions.md never decides the mode
        self.assertEqual(identity.mode(self.kit.run_dir, lambda: "app"), "app")


class MergeArgvTest(unittest.TestCase):
    """`merge.merge_argv`: the argv the merge rule runs as, for the identity proven above."""

    def test_no_rule_is_gh_pr_merge_pinned_to_the_head(self):
        self.assertEqual(merge.merge_argv(None, "5", "abc"),
                         ["gh", "pr", "merge", "5", "--squash", "--delete-branch", "--match-head-commit", "abc"])

    def test_a_rule_gets_its_placeholders_filled_and_keeps_its_quoting(self):
        self.assertEqual(merge.merge_argv("'scripts/merge pr.sh' {pr} --head={sha}", "5", "abc"),
                         ["scripts/merge pr.sh", "5", "--head=abc"])
        self.assertEqual(merge.merge_argv("merge --sha {sha}", "5", "abc"), ["merge", "--sha", "abc"])

    def test_a_rule_naming_neither_placeholder_gets_the_pr_appended(self):
        self.assertEqual(merge.merge_argv("scripts/merge-pr.sh --yes", "5", "abc"),
                         ["scripts/merge-pr.sh", "--yes", "5"])


for _name in [n for n in dir(test_merge_task.MergeTaskTest) if n.startswith("test_")]:
    setattr(IdentityTest, _name, None)

if __name__ == "__main__":
    unittest.main()
