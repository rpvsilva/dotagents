"""Tests for `ship_ticket.py merge` into a non-default base: the integration ancestry and its two guards."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import runfolder  # noqa: E402
from test_ship import SHIP, Base  # noqa: E402

BASE = "integ"


class AncestryTest(Base):
    """A ticket `feat/t` cut from `integ`, its task PRs squash-merged into it while `integ` moves on."""

    def setUp(self):
        super().setUp()
        kit = self.kit
        kit.branch_from_main(BASE)
        (kit.answers / "pr-base").write_text(BASE)
        runfolder.set_header(self.ticket, "PR", "https://github.com/o/r/pull/20")
        kit.answer("pr-list", out="[]")
        self.assertEqual(self.ship("open"), "OK 01 open pr=20")
        self.work = kit.tmp / "work"
        kit.git("worktree", "add", "-q", "--detach", str(self.work), "origin/feat/t", cwd=kit.clone)
        # Task 1: a plain task, squash-merged.
        self.task1 = self.commit("a.txt", "a\n", on="origin/feat/t")
        self.squash(self.task1)
        # The base moves on.
        self.base_tip = self.commit("b.txt", "b\n", on=f"origin/{BASE}")
        self.push(self.base_tip, BASE)
        # Task 2: a hub task that merges the base, then squash-merged: the ticket has the base's tree, not its tip.
        self.git("checkout", "-q", "--detach", "origin/feat/t")
        self.git("merge", "-q", "--no-edit", f"origin/{BASE}")
        self.task2 = self.commit("c.txt", "c\n")
        self.squash(self.task2)

    def git(self, *args):
        return self.kit.git(*args, cwd=self.work)

    def commit(self, path, text, on=None):
        if on:
            self.git("checkout", "-q", "--detach", on)
        (self.work / path).write_text(text)
        self.git("add", path)
        self.git("commit", "-qm", f"add {path}")
        return self.git("rev-parse", "HEAD")

    def push(self, sha, branch):
        self.git("push", "-q", "origin", f"{sha}:refs/heads/{branch}")
        self.git("fetch", "-q", "origin")

    def squash(self, task):
        """What a squash merge of `task` into the ticket branch leaves: one commit with its tree, one parent."""
        sha = self.git("commit-tree", f"{task}^{{tree}}", "-p", "origin/feat/t", "-m", "squash")
        self.push(sha, "feat/t")
        return sha

    def ship(self, *args):
        return self.one_line(self.kit.step(SHIP, self.kit.run_dir, "01", *args))

    def merge_calls(self):
        return self.calls("pr", "merge")

    def tree(self, ref):
        return self.git("rev-parse", f"{ref}^{{tree}}")

    def test_squashed_tasks_ship_into_a_moved_base_with_no_conflict_and_the_reviewed_tree(self):
        before = self.head()
        self.assertEqual(self.ship("merge", "--reviewed", self.task2), "OK 01 merged pr=20")
        head = self.head()
        self.assertEqual(self.kit.git("--git-dir", str(self.kit.origin), "rev-parse", f"{head}^@").split(),
                         [before, self.base_tip])
        self.git("fetch", "-q", "origin")
        self.git("checkout", "-q", "--detach", f"origin/{BASE}")
        self.git("merge", "--no-edit", head)  # raises on a conflict
        self.assertEqual(self.tree("HEAD"), self.tree(self.task2))
        self.assertEqual(self.tree(head), self.tree(self.task2))
        self.assertEqual(runfolder.get(self.ticket, "Status"), "merged")

    def test_the_merge_is_pinned_to_the_pushed_ancestry_commit(self):
        self.assertEqual(self.ship("merge", "--reviewed", self.task2), "OK 01 merged pr=20")
        self.assertEqual(self.merge_calls(),
                         [["pr", "merge", "20", "--squash", "--delete-branch", "--match-head-commit", self.head()]])

    def test_the_reviewed_head_defaults_to_the_commit_the_posted_review_was_on(self):
        (self.kit.run_dir / "logs" / "PR20-review.json").write_text(json.dumps({"commit_id": self.task2}))
        self.assertEqual(self.ship("merge"), "OK 01 merged pr=20")
        self.assertEqual(self.tree(self.head()), self.tree(self.task2))

    def test_with_no_review_and_no_reviewed_head_nothing_merges(self):
        before = self.head()
        self.assertRegex(self.ship("merge"), r"^FAIL 01 no-review \(log: ")
        self.assertEqual(self.merge_calls(), [])
        self.assertEqual(self.head(), before)

    def test_a_reviewed_head_missing_the_base_tip_is_refused_and_nothing_merges(self):
        before = self.head()
        self.assertRegex(self.ship("merge", "--reviewed", self.task1), r"^FAIL 01 reviewed-head-lacks-base \(log: ")
        self.assertEqual(self.merge_calls(), [])
        self.assertEqual(self.head(), before)
        self.assertEqual(runfolder.get(self.ticket, "Status"), "building")

    def test_a_ticket_tree_other_than_the_reviewed_tree_is_refused_and_nothing_merges(self):
        self.squash(self.commit("d.txt", "late\n", on="origin/feat/t"))
        before = self.head()
        self.assertRegex(self.ship("merge", "--reviewed", self.task2), r"^FAIL 01 tree-mismatch \(log: ")
        self.assertEqual(self.merge_calls(), [])
        self.assertEqual(self.head(), before)
        self.assertEqual(runfolder.get(self.ticket, "Status"), "building")

    def test_a_reviewed_head_git_does_not_know_is_refused(self):
        self.assertRegex(self.ship("merge", "--reviewed", "0" * 40), r"^FAIL 01 reviewed-unknown \(log: ")
        self.assertEqual(self.merge_calls(), [])

    def test_shipping_again_after_the_ancestry_is_pushed_adds_no_second_one(self):
        (self.kit.answers / "pr-state").write_text("OPEN")
        self.rule('echo "needs an approval from a code owner"; exit 1')
        self.assertEqual(self.ship("merge", "--reviewed", self.task2), "OK 01 in-review pr=20")
        first = self.head()
        self.kit.answer("pr-list", out="[]")
        self.assertEqual(self.ship("open"), "OK 01 open pr=20")
        self.rule("exit 0")
        (self.kit.answers / "pr-state").write_text("MERGED")
        self.assertEqual(self.ship("merge", "--reviewed", self.task2), "OK 01 merged pr=20")
        self.assertEqual(self.head(), first)

    def test_with_merge_commits_no_ancestry_step_runs(self):
        self.set_setting("task_merge_method", "merge")
        before = self.head()
        self.assertEqual(self.ship("merge"), "OK 01 merged pr=20")
        self.assertEqual(self.head(), before)
        self.assertEqual(self.merge_calls(),
                         [["pr", "merge", "20", "--squash", "--delete-branch", "--match-head-commit", before]])

    def test_the_issues_stay_open_and_get_a_merged_to_base_label(self):
        self.assertEqual(self.ship("merge", "--reviewed", self.task2), "OK 01 merged pr=20")
        self.assertEqual(self.calls("issue", "close"), [])
        self.assertEqual([c for c in self.calls("issue", "edit")],
                         [["issue", "edit", "7", "--add-label", f"merged-to-{BASE}"],
                          ["issue", "edit", "8", "--add-label", f"merged-to-{BASE}"]])

    def rule(self, script):
        path = self.kit.tmp / "merge-rule.sh"
        path.write_text(f"#!/usr/bin/env bash\n{script}\n")
        path.chmod(0o755)
        self.set_setting("merge_command", f"{path} {{pr}}")


if __name__ == "__main__":
    unittest.main()
