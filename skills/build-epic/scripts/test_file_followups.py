"""Tests for `tracker_cmd.py <run> file-followups`: should-fix follow-ups filed as issues under the epic, against a stub
`gh`."""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tracker_cmd  # noqa: E402
from steplib import followups, runfolder  # noqa: E402
from testkit import SCRIPTS, Kit  # noqa: E402

TRACKER = SCRIPTS / "tracker_cmd.py"
EPIC = 4


def key(*args):
    """The stub `gh`'s answer key for a call starting with `args`."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", "-".join(args[:2]))


def issue_api(n):
    return key("api", f"repos/{{owner}}/{{repo}}/issues/{n}")


SUB_ISSUES = key("api", f"repos/{{owner}}/{{repo}}/issues/{EPIC}/sub_issues")


class FileFollowupsCase(unittest.TestCase):
    def setUp(self):
        self.kit = Kit()
        self.addCleanup(self.kit.close)
        self.kit.make_origin()
        self.kit.init_run()
        runfolder.set_header(self.kit.run_dir / "run.md", "Epic", f"#{EPIC}")
        self.kit.answer("api-user", out="agent-bot\n")

    def row(self, claim, severity="should-fix", ticket="01", pr=9, path="src/a.py", line=3, kind="follow-up"):
        followups.add(self.kit.run_dir, [{"ticket": ticket, "pr": pr, "severity": severity, "kind": kind,
                                          "file": path, "line": line, "claim": claim, "source": "r.md"}])

    def creates(self, n, issue, db_id=None):
        """The n-th `gh issue create` answers issue `issue`, whose database id is `db_id`."""
        self.kit.answer("issue-create", out=f"https://github.com/o/r/issues/{issue}\n", n=n)
        self.kit.answer(issue_api(issue), out=f"{db_id or issue * 1000}\n")

    def file(self):
        return self.kit.step(TRACKER, self.kit.run_dir, "file-followups")

    def line(self, p):
        self.assertEqual(p.stdout.count("\n"), 1, p.stdout + p.stderr)
        return p.stdout.rstrip("\n")

    def calls(self, *prefix):
        return [c for c in self.kit.gh_calls() if c[:len(prefix)] == list(prefix)]

    def filed(self):
        return {r["claim"]: r.get("issue") for r in followups.load(self.kit.run_dir)}


class FileFollowupsTest(FileFollowupsCase):
    def test_files_one_issue_per_should_fix_row_under_the_epic_and_records_its_number(self):
        self.row("the parser drops tabs")
        self.row("rename the flag", pr=None, path="docs/x.md", line=None, ticket="02")
        self.row("a nit", severity="nit")
        self.row("a blocker", severity="blocker")
        self.creates(1, 12)
        self.creates(2, 13)
        p = self.file()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.line(p), "OK followups filed 2 issues #12 #13")
        creates = self.calls("issue", "create")
        self.assertEqual(len(creates), 2)
        first = creates[0][creates[0].index("--body") + 1]
        for text in ("ticket 01", "PR #9", "`src/a.py:3`", "the parser drops tabs", f"Part of #{EPIC}"):
            self.assertIn(text, first)
        self.assertEqual(creates[0][creates[0].index("--title") + 1], "the parser drops tabs")
        second = creates[1][creates[1].index("--body") + 1]
        self.assertIn("ticket 02", second)
        self.assertIn("`docs/x.md`", second)
        self.assertNotIn("PR #", second)
        links = self.calls("api", f"repos/{{owner}}/{{repo}}/issues/{EPIC}/sub_issues")
        self.assertEqual([c[c.index("-F") + 1] for c in links], ["sub_issue_id=12000", "sub_issue_id=13000"])
        self.assertEqual(self.filed(), {"the parser drops tabs": 12, "rename the flag": 13, "a nit": None,
                                        "a blocker": None})

    def test_a_second_run_files_nothing(self):
        self.row("the parser drops tabs")
        self.creates(1, 12)
        self.assertEqual(self.file().returncode, 0)
        p = self.file()
        self.assertEqual(self.line(p), "OK followups filed 0 issues")
        self.assertEqual(len(self.calls("issue", "create")), 1)

    def test_a_refused_sub_issue_link_still_records_the_issue_and_prints_its_number(self):
        self.row("the parser drops tabs")
        self.row("rename the flag")
        self.creates(1, 12)
        self.creates(2, 13)
        self.kit.answer(SUB_ISSUES, err="HTTP 403: Resource not accessible by integration\n", rc=1, n=2)
        p = self.file()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.line(p), "OK followups filed 2 issues #12 #13 not-linked #13")
        self.assertEqual(self.filed(), {"the parser drops tabs": 12, "rename the flag": 13})
        body = self.calls("issue", "create")[1]
        self.assertIn(f"Part of #{EPIC}", body[body.index("--body") + 1])

    def test_a_failed_create_files_nothing_for_that_row(self):
        self.row("the parser drops tabs")
        self.kit.answer("issue-create", err="HTTP 422: Validation Failed\n", rc=1)
        p = self.file()
        self.assertEqual(p.returncode, 1)
        self.assertTrue(self.line(p).startswith("FAIL followups gh-issue-create (log: "), p.stdout)
        self.assertEqual(self.filed(), {"the parser drops tabs": None})


class IdentityTest(FileFollowupsCase):
    def test_an_identity_change_holds_the_filing_and_its_replay_files_once(self):
        self.row("the parser drops tabs")
        self.creates(1, 12)
        self.assertEqual(self.line(self.file()), "OK followups filed 1 issues #12")
        self.row("rename the flag")
        self.creates(2, 13)
        self.kit.answer("api-user", out="someone-else\n", n=2)
        p = self.file()
        self.assertEqual(p.returncode, 1)
        self.assertTrue(self.line(p).startswith("FAIL followups identity-changed (log: "), p.stdout)
        self.assertEqual(len(self.calls("issue", "create")), 1)
        self.assertEqual(self.filed(), {"the parser drops tabs": 12, "rename the flag": None})
        p = self.file()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.line(p), "OK followups filed 1 issues #13")
        self.assertEqual(len(self.calls("issue", "create")), 2)
        self.assertEqual(self.filed(), {"the parser drops tabs": 12, "rename the flag": 13})
        self.assertEqual(self.line(self.file()), "OK followups filed 0 issues")
        self.assertEqual(len(self.calls("issue", "create")), 2)

    def test_a_flush_files_the_held_one_and_the_next_run_only_records_it(self):
        self.row("the parser drops tabs")
        self.creates(1, 12)
        self.assertEqual(self.file().returncode, 0)  # records agent-bot as the run's identity
        self.row("rename the flag")
        self.creates(2, 13)
        self.kit.answer("api-user", out="someone-else\n", n=2)
        self.assertEqual(self.file().returncode, 1)
        self.assertEqual(self.line(self.kit.step(TRACKER, self.kit.run_dir, "flush")), "OK tracker flushed 1 held")
        self.assertEqual(len(self.calls("issue", "create")), 2)
        p = self.file()
        self.assertEqual(self.line(p), "OK followups filed 1 issues #13")
        self.assertEqual(len(self.calls("issue", "create")), 2)
        self.assertEqual(self.filed(), {"the parser drops tabs": 12, "rename the flag": 13})


class RefusalTest(FileFollowupsCase):
    def test_another_tracker_is_left_to_the_orchestrator(self):
        tool = self.kit.home / ".agents" / "bin" / "settings"
        tool.parent.mkdir(parents=True, exist_ok=True)
        tool.write_text('#!/bin/sh\ncase "$5" in\n  tracker) echo "linear" ;;\nesac\n', encoding="utf-8")
        tool.chmod(0o755)
        self.row("the parser drops tabs")
        p = self.file()
        self.assertEqual(self.line(p), "OK followups manual tracker=linear")
        self.assertEqual(self.kit.gh_calls(), [])
        self.assertEqual(self.filed(), {"the parser drops tabs": None})

    def test_without_an_epic_it_is_refused(self):
        runfolder.set_header(self.kit.run_dir / "run.md", "Epic", "none")
        self.row("the parser drops tabs")
        self.assertRegex(self.line(self.file()), r"^FAIL followups no-epic: ")
        self.assertEqual(self.calls("issue", "create"), [])

    def test_extra_arguments_are_refused(self):
        self.assertRegex(self.line(self.kit.step(TRACKER, self.kit.run_dir, "file-followups", "x")),
                         r"^FAIL followups usage: .*file-followups")

    def test_usage_and_docstring_name_file_followups(self):
        p = self.kit.step(TRACKER, self.kit.run_dir, "nope")
        self.assertEqual(p.returncode, 2)
        self.assertIn("file-followups", self.line(p))
        self.assertIn("file-followups", tracker_cmd.USAGE)
        self.assertIn("- `file-followups`", tracker_cmd.__doc__)


if __name__ == "__main__":
    unittest.main()
