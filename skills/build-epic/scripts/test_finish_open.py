"""Tests for `finish_task.py` and `steplib/prbody.py`."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import prbody, runfolder  # noqa: E402
from testkit import SCRIPTS, Kit, skip_without_zsh, zsh  # noqa: E402

FINISH = SCRIPTS / "finish_task.py"

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
        (run / "tickets" / "01-alpha.md").write_text(TICKET, encoding="utf-8")
        self.task = run / "tasks" / "01-02-json-export.md"
        self.task.write_text(TASK, encoding="utf-8")
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
        path.write_text(text, encoding="utf-8")

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
        first = runfolder.get(self.task, "Finished")
        self.assertRegex(first, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        runfolder.set_header(self.task, "Finished", "2000-01-01T00:00:00Z")  # a fix round keeps the first one
        self.write(self.wt, "a.txt", "fixed\n")
        self.assertEqual(self.one_line(self.kit.step(FINISH, self.kit.run_dir, "01-02")), "OK 01-02 committed files=3")
        self.assertEqual(runfolder.get(self.task, "Finished"), "2000-01-01T00:00:00Z")

    def test_merges_the_ticket_branch_tip(self):
        tip = self.advance_ticket("t.txt", "t\n")
        self.write(self.wt, "a.txt", "a\n")
        self.assertEqual(self.one_line(self.kit.step(FINISH, self.kit.run_dir, "01-02")), "OK 01-02 committed files=1")
        self.kit.git("merge-base", "--is-ancestor", tip, "HEAD", cwd=self.wt)

    def test_a_conflict_with_the_ticket_branch_names_its_files_and_a_rerun_commits_the_resolution(self):
        self.advance_ticket("a.txt", "theirs\n")
        self.write(self.wt, "a.txt", "ours\n")
        line = self.assert_fail(self.kit.step(FINISH, self.kit.run_dir, "01-02"), "FAIL 01-02 conflict a.txt (log: ")
        self.assertIn("<<<<<<<", (self.wt / "a.txt").read_text(encoding="utf-8"))
        self.assert_fail(self.kit.step(FINISH, self.kit.run_dir, "01-02"), "FAIL 01-02 conflict a.txt (log: ")
        (self.wt / "a.txt").write_text("both\n", encoding="utf-8")
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
        report.write_text("status: blocked\nchanged: none\nwhy: the API is missing\nfollow-up:\n", encoding="utf-8")
        p = self.kit.step(FINISH, "../run", "01-02", "--report", "../report.md", cwd=self.kit.home)
        line = self.assert_fail(p, "FAIL 01-02 blocked the API is missing (log: ")
        self.assertTrue(self.wt.exists(), line)
        self.assertEqual(runfolder.get(self.task, "Status"), "in-progress")
        self.assertEqual((self.kit.run_dir / "reports" / "01-02.md").read_text(encoding="utf-8"), report.read_text(encoding="utf-8"))

    def test_the_last_block_decides_when_a_report_holds_two(self):
        """A remote report is the agent's report file, ending with its block, then its final message: the block checked."""
        report = self.kit.tmp / "report.md"
        report.write_text("Built it.\nstatus: done\nchanged: a.txt\nwhy:\nfollow-up:\n\n"
                          "status: blocked\nchanged: none\nwhy: the API is missing\nfollow-up:\n", encoding="utf-8")
        p = self.kit.step(FINISH, "../run", "01-02", "--report", "../report.md", cwd=self.kit.home)
        self.assert_fail(p, "FAIL 01-02 blocked the API is missing (log: ")
        self.assertEqual(runfolder.get(self.task, "Status"), "in-progress")

    def test_an_unknown_flag_or_a_missing_report_fails_before_touching_the_task(self):
        self.write(self.wt, "a.txt", "a\n")
        for args, prefix in ((["--force", "x"], "FAIL 01-02 usage: finish_task.py "), (["--title"], "FAIL 01-02 usage: "),
                             (["--report", "nowhere.md"], f"FAIL 01-02 no-report: {self.kit.tmp / 'nowhere.md'} ")):
            self.assert_fail(self.kit.step(FINISH, self.kit.run_dir, "01-02", *args, cwd=self.kit.tmp), prefix)
        self.assertEqual(self.kit.git("status", "--porcelain", cwd=self.wt), "?? a.txt")
        self.assertEqual(runfolder.get(self.task, "Status"), "in-progress")

    def test_a_ticket_with_no_branch_fails_before_touching_the_task(self):
        (self.kit.run_dir / "tickets" / "01-alpha.md").write_text(TICKET.replace("Branch: feat/alpha\n", ""),
                                                                  encoding="utf-8")
        self.write(self.wt, "a.txt", "a\n")
        self.assert_fail(self.kit.step(FINISH, self.kit.run_dir, "01-02", cwd=self.kit.tmp), "FAIL 01-02 no-ticket-branch ")
        self.assertEqual(self.kit.git("status", "--porcelain", cwd=self.wt), "?? a.txt")
        self.assertEqual(runfolder.get(self.task, "Status"), "in-progress")


class PrBodyTest(StepCase):
    def test_ticket_body_lists_tasks_with_their_merge_commits_and_closes_the_issue(self):
        runfolder.set_header(self.task, "Commit", "abcdef0" + "1" * 33)
        (self.kit.run_dir / "tasks" / "01-03-csv.md").write_text("# 01-03: CSV import\nStatus: ready\n",
                                                                 encoding="utf-8")
        (self.kit.run_dir / "tasks" / "02-01-other.md").write_text("# 02-01: Other\nStatus: ready\n", encoding="utf-8")
        body = prbody.ticket_body(self.kit.run_dir, self.kit.run_dir / "tickets" / "01-alpha.md")
        self.assertIn("- 01-02 JSON export: merged in `abcdef0`\n- 01-03 CSV import: not merged", body)
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
