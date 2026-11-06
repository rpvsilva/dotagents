"""Tests for `finish_task.py`, `open_pr.py`, `push_fix.py` and `steplib/prbody.py`."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import prbody, runfolder  # noqa: E402
from testkit import SCRIPTS, Kit, skip_without_zsh, zsh  # noqa: E402

FINISH = SCRIPTS / "finish_task.py"
OPEN_PR = SCRIPTS / "open_pr.py"
PUSH_FIX = SCRIPTS / "push_fix.py"

TICKET = "# Alpha\nStatus: building\nBranch: feat/alpha\nIssue: #42\n"
TASK = """# 01-02: JSON export
Status: in-progress
Ticket: 01
Blocked by: none
Writes: a.txt, docs/

## What to build
Exports the list as JSON.

## Acceptance criteria
- [ ] it exports

## Check
`test_export.py` against the kit.
"""
URL = "https://github.com/o/r/pull/7"


class StepCase(unittest.TestCase):
    """A run with ticket 01 (`feat/alpha` on origin) and task 01-02 in its worktree on `task/alpha/02`."""

    def setUp(self):
        self.kit = Kit()
        self.addCleanup(self.kit.close)
        self.clone = self.kit.make_origin()
        self.kit.branch_from_main("feat/alpha")
        self.kit.init_run()
        run = self.kit.run_dir
        (run / "tickets").mkdir()
        (run / "tasks").mkdir()
        (run / "tickets" / "01-alpha.md").write_text(TICKET)
        self.task = run / "tasks" / "01-02-json-export.md"
        self.task.write_text(TASK)
        self.wt = run / "worktrees" / "01-02"
        self.kit.git("worktree", "add", "-q", "-b", "task/alpha/02", str(self.wt), "origin/feat/alpha", cwd=self.clone)

    def one_line(self, p):
        self.assertEqual(p.stdout.count("\n"), 1, p.stdout + p.stderr)
        return p.stdout.rstrip("\n")

    def assert_fail(self, p, prefix):
        self.assertEqual(p.returncode, 1, p.stdout)
        line = self.one_line(p)
        self.assertTrue(line.startswith(prefix), line)
        self.assertTrue(Path(line.rsplit("(log: ", 1)[1][:-1]).is_file(), line)
        return line

    def write(self, root, name, text):
        path = Path(root) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def advance_ticket(self, name, text, subject="ticket change"):
        """Adds a commit to `feat/alpha` on origin, from the main clone."""
        self.kit.git("checkout", "-q", "-B", "alpha-local", "origin/feat/alpha", cwd=self.clone)
        self.write(self.clone, name, text)
        self.kit.git("add", "-A", cwd=self.clone)
        self.kit.git("commit", "-qm", subject, cwd=self.clone)
        self.kit.git("push", "-q", "origin", "alpha-local:feat/alpha", cwd=self.clone)
        self.kit.git("checkout", "-q", "main", cwd=self.clone)
        return self.kit.git("rev-parse", "origin/feat/alpha", cwd=self.clone)

    def commit_in_wt(self, name, text, subject):
        self.write(self.wt, name, text)
        self.kit.git("add", "-A", cwd=self.wt)
        self.kit.git("commit", "-qm", subject, cwd=self.wt)

    def branch_exists(self, name):
        return bool(self.kit.git("branch", "--list", name, cwd=self.clone))


class FinishTaskTest(StepCase):
    def test_commits_remote_changes_and_appends_paths_outside_writes(self):
        self.write(self.wt, "a.txt", "a\n")
        self.write(self.wt, "docs/x.md", "x\n")
        self.write(self.wt, "b.txt", "b\n")
        p = self.kit.step(FINISH, self.kit.run_dir, "01-02")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertEqual(self.one_line(p), "OK 01-02 committed files=3")
        self.assertEqual(runfolder.get(self.task, "Writes"), "a.txt, docs/, b.txt")
        self.assertEqual(self.kit.git("status", "--porcelain", cwd=self.wt), "")
        self.assertEqual(self.kit.git("log", "-1", "--format=%s", cwd=self.wt), "JSON export")
        self.assertEqual(runfolder.get(self.task, "Status"), "in-progress")

    def test_merges_the_ticket_branch_tip(self):
        tip = self.advance_ticket("t.txt", "t\n")
        self.write(self.wt, "a.txt", "a\n")
        self.assertEqual(self.one_line(self.kit.step(FINISH, self.kit.run_dir, "01-02")), "OK 01-02 committed files=1")
        self.kit.git("merge-base", "--is-ancestor", tip, "HEAD", cwd=self.wt)

    def test_a_conflict_with_the_ticket_branch_names_its_files_and_a_rerun_commits_the_resolution(self):
        self.advance_ticket("a.txt", "theirs\n")
        self.write(self.wt, "a.txt", "ours\n")
        line = self.assert_fail(self.kit.step(FINISH, self.kit.run_dir, "01-02"), "FAIL 01-02 conflict a.txt (log: ")
        self.assertIn("<<<<<<<", (self.wt / "a.txt").read_text())
        self.assert_fail(self.kit.step(FINISH, self.kit.run_dir, "01-02"), "FAIL 01-02 conflict a.txt (log: ")
        (self.wt / "a.txt").write_text("both\n")
        p = self.kit.step(FINISH, self.kit.run_dir, "01-02")
        self.assertEqual(self.one_line(p), "OK 01-02 committed files=1", line)
        self.assertEqual(self.kit.git("status", "--porcelain", cwd=self.wt), "")

    def assert_no_change(self, p):
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.one_line(p), "OK 01-02 no-change")
        self.assertEqual(runfolder.get(self.task, "Status"), "done")
        self.assertFalse(self.wt.exists())
        self.assertFalse(self.branch_exists("task/alpha/02"))
        self.assertNotIn(str(self.wt), self.kit.git("worktree", "list", cwd=self.clone))

    def test_no_change_marks_the_task_done_and_removes_worktree_and_branch(self):
        self.assert_no_change(self.kit.step(FINISH, self.kit.run_dir, "01-02"))
        self.assertEqual(self.one_line(self.kit.step(FINISH, self.kit.run_dir, "01-02")), "OK 01-02 no-change")

    def test_no_change_under_bash_c_through_a_linked_run(self):
        """Bug 5: the no-change script failed with `source line number 1`."""
        linked = self.kit.link(self.kit.run_dir, "run-link")
        self.assert_no_change(self.kit.shell("bash", f"python3 -I '{FINISH}' '{linked}' 01-02"))

    @skip_without_zsh
    def test_no_change_under_zsh_c(self):
        self.assert_no_change(self.kit.shell(zsh, f"python3 -I '{FINISH}' '{self.kit.run_dir}' 01-02"))

    def test_a_blocked_report_keeps_the_work_and_never_marks_done(self):
        report = self.kit.tmp / "report.md"
        report.write_text("status: blocked\nchanged: none\nwhy: the API is missing\nfollow-up:\n")
        p = self.kit.step(FINISH, "../run", "01-02", "--report", "../report.md", cwd=self.kit.home)
        line = self.assert_fail(p, "FAIL 01-02 blocked the API is missing (log: ")
        self.assertTrue(self.wt.exists(), line)
        self.assertEqual(runfolder.get(self.task, "Status"), "in-progress")
        self.assertEqual((self.kit.run_dir / "reports" / "01-02.md").read_text(), report.read_text())


class OpenPrTest(StepCase):
    def finished(self, subject="JSON export"):
        self.commit_in_wt("a.txt", "a\n", subject)

    def test_pushes_opens_and_prints_the_url_once(self):
        """Bug 1: the PR URL was printed twice."""
        self.finished()
        self.kit.answer("pr-list", out="[]")
        self.kit.answer("pr-create", out=URL + "\n", err=f"Creating pull request...\n{URL}\n")
        p = self.kit.step(OPEN_PR, self.kit.run_dir, "01-02")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertEqual(p.stdout, f"OK 01-02 pr=7 {URL}\n")
        self.assertEqual(runfolder.get(self.task, "PR"), URL)
        head = self.kit.git("rev-parse", "HEAD", cwd=self.wt)
        self.assertEqual(self.kit.git("rev-parse", "refs/heads/task/alpha/02", cwd=self.kit.origin), head)
        create = [c for c in self.kit.gh_calls() if c[:2] == ["pr", "create"]]
        self.assertEqual(len(create), 1)
        args = dict(zip(create[0][2::2], create[0][3::2]))
        self.assertEqual((args["--base"], args["--head"], args["--title"]), ("feat/alpha", "task/alpha/02", "JSON export"))
        body = Path(args["--body-file"]).read_text()
        self.assertIn("Exports the list as JSON.", body)
        self.assertIn("Part of #42", body)

    def test_a_rerun_finds_the_open_pr_instead_of_creating_a_second(self):
        self.finished()
        self.kit.answer("pr-list", out=json.dumps([{"number": 7, "url": URL}]))
        self.assertEqual(self.one_line(self.kit.step(OPEN_PR, self.kit.run_dir, "01-02")), f"OK 01-02 pr=7 {URL}")
        self.assertEqual([c for c in self.kit.gh_calls() if c[:2] == ["pr", "create"]], [])
        list_call = next(c for c in self.kit.gh_calls() if c[:2] == ["pr", "list"])
        self.assertEqual(list_call[list_call.index("--head") + 1], "task/alpha/02")

    def test_a_create_that_failed_after_opening_the_pr_is_not_repeated(self):
        self.finished()
        self.kit.answer("pr-list", out="[]", n=1)
        self.kit.answer("pr-list", out=json.dumps([{"number": 7, "url": URL}]), n=2)
        self.kit.answer("pr-create", err="HTTP 502: Bad Gateway\n", rc=1)
        self.assertEqual(self.one_line(self.kit.step(OPEN_PR, self.kit.run_dir, "01-02")), f"OK 01-02 pr=7 {URL}")
        self.assertEqual(len([c for c in self.kit.gh_calls() if c[:2] == ["pr", "create"]]), 1)

    def test_a_4xx_on_create_fails_at_once(self):
        self.finished()
        self.kit.answer("pr-list", out="[]")
        self.kit.answer("pr-create", err="HTTP 422: Validation Failed\n", rc=1)
        self.assert_fail(self.kit.step(OPEN_PR, self.kit.run_dir, "01-02"), "FAIL 01-02 gh-pr-create (log: ")
        self.assertEqual(len([c for c in self.kit.gh_calls() if c[:2] == ["pr", "create"]]), 1)
        self.assertIsNone(runfolder.get(self.task, "PR"))

    def test_title_skips_a_merge_commit_that_is_the_oldest_and_keeps_an_acronym(self):
        """Bugs 11 and 2: a PR titled after a merge commit; `JSON` re-cased to `jSON`."""
        self.kit.git("checkout", "-q", "-b", "side", "origin/feat/alpha", cwd=self.clone)
        self.write(self.clone, "side.txt", "s\n")
        self.kit.git("add", "-A", cwd=self.clone)
        self.kit.git("commit", "-qm", "side commit", cwd=self.clone)
        self.kit.git("checkout", "-q", "main", cwd=self.clone)
        self.kit.git("merge", "-q", "--no-ff", "--no-edit", "side", cwd=self.wt)
        self.finished("JSON export")
        self.commit_in_wt("docs/x.md", "x\n", "later fix")
        self.assertTrue(self.kit.git("log", "--reverse", "--format=%s", "origin/feat/alpha..HEAD", cwd=self.wt)
                        .startswith("side commit\nMerge branch"))
        self.kit.answer("pr-list", out="[]")
        self.kit.answer("pr-create", out=URL + "\n")
        self.assertEqual(self.one_line(self.kit.step(OPEN_PR, self.kit.run_dir, "01-02")), f"OK 01-02 pr=7 {URL}")
        create = next(c for c in self.kit.gh_calls() if c[:2] == ["pr", "create"])
        self.assertEqual(create[create.index("--title") + 1], "JSON export")

    def test_a_title_given_with_title_names_the_pr_instead_of_the_oldest_commit(self):
        self.finished("test: speed up the export suite")
        self.kit.answer("pr-list", out="[]")
        self.kit.answer("pr-create", out=URL + "\n")
        p = self.kit.step(OPEN_PR, self.kit.run_dir, "01-02", "--title", "test: tune Alpha")
        self.assertEqual(self.one_line(p), f"OK 01-02 pr=7 {URL}")
        create = [c for c in self.kit.gh_calls() if c[:2] == ["pr", "create"]][0]
        self.assertEqual(create[create.index("--title") + 1], "test: tune Alpha")

    def test_an_empty_or_dangling_title_is_refused_before_pushing(self):
        self.finished()
        for args in (["--title"], ["--title", " "], ["--name", "x"]):
            with self.subTest(args):
                self.assert_fail(self.kit.step(OPEN_PR, self.kit.run_dir, "01-02", *args), "FAIL 01-02 usage: ")
        self.assertEqual(self.kit.gh_calls(), [])

    def test_uncommitted_changes_fail_before_pushing(self):
        self.finished()
        self.write(self.wt, "b.txt", "b\n")
        self.assert_fail(self.kit.step(OPEN_PR, self.kit.run_dir, "01-02"), "FAIL 01-02 uncommitted")
        self.assertEqual(self.kit.gh_calls(), [])

    def test_a_branch_with_no_commits_of_its_own_fails_before_pushing(self):
        self.assert_fail(self.kit.step(OPEN_PR, self.kit.run_dir, "01-02"), "FAIL 01-02 no-commits: ")
        self.assertEqual(self.kit.gh_calls(), [])
        self.assertEqual(self.kit.git("ls-remote", str(self.kit.origin), "refs/heads/task/alpha/02"), "")


class PushFixTest(StepCase):
    def test_merges_the_ticket_tip_then_pushes(self):
        self.commit_in_wt("a.txt", "a\n", "JSON export")
        tip = self.advance_ticket("t.txt", "t\n")
        p = self.kit.step(PUSH_FIX, self.kit.run_dir, "01-02")
        head = self.kit.git("rev-parse", "HEAD", cwd=self.wt)
        self.assertEqual(self.one_line(p), f"OK 01-02 pushed head={head[:7]}")
        self.kit.git("merge-base", "--is-ancestor", tip, head, cwd=self.wt)
        self.assertEqual(self.kit.git("rev-parse", "refs/heads/task/alpha/02", cwd=self.kit.origin), head)

    def test_pushes_without_a_merge_when_the_branch_has_the_tip(self):
        self.commit_in_wt("a.txt", "a\n", "JSON export")
        before = self.kit.git("rev-parse", "HEAD", cwd=self.wt)
        self.assertEqual(self.one_line(self.kit.step(PUSH_FIX, self.kit.run_dir, "01-02")),
                         f"OK 01-02 pushed head={before[:7]}")

    def test_a_conflict_names_its_files_and_pushes_nothing(self):
        self.commit_in_wt("a.txt", "ours\n", "JSON export")
        self.advance_ticket("a.txt", "theirs\n")
        self.assert_fail(self.kit.step(PUSH_FIX, self.kit.run_dir, "01-02"), "FAIL 01-02 conflict a.txt (log: ")
        self.assertEqual(self.kit.git("branch", "--list", "task/alpha/02", cwd=self.kit.origin), "")


class PrBodyTest(StepCase):
    def test_task_body_has_what_it_builds_its_check_result_and_part_of(self):
        reports = self.kit.run_dir / "reports"
        reports.mkdir()
        (reports / "01-02.checks.json").write_text(json.dumps([{"command": "tests/run.sh", "rc": 0, "seconds": 3}]))
        ticket = self.kit.run_dir / "tickets" / "01-alpha.md"
        body = prbody.task_body(self.kit.run_dir, "01-02", self.task, ticket)
        self.assertIn("Exports the list as JSON.", body)
        self.assertNotIn("it exports", body)
        self.assertIn("`test_export.py` against the kit.", body)
        self.assertIn("- `tests/run.sh`: passed in 3s", body)
        self.assertTrue(body.rstrip().endswith("Part of #42"), body)
        self.assertNotIn("Closes", body)

    def test_task_body_says_when_no_result_was_recorded(self):
        body = prbody.task_body(self.kit.run_dir, "01-02", self.task, self.kit.run_dir / "tickets" / "01-alpha.md")
        self.assertIn(prbody.NO_RESULT, body)

    def test_ticket_body_lists_tasks_with_pr_links_and_closes_the_issue(self):
        runfolder.set_header(self.task, "PR", URL)
        (self.kit.run_dir / "tasks" / "01-03-csv.md").write_text("# 01-03: CSV import\nStatus: ready\n")
        (self.kit.run_dir / "tasks" / "02-01-other.md").write_text("# 02-01: Other\nStatus: ready\n")
        body = prbody.ticket_body(self.kit.run_dir, self.kit.run_dir / "tickets" / "01-alpha.md")
        self.assertIn(f"- JSON export: {URL}\n- CSV import: no PR", body)
        self.assertNotIn("Other", body)
        self.assertTrue(body.rstrip().endswith("Closes #42"), body)
        self.assertNotIn("Part of", body)

    def test_issue_numbers_from_each_form(self):
        ticket = self.kit.run_dir / "tickets" / "01-alpha.md"
        for value, want in (("#1, #2", ["1", "2"]), ("gh-12 due dates", ["12"]),
                            ("https://github.com/o/r/issues/9", ["9"]), ("5", ["5"]), ("none", [])):
            runfolder.set_header(ticket, "Issue", value)
            self.assertEqual(prbody.issues(ticket), want, value)


if __name__ == "__main__":
    unittest.main()
