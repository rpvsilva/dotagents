"""Tests for `merge_task.py`: the Checks, the merge guard and the local merge of a task into its ticket branch."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import runfolder  # noqa: E402
from testkit import SCRIPTS, Kit, env_tool_setup  # noqa: E402

MERGE_TASK = SCRIPTS / "merge_task.py"
REPO = SCRIPTS.parents[2]

TICKET = "# 01: Merge JSON things\nStatus: building\nIssue: #7\nBranch: feat/t\nBlocked by: none\n"
TASK = "# 01-{nn}: task {nn}\nStatus: {status}\nTicket: 01\nBlocked by: none\nWrites: x\n"


class MergeTaskTest(unittest.TestCase):
    def setUp(self):
        self.kit = kit = Kit()
        self.addCleanup(kit.close)
        kit.env.pop("XDG_CONFIG_HOME", None)
        clone = kit.make_origin()
        (kit.home / ".agents" / "bin").mkdir(parents=True)
        (kit.home / ".agents" / "bin" / "settings").symlink_to(REPO / "scripts" / "settings.sh")
        kit.branch_from_main("feat/t")
        kit.init_run()
        for d in ("tickets", "tasks", "worktrees"):
            (kit.run_dir / d).mkdir()
        self.ticket = kit.run_dir / "tickets" / "01-t.md"
        self.ticket.write_text(TICKET, encoding="utf-8")
        self.task = kit.run_dir / "tasks" / "01-01-a.md"
        self.task.write_text(TASK.format(nn="01", status="in-progress"), encoding="utf-8")
        self.checks("# a comment, not a suite", "true", "echo suite two")
        self.wt = kit.run_dir / "worktrees" / "01-01"
        kit.git("worktree", "add", "-q", "-b", "task/t/01", str(self.wt), "origin/feat/t", cwd=clone)
        self.commit(self.wt, "task.txt", "task\n", "feat: task one")

    # Helpers

    def checks(self, *commands):
        body = "\n".join(commands)
        (self.kit.run_dir / "map.md").write_text(f"# Map\n\n## Checks\n\nLocal:\n\n```\n{body}\n```\n\n## Gaps\n\n"
                                                 "```\nfalse\n```\n", encoding="utf-8")

    def commit(self, where, name, text, message):
        (Path(where) / name).write_text(text, encoding="utf-8")
        self.kit.git("add", name, cwd=where)
        self.kit.git("commit", "-qm", message, cwd=where)
        return self.kit.git("rev-parse", "HEAD", cwd=where)

    def ticket_tip(self):
        return self.kit.git("rev-parse", "refs/heads/feat/t", cwd=self.kit.clone)

    def advance_ticket(self, name, text):
        """A commit on the local ticket branch, as another task's merge would leave."""
        other = self.kit.tmp / "other-wt"
        self.kit.git("worktree", "add", "-q", "--detach", str(other), self.ticket_tip(), cwd=self.kit.clone)
        sha = self.commit(other, name, text, f"feat: {name}")
        self.kit.git("update-ref", "refs/heads/feat/t", sha, cwd=self.kit.clone)
        self.kit.git("worktree", "remove", "--force", str(other), cwd=self.kit.clone)
        return sha

    def merge(self, run=None):
        p = self.kit.step(MERGE_TASK, run or self.kit.run_dir, "01-01")
        self.assertEqual(p.stdout.count("\n"), 1, p.stdout + p.stderr)
        line = p.stdout.rstrip("\n")
        self.assertEqual(p.returncode, 0 if line.startswith("OK ") else 1, line)
        if line.startswith("FAIL "):
            self.assertRegex(line, r" \(log: .+\)$")
            self.assertTrue(Path(line.rsplit("(log: ", 1)[1][:-1]).is_file(), line)
        return line

    def origin_tip(self, branch):
        return self.kit.git("--git-dir", str(self.kit.origin), "rev-parse", branch)

    def assert_not_done(self):
        self.assertEqual(runfolder.get(self.task, "Status"), "in-progress")
        self.assertTrue(self.wt.is_dir())

    def log(self):
        return (self.kit.run_dir / "logs" / "01-01-merge_task.log").read_text(encoding="utf-8")

    # Green: a local merge commit, no GitHub call

    def test_green_merges_into_the_local_ticket_branch_with_no_github_call(self):
        start = self.origin_tip("feat/t")
        head = self.kit.git("rev-parse", "HEAD", cwd=self.wt)
        line = self.merge()
        self.assertRegex(line, r"^OK 01-01 done suites=2 \d+s commit=[0-9a-f]{7}$")
        tip = self.ticket_tip()
        self.assertEqual(self.kit.git("rev-parse", f"{tip}^1", cwd=self.kit.clone), start)
        self.assertEqual(self.kit.git("rev-parse", f"{tip}^2", cwd=self.kit.clone), head)
        self.assertEqual(self.kit.git("rev-parse", f"{tip}^{{tree}}", cwd=self.kit.clone),
                         self.kit.git("rev-parse", f"{head}^{{tree}}", cwd=self.kit.clone))
        self.assertEqual(self.kit.git("log", "-1", "--format=%s", tip, cwd=self.kit.clone),
                         "Merge task 01-01: task 01")
        self.assertTrue(line.endswith(f"commit={tip[:7]}"))
        self.assertEqual(self.kit.gh_calls(), [])
        self.assertEqual(self.origin_tip("feat/t"), start)
        self.assertEqual(self.kit.git("--git-dir", str(self.kit.origin), "branch", "--list", "task/t/01"), "")
        self.assertEqual(runfolder.get(self.task, "Status"), "done")
        self.assertEqual(runfolder.get(self.task, "Commit"), tip)
        self.assertRegex(runfolder.get(self.task, "Done"), r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertFalse(self.wt.exists())
        self.assertEqual(self.kit.git("branch", "--list", "task/t/01", cwd=self.kit.clone), "")
        self.assertIn("suite two", self.log())
        saved = json.loads((self.kit.run_dir / "reports" / "01-01.checks.json").read_text(encoding="utf-8"))
        self.assertEqual([(s["command"], s["rc"]) for s in saved], [("true", 0), ("echo suite two", 0)])

    def test_a_missing_local_ticket_branch_is_made_from_origins(self):
        self.assertNotEqual(self.kit.git("branch", "--list", "feat/t", cwd=self.kit.clone), "feat/t")
        start = self.origin_tip("feat/t")
        self.assertRegex(self.merge(), r"^OK 01-01 done ")
        self.assertEqual(self.kit.git("rev-parse", f"{self.ticket_tip()}^1", cwd=self.kit.clone), start)

    def test_a_shipped_ticket_gets_its_branch_pushed_so_its_pr_updates(self):
        runfolder.set_header(self.ticket, "PR", "https://github.com/o/r/pull/9")
        self.assertRegex(self.merge(), r"^OK 01-01 done ")
        self.assertEqual(self.origin_tip("feat/t"), self.ticket_tip())
        self.assertEqual(self.kit.gh_calls(), [])

    def test_a_rerun_after_the_merge_landed_does_not_merge_twice(self):
        self.assertRegex(self.merge(), r"^OK 01-01 done ")
        tip = self.ticket_tip()
        # As if the step had stopped after the local merge: the task still in progress on the head it merged.
        self.kit.git("worktree", "add", "-q", "-b", "task/t/01", str(self.wt), f"{tip}^2", cwd=self.kit.clone)
        runfolder.set_header(self.task, "Status", "in-progress")
        self.assertRegex(self.merge(), r"^OK 01-01 done ")
        self.assertEqual(self.ticket_tip(), tip)

    def test_checks_calling_a_tool_setup_installed_in_the_env_merge_green(self):
        (self.kit.run_dir / "map.md").write_text(
            "# Map\n\n## Checks\n\n```\nenvtool\n```\n\n" + env_tool_setup("envtool", "from the env") + "\n## Gaps\n",
            encoding="utf-8")
        p = self.kit.step(SCRIPTS / "setup.py", self.kit.run_dir)
        self.assertTrue(p.stdout.startswith("OK run set up "), p.stdout + p.stderr)
        self.assertRegex(self.merge(), r"^OK 01-01 done suites=1 ")
        self.assertIn("from the env", self.log())

    def test_works_through_a_linked_run_path(self):
        self.assertRegex(self.merge(self.kit.link(self.kit.run_dir, "run-link")), r"^OK 01-01 done ")

    # High-tier tasks (D7): reviewed before they merge

    def high_tier(self):
        self.task.write_text(TASK.format(nn="01", status="in-progress") + "Tier: high\n", encoding="utf-8")

    def test_a_high_tier_task_with_no_review_of_its_head_is_refused_before_checks(self):
        self.high_tier()
        self.checks("echo suite ran")
        line = self.merge()
        self.assertTrue(line.startswith("FAIL 01-01 unreviewed (log: "), line)
        self.assertNotIn("suite ran", self.log())
        self.assert_not_done()

    def test_a_high_tier_task_reviewed_at_its_head_merges(self):
        self.high_tier()
        head = self.kit.git("rev-parse", "HEAD", cwd=self.wt)
        (self.kit.run_dir / "reviews").mkdir()
        (self.kit.run_dir / "reviews" / f"01-01-{head[:7]}.json").write_text("{}\n", encoding="utf-8")
        self.assertRegex(self.merge(), r"^OK 01-01 done ")

    def test_a_review_of_an_older_head_does_not_count(self):
        self.high_tier()
        old = self.kit.git("rev-parse", "HEAD", cwd=self.wt)
        (self.kit.run_dir / "reviews").mkdir()
        (self.kit.run_dir / "reviews" / f"01-01-{old[:7]}.json").write_text("{}\n", encoding="utf-8")
        self.commit(self.wt, "fix.txt", "fix\n", "fix: after the review")
        self.assertTrue(self.merge().startswith("FAIL 01-01 unreviewed (log: "))

    # Refusals before any check or merge

    def assert_refused(self, reason):
        start = self.origin_tip("feat/t")
        line = self.merge()
        self.assertTrue(line.startswith(f"FAIL 01-01 {reason} (log: "), line)
        self.assertEqual(self.kit.gh_calls(), [])
        self.assertEqual(self.origin_tip("feat/t"), start)
        self.assertEqual(runfolder.get(self.task, "Status"), "in-progress")

    def test_uncommitted_changes_are_refused_and_kept(self):
        (self.wt / "task.txt").write_text("changed\n", encoding="utf-8")
        self.assert_refused("uncommitted-changes")
        self.assertEqual((self.wt / "task.txt").read_text(encoding="utf-8"), "changed\n")

    def test_an_untracked_file_is_not_an_uncommitted_change(self):
        (self.wt / "scratch.txt").write_text("x\n", encoding="utf-8")
        self.assertRegex(self.merge(), r"^OK 01-01 done ")

    def test_a_detached_head_is_refused(self):
        self.kit.git("checkout", "-q", "--detach", cwd=self.wt)
        self.assert_refused("detached-head")

    def test_a_missing_worktree_is_refused_naming_it(self):
        self.kit.git("worktree", "remove", "--force", str(self.wt), cwd=self.kit.clone)
        self.assert_refused(f"no-worktree: {self.wt}")

    def test_a_ticket_without_a_branch_is_refused(self):
        self.ticket.write_text(TICKET.replace("Branch: feat/t\n", ""), encoding="utf-8")
        self.assert_refused("ticket-no-branch")

    # Red checks

    def test_a_red_check_fails_naming_the_suite_and_nothing_merges(self):
        self.checks("true", "exit 3", "echo never")
        start = self.origin_tip("feat/t")
        line = self.merge()
        self.assertTrue(line.startswith("FAIL 01-01 red exit 3 (log: "), line)
        self.assertNotIn("never", self.log())
        self.assertEqual(self.ticket_tip(), start)
        self.assert_not_done()

    # The merge guard (behaviours 9, 10)

    def guard_on_main(self):
        """`AGENTS.md` on the default branch, and on the ticket branch forked from it, refuses process exits under
        `pkg/`."""
        clone = self.kit.clone
        self.commit(clone, "AGENTS.md", "# Rules\n\n```agent-merge-guard\nprocess-exit added \\bsys\\.exit\\( in pkg/**\n"
                                        "```\n", "docs: rules")
        self.kit.git("push", "-q", "origin", "main", "main:feat/t", cwd=clone)
        self.kit.git("fetch", "-q", "origin", cwd=clone)

    def guard_on_ticket(self):
        """The default branch's rules refuse process exits under `pkg/`; the task adds one."""
        self.guard_on_main()
        (self.wt / "pkg").mkdir()
        self.commit(self.wt, "pkg/run.py", "import sys\n\nsys.exit(1)\n", "feat: exit")

    def test_a_guarded_diff_is_refused_before_checks_and_nothing_merges(self):
        self.guard_on_ticket()
        self.checks("echo suite ran")
        line = self.merge()
        self.assertTrue(line.startswith("FAIL 01-01 guarded pkg/run.py:3 process-exit (log: "), line)
        self.assertNotIn("suite ran", self.log())
        self.assert_not_done()

    def test_a_task_deleting_the_rules_is_refused_until_waived(self):
        self.guard_on_main()
        self.kit.git("merge", "-q", "origin/feat/t", cwd=self.wt)
        self.commit(self.wt, "AGENTS.md", "# Rules\n", "docs: drop the rules")
        self.assertRegex(self.merge(), r"^FAIL 01-01 guarded AGENTS.md:1 merge-guard \(log: ")
        p = self.kit.step(SCRIPTS / "decide.py", self.kit.run_dir, "clause", "merge-guard", "yes", "Rui: fine")
        self.assertTrue(p.stdout.startswith("OK decisions clause waived=yes"), p.stdout + p.stderr)
        self.assertRegex(self.merge(), r"^OK 01-01 done ")
        self.assertNotIn("agent-merge-guard", self.kit.git("show", "feat/t:AGENTS.md", cwd=self.kit.clone))

    def test_an_unreadable_rule_on_the_default_branch_fails_bad_rule_before_checks(self):
        clone = self.kit.clone
        self.commit(clone, "AGENTS.md", "# Rules\n\n```agent-merge-guard\nprocess-exit added\n```\n", "docs: rules")
        self.kit.git("push", "-q", "origin", "main", cwd=clone)
        self.checks("echo suite ran")
        line = self.merge()
        self.assertTrue(line.startswith("FAIL 01-01 bad-rule: line 4: process-exit added (log: "), line)
        self.assertNotIn("suite ran", self.log())
        self.assert_not_done()

    def test_a_guarded_diff_whose_rule_is_waived_yes_merges(self):
        self.guard_on_ticket()
        p = self.kit.step(SCRIPTS / "decide.py", self.kit.run_dir, "clause", "process-exit", "yes", "Rui: fine here")
        self.assertTrue(p.stdout.startswith("OK decisions clause waived=yes"), p.stdout + p.stderr)
        self.assertRegex(self.merge(), r"^OK 01-01 done ")

    # Catching up with the ticket (bug 4)

    def test_a_branch_behind_the_ticket_merges_forward_keeping_its_resolution(self):
        self.merge_once_to_make_the_local_branch()
        later = self.advance_ticket("later.txt", "later\n")
        self.commit(self.wt, "README.md", "task side\n", "feat: task readme")
        head = self.kit.git("rev-parse", "HEAD", cwd=self.wt)
        self.assertRegex(self.merge(), r"^OK 01-01 done ")
        tip = self.ticket_tip()
        for ancestor in (later, head):
            self.kit.git("merge-base", "--is-ancestor", ancestor, tip, cwd=self.kit.clone)
        self.assertEqual(self.kit.git("show", f"{tip}:README.md", cwd=self.kit.clone), "task side")
        self.assertEqual(self.kit.git("rev-parse", f"{tip}^1", cwd=self.kit.clone), later)

    def merge_once_to_make_the_local_branch(self):
        """Makes the local ticket branch exist, at origin's tip, as an earlier merge in the run would."""
        self.kit.git("branch", "--no-track", "feat/t", "origin/feat/t", cwd=self.kit.clone)

    def test_a_conflict_with_the_ticket_fails_naming_the_files_and_leaves_the_branch(self):
        self.merge_once_to_make_the_local_branch()
        self.advance_ticket("README.md", "ticket side\n")
        head = self.commit(self.wt, "README.md", "task side\n", "feat: task readme")
        line = self.merge()
        self.assertTrue(line.startswith("FAIL 01-01 conflict README.md (log: "), line)
        self.assertEqual(self.kit.git("rev-parse", "HEAD", cwd=self.wt), head)
        self.assertEqual(self.kit.git("status", "--porcelain", cwd=self.wt), "")
        self.assert_not_done()

    def test_a_resolved_merge_at_the_head_is_kept_not_reset(self):
        self.merge_once_to_make_the_local_branch()
        self.advance_ticket("README.md", "ticket side\n")
        self.commit(self.wt, "README.md", "task side\n", "feat: task readme")
        subprocess.run(["git", "merge", "feat/t"], cwd=self.wt, env=self.kit.env, capture_output=True)
        (self.wt / "README.md").write_text("resolved\n", encoding="utf-8")
        self.kit.git("commit", "-qam", "Merge ticket, resolved", cwd=self.wt)
        resolved = self.kit.git("rev-parse", "HEAD", cwd=self.wt)
        self.assertRegex(self.merge(), r"^OK 01-01 done ")
        self.assertEqual(self.kit.git("rev-parse", f"{self.ticket_tip()}^2", cwd=self.kit.clone), resolved)


if __name__ == "__main__":
    unittest.main()
