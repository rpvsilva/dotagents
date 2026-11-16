"""Tests for `ship_ticket.py merge` into a base other than the default branch: the Checks on the ticket head, the
reviewed-tree guard, and tasks landed as merge commits keeping every base merge a hub task made."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import runfolder  # noqa: E402
from test_ship import SHIP, Base  # noqa: E402

BASE = "integ"


class ShipIntoTest(Base):
    """A ticket `feat/t` cut from `integ`; two tasks merged into its local branch while `integ` moves on, the second a
    hub task that merged the base first; the ticket PR reviewed at that head."""

    def setUp(self):
        super().setUp()
        kit = self.kit
        kit.branch_from_main(BASE)
        (kit.run_dir / "decisions.md").write_text(f"# Decisions\nbase: {BASE}\n", encoding="utf-8")
        (kit.answers / "pr-base").write_text(BASE, encoding="utf-8")
        runfolder.set_header(self.ticket, "PR", "https://github.com/o/r/pull/20")
        kit.git("branch", "--no-track", "feat/t", "origin/feat/t", cwd=kit.clone)
        self.work = kit.tmp / "work"
        kit.git("worktree", "add", "-q", "--detach", str(self.work), "feat/t", cwd=kit.clone)
        self.land(self.commit("a.txt", "a\n", on="feat/t"))
        self.base_tip = self.commit("b.txt", "b\n", on=f"origin/{BASE}")
        self.git("push", "-q", "origin", f"{self.base_tip}:refs/heads/{BASE}")
        self.git("checkout", "-q", "--detach", "feat/t")
        self.git("merge", "-q", "--no-edit", f"origin/{BASE}")
        self.land(self.commit("c.txt", "c\n"))
        self.reviewed_head = self.local_tip()
        self.review(self.reviewed_head)

    def git(self, *args):
        return self.kit.git(*args, cwd=self.work)

    def commit(self, path, text, on=None):
        if on:
            self.git("checkout", "-q", "--detach", on)
        (self.work / path).write_text(text, encoding="utf-8")
        self.git("add", path)
        self.git("commit", "-qm", f"add {path}")
        return self.git("rev-parse", "HEAD")

    def local_tip(self):
        return self.git("rev-parse", "refs/heads/feat/t")

    def land(self, task):
        """What `merge_task.py` leaves: a merge commit of `task` on the local ticket branch, with the task's tree."""
        sha = self.git("commit-tree", f"{task}^{{tree}}", "-p", "feat/t", "-p", task, "-m", "Merge task")
        self.git("update-ref", "refs/heads/feat/t", sha)
        return sha

    def review(self, sha):
        """The ticket PR's review as `post_review.py` keeps it: posted on `sha`."""
        (self.kit.run_dir / "logs").mkdir(exist_ok=True)
        (self.kit.run_dir / "logs" / "PR20-review.json").write_text(json.dumps({"commit_id": sha}), encoding="utf-8")

    def ship(self, *args):
        return self.one_line(self.kit.step(SHIP, self.kit.run_dir, "01", "merge", *args))

    def assert_nothing_shipped(self, before):
        self.assertEqual(self.calls("pr", "merge"), [])
        self.assertEqual(self.head(), before)

    def test_ships_the_local_head_with_its_base_merges_and_no_extra_commit(self):
        self.assertEqual(self.ship(), "OK 01 merged pr=20")
        self.assertEqual(self.head(), self.reviewed_head)
        self.assertEqual(self.calls("pr", "merge")[0][-1], self.reviewed_head)
        self.kit.git("merge-base", "--is-ancestor", self.base_tip, self.head(), cwd=self.kit.clone)
        self.assertEqual(runfolder.get(self.ticket, "Status"), "merged")

    def test_the_issues_stay_open_and_get_a_merged_to_base_label(self):
        self.assertEqual(self.ship(), "OK 01 merged pr=20")
        self.assertEqual(self.calls("issue", "close"), [])
        self.assertTrue(any(f"merged-to-{BASE}" in c for c in self.calls("issue", "edit")), self.kit.gh_calls())

    def test_a_red_check_on_the_ticket_head_merges_and_pushes_nothing(self):
        before = self.head()
        (self.kit.run_dir / "map.md").write_text("# Map\n\n## Checks\n\n```\nexit 4\n```\n", encoding="utf-8")
        self.assertRegex(self.ship(), r"^FAIL 01 red exit 4 \(log: ")
        self.assert_nothing_shipped(before)

    def test_a_merge_after_the_review_is_refused_and_nothing_ships(self):
        before = self.head()
        self.land(self.commit("late.txt", "late\n", on="feat/t"))
        self.assertRegex(self.ship(), r"^FAIL 01 tree-mismatch \(log: ")
        self.assert_nothing_shipped(before)

    def test_reviewed_names_another_commit_with_the_reviewed_tree(self):
        (self.kit.run_dir / "logs" / "PR20-review.json").unlink()
        before = self.head()
        self.assertRegex(self.ship(), r"^FAIL 01 no-review \(log: ")
        self.assert_nothing_shipped(before)
        self.assertEqual(self.ship("--reviewed", self.reviewed_head), "OK 01 merged pr=20")

    def test_a_reviewed_head_git_does_not_know_is_refused(self):
        self.review("0" * 40)
        self.assertRegex(self.ship(), r"^FAIL 01 reviewed-unknown \(log: ")

    def test_a_pr_retargeted_to_another_branch_fails_wrong_base_and_pushes_nothing(self):
        before = self.head()
        (self.kit.answers / "pr-base").write_text("main", encoding="utf-8")
        self.assertRegex(self.ship(), r"^FAIL 01 wrong-base main \(log: ")
        self.assert_nothing_shipped(before)


if __name__ == "__main__":
    unittest.main()
