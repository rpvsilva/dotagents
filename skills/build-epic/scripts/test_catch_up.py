"""Tests for `catch_up.py` and the `merged-to-<base>` label `ship_ticket.py merge` leaves, against a stub `gh`."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import runfolder  # noqa: E402
from test_ship import SHIP, Base  # noqa: E402
from testkit import SCRIPTS, Kit  # noqa: E402

CATCH_UP = SCRIPTS / "catch_up.py"
START_TICKET = SCRIPTS / "start_ticket.py"
NEW = "int/x"


class MergedToLabelTest(Base):
    """A ticket PR into the integration branch `int/x`, merged with merge commits (no ancestry step)."""

    def test_a_ticket_merged_into_a_non_default_base_leaves_its_issues_open_and_labelled(self):
        kit = self.kit
        kit.branch_from_main(NEW)
        (kit.run_dir / "decisions.md").write_text(f"# Decisions\nbase: {NEW}\n")
        (kit.answers / "pr-base").write_text(NEW)
        runfolder.set_header(self.ticket, "PR", "https://github.com/o/r/pull/20")
        self.set_setting("task_merge_method", "merge")
        p = kit.step(SHIP, kit.run_dir, "01", "merge")
        self.assertEqual(self.one_line(p), "OK 01 merged pr=20")
        self.assertEqual(self.calls("issue", "close"), [])
        self.assertEqual(self.calls("issue", "edit"),
                         [["issue", "edit", "7", "--add-label", f"merged-to-{NEW}"],
                          ["issue", "edit", "8", "--add-label", f"merged-to-{NEW}"]])


class CatchUpCase(unittest.TestCase):
    """Tickets 01 (building, behind `int/x`), 02 and 03 (ready, never pushed), 04 (merged), 05 (building, already
    holding `int/x`) and 06 (ready, its branch pushed by a start cut short); `int/x` is `main` plus one commit."""

    def setUp(self):
        self.kit = kit = Kit()
        self.addCleanup(kit.close)
        kit.make_origin()
        kit.init_run()
        (kit.run_dir / "tickets").mkdir()
        self.ticket("01", "building", "feat/a")
        self.ticket("02", "ready", "feat/b")
        self.ticket("03", "ready", "feat/c")
        self.ticket("04", "merged", "feat/d")
        self.ticket("05", "building", "feat/e")
        self.ticket("06", "ready", "feat/f")
        kit.branch_from_main("feat/a")
        kit.branch_from_main("feat/f")
        self.commit("feat/a", "a.txt", "a\n")
        self.new_tip = self.commit(NEW, "x.txt", "x\n", start="main")
        kit.git("push", "-q", "origin", f"{self.new_tip}:refs/heads/feat/e", cwd=kit.clone)
        kit.git("fetch", "-q", "origin", cwd=kit.clone)

    def ticket(self, tt, status, branch, extra=""):
        path = self.kit.run_dir / "tickets" / f"{tt}-t.md"
        path.write_text(f"# {tt}: T\nStatus: {status}\nIssue: none\nBranch: {branch}\n{extra}\nBody.\n")
        return path

    def commit(self, branch, path, text, start=None):
        """Commits `path` on origin's `branch` (or on `start` for a new branch) through a scratch worktree."""
        kit, work = self.kit, self.kit.tmp / "scratch"
        kit.git("worktree", "add", "-q", "--detach", str(work), f"origin/{start or branch}", cwd=kit.clone)
        (work / path).write_text(text)
        kit.git("add", path, cwd=work)
        kit.git("commit", "-qm", f"add {path}", cwd=work)
        kit.git("push", "-q", "origin", f"HEAD:refs/heads/{branch}", cwd=work)
        sha = kit.git("rev-parse", "HEAD", cwd=work)
        kit.git("worktree", "remove", "--force", str(work), cwd=kit.clone)
        kit.git("fetch", "-q", "origin", cwd=kit.clone)
        return sha

    def remote(self, ref):
        return self.kit.git("--git-dir", str(self.kit.origin), "rev-parse", ref)

    def refs(self):
        return self.kit.git("--git-dir", str(self.kit.origin), "for-each-ref")

    def catch_up(self, *args):
        p = self.kit.step(CATCH_UP, self.kit.run_dir, *args)
        self.assertEqual(p.stdout.count("\n"), 1, p.stdout + p.stderr)
        line = p.stdout.rstrip("\n")
        self.assertEqual(p.returncode, 0 if line.startswith("OK ") else 1, line)
        return line


class CatchUpTest(CatchUpCase):
    def test_without_apply_it_lists_the_tickets_and_changes_nothing(self):
        refs = self.refs()
        line = self.catch_up(NEW)
        self.assertTrue(line.startswith("OK run catch-up 2 unstarted 1 started"), line)
        self.assertIn("unstarted=02,03 started=01", line)
        self.assertFalse((self.kit.run_dir / "decisions.md").exists())
        self.assertEqual(self.refs(), refs)
        self.assertEqual(self.kit.gh_calls(), [])

    def test_with_apply_it_records_the_base_and_start_ticket_cuts_from_it(self):
        line = self.catch_up(NEW, "--apply")
        self.assertTrue(line.startswith(f"OK run caught-up base={NEW} 2 unstarted 1 started"), line)
        self.assertIn(f"base: {NEW}\n", (self.kit.run_dir / "decisions.md").read_text())
        p = self.kit.step(START_TICKET, self.kit.run_dir, "02")
        self.assertEqual(p.stdout.strip(), f"OK 02 building branch=feat/b base={self.new_tip[:7]}", p.stdout)
        self.assertEqual(self.remote("feat/b"), self.new_tip)

    def test_with_apply_a_started_ticket_branch_gets_the_new_base_merged_in(self):
        before = self.remote("feat/a")
        self.catch_up(NEW, "--apply")
        tip = self.remote("feat/a")
        self.assertEqual(self.kit.git("rev-parse", f"{tip}^1", cwd=self.kit.clone), before)
        self.assertEqual(self.kit.git("rev-parse", f"{tip}^2", cwd=self.kit.clone), self.new_tip)
        self.assertEqual(self.remote("feat/e"), self.new_tip)
        self.assertEqual(self.catch_up(NEW), "OK run catch-up 2 unstarted 0 started unstarted=02,03 started=none")

    def test_with_apply_a_started_tickets_open_pr_moves_to_the_new_base(self):
        self.ticket("01", "building", "feat/a", "PR: https://github.com/o/r/pull/20\n")
        self.catch_up(NEW, "--apply")
        self.assertEqual(self.kit.gh_calls(), [["pr", "edit", "20", "--base", NEW]])

    def test_a_conflict_fails_naming_the_ticket_and_files_and_leaves_its_branch(self):
        self.commit("feat/a", "x.txt", "other\n")
        before = self.remote("feat/a")
        line = self.catch_up(NEW, "--apply")
        self.assertRegex(line, r"^FAIL run conflict 01: x\.txt \(log: ")
        self.assertEqual(self.remote("feat/a"), before)
        self.assertIn(f"base: {NEW}\n", (self.kit.run_dir / "decisions.md").read_text())
        self.assertEqual(list((self.kit.run_dir / "worktrees").glob("*")), [])

    def test_a_base_missing_on_origin_is_refused_and_nothing_changes(self):
        line = self.catch_up("int/none", "--apply")
        self.assertRegex(line, r"^FAIL run no-base int/none \(log: ")
        self.assertFalse((self.kit.run_dir / "decisions.md").exists())

    def test_a_bad_argument_is_a_usage_error(self):
        self.assertRegex(self.catch_up(NEW, "--force"), r"^FAIL run usage: ")


if __name__ == "__main__":
    unittest.main()
