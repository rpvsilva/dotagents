"""Tests for `merge_task.py`: Checks, the merge rule, Ticket CI and the tuning task, against a stub `gh`."""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import runfolder  # noqa: E402
from testkit import SCRIPTS, Kit  # noqa: E402

MERGE_TASK = SCRIPTS / "merge_task.py"
REPO = SCRIPTS.parents[2]

# `gh pr view` answers like GitHub: the PR's head is the task branch's tip on origin (or `answers/pr-head`), its state
# `answers/pr-state` (default MERGED). Every other call goes to the testkit stub.
PR_VIEW = r'''#!{python} -IS
import os, sys
d = os.environ["GH_STUB_DIR"]
args = sys.argv[1:]
if args[:2] != ["pr", "view"]:
    os.execv({kit_gh!r}, [{kit_gh!r}, *args])
import json, subprocess
with open(os.path.join(d, "gh.log"), "a") as f:
    f.write(json.dumps(args) + "\n")
def read(name, default):
    p = os.path.join(d, "answers", name)
    return open(p).read().strip() if os.path.exists(p) else default
head = read("pr-head", "") or subprocess.run(["git", "--git-dir", {origin!r}, "rev-parse", "task/t/01"],
                                             capture_output=True, text=True).stdout.strip()
print(json.dumps({{"state": read("pr-state", "MERGED"), "headRefOid": head}}))
'''

TICKET = "# 01: Merge JSON things\nStatus: building\nIssue: #7\nBranch: feat/t\nBlocked by: none\n"
TASK = "# 01-{nn}: task {nn}\nStatus: {status}\nTicket: 01\nBlocked by: none\nWrites: x\n"


class MergeTaskTest(unittest.TestCase):
    def setUp(self):
        self.kit = kit = Kit()
        self.addCleanup(kit.close)
        kit.env.pop("XDG_CONFIG_HOME", None)
        clone = kit.make_origin()
        (kit.bin / "gh").rename(kit.bin / "gh-kit")
        (kit.bin / "gh").write_text(PR_VIEW.format(python=sys.executable, kit_gh=str(kit.bin / "gh-kit"),
                                                   origin=str(kit.origin)))
        (kit.bin / "gh").chmod(0o755)
        (kit.home / ".agents" / "bin").mkdir(parents=True)
        (kit.home / ".agents" / "bin" / "settings").symlink_to(REPO / "scripts" / "settings.sh")
        kit.branch_from_main("feat/t")
        kit.init_run()
        for d in ("tickets", "tasks", "worktrees"):
            (kit.run_dir / d).mkdir()
        self.ticket = kit.run_dir / "tickets" / "01-t.md"
        self.ticket.write_text(TICKET)
        self.task = kit.run_dir / "tasks" / "01-01-a.md"
        self.task.write_text(TASK.format(nn="01", status="in-progress") + "PR: https://github.com/o/r/pull/5\n")
        self.checks("# a comment, not a suite", "true", "echo suite two")
        kit.answer("pr-list", out="[]")
        kit.answer("pr-create", out="https://github.com/o/r/pull/9\n")
        self.wt = kit.run_dir / "worktrees" / "01-01"
        kit.git("worktree", "add", "-q", "-b", "task/t/01", str(self.wt), "origin/feat/t", cwd=clone)
        self.commit(self.wt, "task.txt", "task\n", "feat: task one")

    # Helpers

    def checks(self, *commands):
        body = "\n".join(commands)
        (self.kit.run_dir / "map.md").write_text(f"# Map\n\n## Checks\n\nLocal:\n\n```\n{body}\n```\n\n## Gaps\n\n"
                                                 "```\nfalse\n```\n")

    def commit(self, where, name, text, message):
        (Path(where) / name).write_text(text)
        self.kit.git("add", name, cwd=where)
        self.kit.git("commit", "-qm", message, cwd=where)
        return self.kit.git("rev-parse", "HEAD", cwd=where)

    def push_to_ticket(self, name, text):
        """A commit on origin's ticket branch, as another task's merge would leave."""
        other = self.kit.tmp / "other"
        if not other.exists():
            self.kit.git("clone", "-q", "-b", "feat/t", str(self.kit.origin), str(other))
        self.kit.git("pull", "-q", "origin", "feat/t", cwd=other)
        sha = self.commit(other, name, text, f"feat: {name}")
        self.kit.git("push", "-q", "origin", "HEAD:feat/t", cwd=other)
        return sha

    def setting(self, key, value):
        p = subprocess.run([str(self.kit.home / ".agents/bin/settings"), "set", "build-epic", key, value, "--machine"],
                           env=self.kit.env, text=True, capture_output=True)
        self.assertEqual(p.returncode, 0, p.stderr)

    def merge(self, run=None):
        p = self.kit.step(MERGE_TASK, run or self.kit.run_dir, "01-01")
        self.assertEqual(p.stdout.count("\n"), 1, p.stdout + p.stderr)
        line = p.stdout.rstrip("\n")
        self.assertEqual(p.returncode, 0 if line.startswith("OK ") else 1, line)
        if line.startswith("FAIL "):
            self.assertRegex(line, r" \(log: .+\)$")
            self.assertTrue(Path(line.rsplit("(log: ", 1)[1][:-1]).is_file(), line)
        return line

    def calls(self, *prefix):
        return [c for c in self.kit.gh_calls() if c[:len(prefix)] == list(prefix)]

    def origin_tip(self, branch):
        return self.kit.git("--git-dir", str(self.kit.origin), "rev-parse", branch)

    def assert_not_done(self):
        self.assertEqual(runfolder.get(self.task, "Status"), "in-progress")
        self.assertTrue(self.wt.is_dir())

    # Green

    def test_green_merges_sets_done_and_removes_worktree_and_branch(self):
        head = self.kit.git("rev-parse", "HEAD", cwd=self.wt)
        line = self.merge()
        self.assertRegex(line, r"^OK 01-01 done suites=2 \d+s$")
        self.assertEqual(runfolder.get(self.task, "Status"), "done")
        self.assertFalse(self.wt.exists())
        self.assertEqual(self.kit.git("branch", "--list", "task/t/01", cwd=self.kit.clone), "")
        self.assertEqual(self.origin_tip("task/t/01"), head)
        self.assertEqual(self.calls("pr", "merge"),
                         [["pr", "merge", "5", "--squash", "--delete-branch", "--match-head-commit", head]])
        log = (self.kit.run_dir / "logs" / "01-01-merge_task.log").read_text()
        self.assertIn("suite two", log)
        self.assertIn("suite 'true': rc 0", log)
        saved = json.loads((self.kit.run_dir / "reports" / "01-01.checks.json").read_text())
        self.assertEqual([(s["command"], s["rc"]) for s in saved], [("true", 0), ("echo suite two", 0)])

    def test_works_through_a_linked_run_path(self):
        self.assertRegex(self.merge(self.kit.link(self.kit.run_dir, "run-link")), r"^OK 01-01 done ")

    # Refusals before any check, push or merge

    def assert_refused(self, reason):
        line = self.merge()
        self.assertTrue(line.startswith(f"FAIL 01-01 {reason} (log: "), line)
        self.assertEqual(self.kit.gh_calls(), [])
        self.assertEqual(self.kit.git("--git-dir", str(self.kit.origin), "branch", "--list", "task/t/01"), "")
        self.assertEqual(runfolder.get(self.task, "Status"), "in-progress")

    def test_uncommitted_changes_are_refused_and_kept(self):
        (self.wt / "task.txt").write_text("changed\n")
        self.assert_refused("uncommitted-changes")
        self.assertEqual((self.wt / "task.txt").read_text(), "changed\n")

    def test_an_untracked_file_is_not_an_uncommitted_change(self):
        (self.wt / "scratch.txt").write_text("x\n")
        self.assertRegex(self.merge(), r"^OK 01-01 done ")

    def test_a_detached_head_is_refused(self):
        self.kit.git("checkout", "-q", "--detach", cwd=self.wt)
        self.assert_refused("detached-head")

    def test_a_missing_worktree_is_refused_naming_it(self):
        self.kit.git("worktree", "remove", "--force", str(self.wt), cwd=self.kit.clone)
        self.assert_refused(f"no-worktree: {self.wt}")

    def test_a_ticket_without_a_branch_is_refused(self):
        self.ticket.write_text(TICKET.replace("Branch: feat/t\n", ""))
        self.assert_refused("ticket-no-branch")

    def test_a_task_without_a_pr_is_refused(self):
        self.task.write_text(TASK.format(nn="01", status="in-progress"))
        self.assert_refused("no-pr")

    def test_a_missing_or_failing_settings_tool_is_refused(self):
        tool = self.kit.home / ".agents" / "bin" / "settings"
        tool.unlink()
        self.assert_refused(f"no-settings: {tool} is missing; run install.sh")
        tool.write_text("#!/bin/sh\necho 'settings: bad value' >&2\nexit 1\n")
        tool.chmod(0o755)
        self.assert_refused("setting-merge_command")

    # Red checks

    def test_a_red_check_fails_naming_the_suite_and_nothing_merges(self):
        self.checks("true", "exit 3", "echo never")
        line = self.merge()
        self.assertTrue(line.startswith("FAIL 01-01 red exit 3 (log: "), line)
        self.assertNotIn("never", (self.kit.run_dir / "logs" / "01-01-merge_task.log").read_text())
        self.assertEqual(self.calls("pr", "merge"), [])
        self.assert_not_done()

    # Merge failures (bug 10)

    def test_a_failed_gh_merge_is_a_fail_merge(self):
        self.kit.answer("pr-merge", err="GraphQL: Pull request is not mergeable\n", rc=1)
        (self.kit.answers / "pr-state").write_text("OPEN")
        self.assertTrue(self.merge().startswith("FAIL 01-01 merge (log: "))
        self.assert_not_done()

    def test_a_merge_that_exits_0_but_reads_back_open_is_a_fail_merge(self):
        (self.kit.answers / "pr-state").write_text("OPEN")
        self.assertTrue(self.merge().startswith("FAIL 01-01 merge (log: "))
        self.assert_not_done()

    def test_a_merge_command_that_refuses_is_a_fail_merge(self):
        rule = self.kit.tmp / "refuse.sh"
        rule.write_text("#!/bin/sh\necho 'gate: refused' >&2\nexit 1\n")
        rule.chmod(0o755)
        self.setting("merge_command", f"{rule} {{pr}}")
        (self.kit.answers / "pr-state").write_text("OPEN")
        self.assertTrue(self.merge().startswith("FAIL 01-01 merge (log: "))
        self.assertIn("gate: refused", (self.kit.run_dir / "logs" / "01-01-merge_task.log").read_text())
        self.assert_not_done()

    def test_merged_after_a_nonzero_exit_counts_as_merged(self):
        # gh exits 1 when it merged but could not delete a branch still checked out in a worktree.
        self.kit.answer("pr-merge", err="failed to delete local branch task/t/01\n", rc=1)
        self.assertRegex(self.merge(), r"^OK 01-01 done ")

    def test_a_head_that_moved_fails_head_moved_and_nothing_merges(self):
        (self.kit.answers / "pr-head").write_text("0" * 40)
        self.assertTrue(self.merge().startswith("FAIL 01-01 head-moved (log: "))
        self.assertEqual(self.calls("pr", "merge"), [])
        self.assert_not_done()

    def test_a_match_head_commit_refusal_fails_head_moved(self):
        self.kit.answer("pr-merge", err="GraphQL: Head branch was modified. Review and try the merge again.\n", rc=1)
        (self.kit.answers / "pr-state").write_text("OPEN")
        self.assertTrue(self.merge().startswith("FAIL 01-01 head-moved (log: "))

    # The merge_command Setting

    def test_merge_command_setting_runs_instead_of_gh_pr_merge(self):
        rule = self.kit.tmp / "merge-rule.sh"
        rule.write_text(f"#!/bin/sh\npwd > {self.kit.tmp}/rule.cwd\necho \"$@\" > {self.kit.tmp}/rule.args\n")
        rule.chmod(0o755)
        self.setting("merge_command", f"{rule} {{pr}} --head {{sha}}")
        head = self.kit.git("rev-parse", "HEAD", cwd=self.wt)
        self.assertRegex(self.merge(), r"^OK 01-01 done ")
        self.assertEqual((self.kit.tmp / "rule.args").read_text(), f"5 --head {head}\n")
        self.assertEqual((self.kit.tmp / "rule.cwd").read_text().strip(), str(self.kit.clone))
        self.assertEqual(self.calls("pr", "merge"), [])

    # Catching up with the ticket (bug 4)

    def test_a_resolved_merge_at_the_head_is_kept_not_reset(self):
        self.push_to_ticket("README.md", "ticket side\n")
        self.commit(self.wt, "README.md", "task side\n", "feat: task readme")
        self.kit.git("fetch", "-q", "origin", cwd=self.wt)
        subprocess.run(["git", "merge", "origin/feat/t"], cwd=self.wt, env=self.kit.env, capture_output=True)
        (self.wt / "README.md").write_text("resolved\n")
        self.kit.git("commit", "-qam", "Merge ticket, resolved", cwd=self.wt)
        resolved = self.kit.git("rev-parse", "HEAD", cwd=self.wt)
        self.assertRegex(self.merge(), r"^OK 01-01 done ")
        self.assertEqual(self.origin_tip("task/t/01"), resolved)
        self.assertEqual(self.calls("pr", "merge")[0][-1], resolved)

    def test_a_branch_behind_the_ticket_merges_forward_keeping_its_resolution(self):
        self.push_to_ticket("README.md", "ticket side\n")
        self.commit(self.wt, "README.md", "task side\n", "feat: task readme")
        self.kit.git("fetch", "-q", "origin", cwd=self.wt)
        subprocess.run(["git", "merge", "origin/feat/t"], cwd=self.wt, env=self.kit.env, capture_output=True)
        (self.wt / "README.md").write_text("resolved\n")
        self.kit.git("commit", "-qam", "Merge ticket, resolved", cwd=self.wt)
        resolved = self.kit.git("rev-parse", "HEAD", cwd=self.wt)
        later = self.push_to_ticket("later.txt", "later\n")
        self.assertRegex(self.merge(), r"^OK 01-01 done ")
        pushed = self.origin_tip("task/t/01")
        for ancestor in (resolved, later):
            self.kit.git("merge-base", "--is-ancestor", ancestor, pushed, cwd=self.kit.clone)
        self.assertEqual(self.kit.git("show", f"{pushed}:README.md", cwd=self.kit.clone), "resolved")
        self.assertEqual(self.calls("pr", "merge")[0][-1], pushed)

    def test_a_conflict_with_the_ticket_fails_naming_the_files_and_leaves_the_branch(self):
        self.push_to_ticket("README.md", "ticket side\n")
        head = self.commit(self.wt, "README.md", "task side\n", "feat: task readme")
        line = self.merge()
        self.assertTrue(line.startswith("FAIL 01-01 conflict README.md (log: "), line)
        self.assertEqual(self.kit.git("rev-parse", "HEAD", cwd=self.wt), head)
        self.assertEqual(self.kit.git("status", "--porcelain", cwd=self.wt), "")
        self.assert_not_done()

    # Ticket CI

    def test_first_merge_opens_the_draft_ticket_pr_and_records_it(self):
        self.assertRegex(self.merge(), r"^OK 01-01 done ")
        self.assertEqual(self.calls("pr", "create"), [[
            "pr", "create", "--draft", "--base", "main", "--head", "feat/t",
            "--title", "feat: Merge JSON things", "--body", "Part of #7"]])
        self.assertEqual(runfolder.get(self.ticket, "PR"), "https://github.com/o/r/pull/9")
        self.assertEqual(self.calls("pr", "checks"), [])

    def test_an_open_ticket_pr_is_recorded_not_duplicated(self):
        self.kit.answer("pr-list", out='[{"number": 4, "url": "https://github.com/o/r/pull/4"}]')
        self.merge()
        self.assertEqual(self.calls("pr", "create"), [])
        self.assertEqual(runfolder.get(self.ticket, "PR"), "https://github.com/o/r/pull/4")

    def test_later_merge_waits_on_pending_ticket_checks_first(self):
        runfolder.set_header(self.ticket, "PR", "https://github.com/o/r/pull/9")
        self.kit.answer("pr-checks", out='[{"name": "ci", "bucket": "pending", "link": ""}]', rc=8, n=1)
        self.kit.answer("pr-checks", out='[{"name": "ci", "bucket": "pass", "link": ""},'
                                         '{"name": "old", "bucket": "cancel", "link": ""}]')
        self.assertRegex(self.merge(), r"^OK 01-01 done ")
        order = [c[:2] for c in self.kit.gh_calls() if c[:2] in (["pr", "checks"], ["pr", "merge"])]
        self.assertEqual(order, [["pr", "checks"], ["pr", "checks"], ["pr", "merge"]])
        self.assertEqual(self.calls("pr", "create"), [])

    def test_a_red_ticket_check_fails_with_its_run_id_once(self):
        runfolder.set_header(self.ticket, "PR", "https://github.com/o/r/pull/9")
        self.kit.answer("pr-checks", out='[{"name": "ci", "bucket": "fail", '
                                         '"link": "https://github.com/o/r/actions/runs/4242/job/7"}]', rc=1)
        line = self.merge()
        self.assertTrue(line.startswith("FAIL 01-01 ticket-ci-red 4242 (log: "), line)
        self.assertEqual(self.calls("run", "view"), [["run", "view", "4242", "--log-failed"]])
        self.assertEqual(self.calls("pr", "merge"), [])
        self.assert_not_done()
        # Reported once: the orchestrator hands it to a later task, and the next merge goes ahead.
        self.assertRegex(self.merge(), r"^OK 01-01 done ")

    def test_ci_off_in_decisions_skips_ticket_ci(self):
        (self.kit.run_dir / "decisions.md").write_text("# Decisions\nci: off\n")
        self.assertRegex(self.merge(), r"^OK 01-01 done ")
        self.assertEqual(self.calls("pr", "create") + self.calls("pr", "list"), [])

    # The tuning task

    def test_last_task_with_a_suite_over_the_minimum_writes_the_tuning_task(self):
        self.setting("tune_min_seconds", "0")
        main = self.kit.git("rev-parse", "origin/main", cwd=self.kit.clone)
        tip = self.push_to_ticket("merged.txt", "as the squash merge left it\n")
        self.merge()
        text = (self.kit.run_dir / "tasks" / "01-tests.md").read_text()
        self.assertTrue(text.startswith("# 01-tests: tune Merge JSON things\n"), text)
        self.assertEqual(runfolder.get(self.kit.run_dir / "tasks" / "01-tests.md", "Status"), "ready")
        self.assertEqual(runfolder.get(self.kit.run_dir / "tasks" / "01-tests.md", "Range"), f"{main}..{tip}")

    def test_no_tuning_task_when_every_suite_was_quicker_than_the_minimum(self):
        self.push_to_ticket("merged.txt", "x\n")
        self.merge()
        self.assertFalse((self.kit.run_dir / "tasks" / "01-tests.md").exists())

    def test_no_tuning_task_while_another_implementer_task_is_open(self):
        self.setting("tune_min_seconds", "0")
        (self.kit.run_dir / "tasks" / "01-02-b.md").write_text(TASK.format(nn="02", status="ready"))
        self.push_to_ticket("merged.txt", "x\n")
        self.merge()
        self.assertFalse((self.kit.run_dir / "tasks" / "01-tests.md").exists())


if __name__ == "__main__":
    unittest.main()
