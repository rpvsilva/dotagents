"""Tests for `ship_ticket.py merge` into a non-default base: the integration ancestry and its two guards."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import runfolder  # noqa: E402
from test_ship import SHIP, TASK, TICKET, Base  # noqa: E402

BASE = "integ"


class AncestryTest(Base):
    """A ticket `feat/t` cut from `integ`, its task PRs squash-merged into it while `integ` moves on."""

    def setUp(self):
        super().setUp()
        kit = self.kit
        kit.branch_from_main(BASE)
        (kit.run_dir / "decisions.md").write_text(f"# Decisions\nbase: {BASE}\n", encoding="utf-8")  # the run's base, or 10-03's guard refuses
        (kit.answers / "pr-base").write_text(BASE, encoding="utf-8")
        runfolder.set_header(self.ticket, "PR", "https://github.com/o/r/pull/20")
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
        # The tuning task and the review fixes, squash-merged too, then the ticket PR's review is posted on its head.
        self.squash(self.commit("tune.txt", "fast\n", on="origin/feat/t"))
        self.squash(self.commit("fix.txt", "fixed\n", on="origin/feat/t"))
        self.reviewed_head = self.head()
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

    def push(self, sha, branch):
        self.git("push", "-q", "origin", f"{sha}:refs/heads/{branch}")  # moves origin/<branch> too: no fetch needed

    def squash(self, task):
        """What a squash merge of `task` into the ticket branch leaves: one commit with its tree, one parent."""
        sha = self.git("commit-tree", f"{task}^{{tree}}", "-p", "origin/feat/t", "-m", "squash")
        self.push(sha, "feat/t")
        return sha

    def review(self, sha):
        """The ticket PR's review as `post_review.py` keeps it: posted on `sha`."""
        (self.kit.run_dir / "logs" / "PR20-review.json").write_text(json.dumps({"commit_id": sha}), encoding="utf-8")

    def ship(self, *args):
        return self.one_line(self.kit.step(SHIP, self.kit.run_dir, "01", *args))

    def merge_calls(self):
        return self.calls("pr", "merge")

    def tree(self, ref):
        return self.git("rev-parse", f"{ref}^{{tree}}")

    def test_squashed_tasks_ship_into_a_moved_base_with_no_conflict_and_the_reviewed_tree(self):
        before = self.head()
        self.assertEqual(self.ship("merge"), "OK 01 merged pr=20")
        head = self.head()
        self.assertEqual(self.kit.git("--git-dir", str(self.kit.origin), "rev-parse", f"{head}^@").split(),
                         [before, self.base_tip])
        self.git("fetch", "-q", "origin")
        self.git("checkout", "-q", "--detach", f"origin/{BASE}")
        self.git("merge", "--no-edit", head)  # raises on a conflict
        self.assertEqual(self.tree("HEAD"), self.tree(self.reviewed_head))
        self.assertEqual(self.tree(head), self.tree(self.reviewed_head))
        self.assertEqual(runfolder.get(self.ticket, "Status"), "merged")

    def test_the_merge_is_pinned_to_the_pushed_ancestry_commit(self):
        self.assertEqual(self.ship("merge"), "OK 01 merged pr=20")
        self.assertEqual(self.merge_calls(),
                         [["pr", "merge", "20", "--squash", "--delete-branch", "--match-head-commit", self.head()]])

    def test_reviewed_names_another_commit_with_the_reviewed_tree(self):
        self.review("0" * 40)
        self.assertEqual(self.ship("merge", "--reviewed", self.reviewed_head), "OK 01 merged pr=20")
        self.assertEqual(self.tree(self.head()), self.tree(self.reviewed_head))

    def test_with_no_review_and_no_reviewed_head_nothing_merges(self):
        (self.kit.run_dir / "logs" / "PR20-review.json").unlink()
        before = self.head()
        self.assertRegex(self.ship("merge"), r"^FAIL 01 no-review \(log: ")
        self.assertEqual(self.merge_calls(), [])
        self.assertEqual(self.head(), before)

    def test_a_base_holding_content_the_reviewed_head_lacks_is_refused_and_nothing_merges(self):
        self.push(self.commit("e.txt", "late base\n", on=f"origin/{BASE}"), BASE)
        before = self.head()
        self.assertRegex(self.ship("merge"), r"^FAIL 01 reviewed-head-lacks-base \(log: ")
        self.assertEqual(self.merge_calls(), [])
        self.assertEqual(self.head(), before)
        self.assertEqual(runfolder.get(self.ticket, "Status"), "building")

    def test_a_ticket_tree_other_than_the_reviewed_tree_is_refused_and_nothing_merges(self):
        self.squash(self.commit("d.txt", "late\n", on="origin/feat/t"))
        before = self.head()
        self.assertRegex(self.ship("merge"), r"^FAIL 01 tree-mismatch \(log: ")
        self.assertEqual(self.merge_calls(), [])
        self.assertEqual(self.head(), before)
        self.assertEqual(runfolder.get(self.ticket, "Status"), "building")

    def test_a_reviewed_head_git_does_not_know_is_refused(self):
        self.assertRegex(self.ship("merge", "--reviewed", "0" * 40), r"^FAIL 01 reviewed-unknown \(log: ")
        self.assertEqual(self.merge_calls(), [])

    def test_a_pr_retargeted_to_another_branch_fails_wrong_base_and_pushes_nothing(self):
        self.kit.branch_from_main("release")
        self.push(self.commit("r.txt", "release\n", on="origin/release"), "release")
        (self.kit.answers / "pr-base").write_text("release", encoding="utf-8")
        before = self.head()
        self.assertRegex(self.ship("merge"), r"^FAIL 01 wrong-base release \(log: ")
        self.assertEqual(self.merge_calls(), [])
        self.assertEqual(self.head(), before)
        self.assertEqual(runfolder.get(self.ticket, "Status"), "building")

    def test_the_checks_run_on_the_ticket_head_before_the_merge(self):
        seen = self.kit.tmp / "checked"
        self.checks(f"git rev-parse HEAD^{{tree}} >'{seen}'")
        self.assertEqual(self.ship("merge"), "OK 01 merged pr=20")
        self.assertEqual(seen.read_text(encoding="utf-8").strip(), self.tree(self.reviewed_head))
        self.assertEqual(list((self.kit.run_dir / "worktrees").glob("*")), [])

    def test_a_red_check_fails_and_nothing_merges_or_pushes(self):
        self.checks("exit 3")
        before = self.head()
        self.assertRegex(self.ship("merge"), r"^FAIL 01 red exit 3 \(log: ")
        self.assertEqual(self.merge_calls(), [])
        self.assertEqual(self.head(), before)
        self.assertEqual(runfolder.get(self.ticket, "Status"), "building")

    def test_an_ancestry_push_origin_refuses_fails_head_moved_and_nothing_merges(self):
        hook = self.kit.origin / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\necho 'the ticket branch moved' >&2\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
        before = self.head()
        self.assertRegex(self.ship("merge"), r"^FAIL 01 head-moved \(log: ")
        self.assertEqual(len(self.calls("pr", "view")), 1, "the merge step ran: only its own head check refused")
        self.assertEqual(self.merge_calls(), [])
        self.assertEqual(self.head(), before)
        self.assertEqual(runfolder.get(self.ticket, "Status"), "building")

    def test_open_with_no_pr_yet_opens_the_ticket_pr_against_the_base(self):
        self.ticket.write_text(TICKET, encoding="utf-8")
        self.kit.answer("pr-list", out="[]")
        self.kit.answer("pr-create", out="https://github.com/o/r/pull/30\n")
        self.assertEqual(self.ship("open"), "OK 01 open pr=30")
        for call in (self.calls("pr", "list")[0], self.calls("pr", "create")[0]):
            self.assertEqual(call[call.index("--base") + 1], BASE, call)
        self.assertEqual(runfolder.get(self.ticket, "PR"), "https://github.com/o/r/pull/30")

    def test_shipping_again_after_the_ancestry_is_pushed_adds_no_second_one(self):
        (self.kit.answers / "pr-state").write_text("OPEN", encoding="utf-8")
        self.rule('echo "needs an approval from a code owner"; exit 1')
        self.assertEqual(self.ship("merge"), "OK 01 in-review pr=20")
        first = self.head()
        self.kit.answer("pr-list", out="[]")
        self.assertEqual(self.ship("open"), "OK 01 open pr=20")
        self.rule("exit 0")
        (self.kit.answers / "pr-state").write_text("MERGED", encoding="utf-8")
        self.assertEqual(self.ship("merge"), "OK 01 merged pr=20")
        self.assertEqual(self.head(), first)

    def test_with_merge_commits_no_ancestry_step_runs(self):
        self.set_setting("task_merge_method", "merge")
        before = self.head()
        self.assertEqual(self.ship("merge"), "OK 01 merged pr=20")
        self.assertEqual(self.head(), before)
        self.assertEqual(self.merge_calls(),
                         [["pr", "merge", "20", "--squash", "--delete-branch", "--match-head-commit", before]])

    def test_the_issues_stay_open_and_get_a_merged_to_base_label(self):
        self.assertEqual(self.ship("merge"), "OK 01 merged pr=20")
        self.assertEqual(self.calls("issue", "close"), [])
        self.assertEqual([c for c in self.calls("issue", "edit")],
                         [["issue", "edit", "7", "--add-label", f"merged-to-{BASE}"],
                          ["issue", "edit", "8", "--add-label", f"merged-to-{BASE}"]])

    def pull(self, n, sha):
        """GitHub's `refs/pull/<n>/head` on origin: kept after the task branch is deleted."""
        self.git("push", "-q", "-f", "origin", f"{sha}:refs/pull/{n}/head")

    def regroup(self):
        """The reported bug: tasks 1 and 2 (the hub, which merged the base) have their PRs' heads on origin, and a
        later squash-merged task 3 rewrites the lines the base added, so the 3-way merge from the ticket's fork point
        conflicts on them. The ticket PR is reviewed at that head."""
        self.pull(5, self.task1)
        self.pull(6, self.task2)
        (self.kit.run_dir / "tasks" / "01-03-c.md").write_text(TASK.format(nn="03", pr=9), encoding="utf-8")
        task3 = self.commit("b.txt", "b, regrouped\n", on="origin/feat/t")
        self.pull(9, task3)
        self.squash(task3)
        self.reviewed_head = self.head()
        self.review(self.reviewed_head)

    def test_a_task_rewriting_what_the_hub_merged_from_the_base_ships_with_the_reviewed_tree(self):
        self.regroup()
        before = self.head()
        self.assertEqual(self.ship("merge"), "OK 01 merged pr=20")
        head = self.head()
        self.assertEqual(self.kit.git("--git-dir", str(self.kit.origin), "rev-parse", f"{head}^@").split(),
                         [before, self.base_tip])
        self.assertEqual(self.tree(head), self.tree(self.reviewed_head))
        self.git("fetch", "-q", "origin")
        self.git("checkout", "-q", "--detach", f"origin/{BASE}")
        self.git("merge", "--no-edit", head)  # raises on a conflict
        self.assertEqual(self.tree("HEAD"), self.tree(self.reviewed_head))

    def test_a_base_moving_on_after_the_hub_task_is_still_refused(self):
        self.regroup()
        self.push(self.commit("e.txt", "late base\n", on=f"origin/{BASE}"), BASE)
        before = self.head()
        self.assertRegex(self.ship("merge"), r"^FAIL 01 reviewed-head-lacks-base \(log: ")
        self.assertEqual(self.merge_calls(), [])
        self.assertEqual(self.head(), before)

    def test_with_no_task_prs_recorded_the_guard_merges_from_the_fork_point_as_before(self):
        self.regroup()
        for path in (self.kit.run_dir / "tasks").glob("01-*.md"):
            path.write_text("".join(line for line in path.read_text(encoding="utf-8").splitlines(keepends=True)
                                    if not line.startswith("PR:")), encoding="utf-8")
        before = self.head()
        self.assertRegex(self.ship("merge"), r"^FAIL 01 reviewed-head-lacks-base \(log: ")
        self.assertEqual(self.head(), before)

    def test_a_task_pr_head_origin_lacks_falls_back_to_the_fork_point(self):
        self.regroup()
        self.kit.git("--git-dir", str(self.kit.origin), "update-ref", "-d", "refs/pull/9/head")
        self.assertRegex(self.ship("merge"), r"^FAIL 01 reviewed-head-lacks-base \(log: ")
        self.assertEqual(self.merge_calls(), [])

    def test_a_task_pr_head_the_ticket_never_merged_proves_nothing(self):
        self.regroup()
        late = self.commit("e.txt", "late base\n", on=f"origin/{BASE}")
        self.push(late, BASE)
        # Task 3's PR head claims the late base (the hub's head with it merged), but that tree never landed on the ticket.
        self.git("checkout", "-q", "--detach", self.task2)
        self.git("merge", "-q", "--no-edit", late)
        self.pull(9, self.git("rev-parse", "HEAD"))
        self.assertRegex(self.ship("merge"), r"^FAIL 01 reviewed-head-lacks-base \(log: ")
        self.assertEqual(self.merge_calls(), [])

    def checks(self, command):
        (self.kit.run_dir / "map.md").write_text(f"# Map\n\n## Checks\n\n```\n{command}\n```\n", encoding="utf-8")

    def rule(self, script):
        path = self.kit.tmp / "merge-rule.sh"
        path.write_text(f"#!/usr/bin/env bash\n{script}\n", encoding="utf-8")
        path.chmod(0o755)
        self.set_setting("merge_command", f"{path} {{pr}}")


if __name__ == "__main__":
    unittest.main()
