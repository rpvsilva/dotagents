"""Tests for `start_ticket.py`, `start_task.py` and `steplib/tracker.py`, against a bare origin and a stub `gh`."""
import io
import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import core, runfolder, tracker  # noqa: E402
from testkit import SCRIPTS, Kit, skip_without_zsh, zsh  # noqa: E402

START_TICKET = SCRIPTS / "start_ticket.py"
START_TASK = SCRIPTS / "start_task.py"
BRANCH = "feat/gh-1-v1.2-json"


def key(*args):
    """The stub `gh`'s answer key for a call starting with `args`."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", "-".join(args[:2]))


class StartCase(unittest.TestCase):
    def setUp(self):
        self.kit = Kit()
        self.addCleanup(self.kit.close)
        self.kit.make_origin()
        self.kit.init_run()
        (self.kit.run_dir / "tickets").mkdir()
        (self.kit.run_dir / "tasks").mkdir()
        self.ticket = self.kit.run_dir / "tickets" / "01-json.md"
        self.write_ticket("ready", "#5")

    def write_ticket(self, status, issue):
        self.ticket.write_text(f"# 01: JSON\nStatus: {status}\nIssue: {issue}\nBranch: {BRANCH}\n\nBody.\n",
                               encoding="utf-8")

    def one_line(self, p):
        self.assertEqual(p.stdout.count("\n"), 1, p.stdout + p.stderr)
        return p.stdout.rstrip("\n")

    def log_of(self, line):
        self.assertRegex(line, r"\(log: .+\)$")
        path = Path(line.rsplit("(log: ", 1)[1][:-1])
        self.assertTrue(path.is_file(), path)
        return path

    def status(self, path):
        return runfolder.get(path, "Status")

    def remote_tip(self, branch):
        out = self.kit.git("ls-remote", str(self.kit.origin), f"refs/heads/{branch}")
        return out.split()[0] if out else None

    def main_tip(self):
        return self.remote_tip("main")

    def commit(self, branch, name, text, msg):
        """Commits `name` with `text` on `branch` in the clone and pushes it."""
        self.kit.git("checkout", "-q", "-B", branch, f"origin/{branch}", cwd=self.kit.clone)
        (self.kit.clone / name).write_text(text, encoding="utf-8")
        self.kit.git("add", name, cwd=self.kit.clone)
        self.kit.git("commit", "-qm", msg, cwd=self.kit.clone)
        self.kit.git("push", "-q", "origin", branch, cwd=self.kit.clone)
        self.kit.git("checkout", "-q", "--detach", cwd=self.kit.clone)

    def settings(self, **values):
        """A stub `~/.agents/bin/settings` answering `get build-epic <key>` from `values`."""
        tool = self.kit.home / ".agents" / "bin" / "settings"
        tool.parent.mkdir(parents=True, exist_ok=True)
        cases = "".join(f'  {k}) echo "{v}" ;;\n' for k, v in values.items())
        tool.write_text(f'#!/bin/sh\ncase "$5" in\n{cases}esac\n', encoding="utf-8")
        tool.chmod(0o755)


class StartTicketTest(StartCase):
    def test_pushes_the_branch_from_the_default_tip_and_sets_building(self):
        self.kit.answer("api-user", out="rpvsilva\n")
        p = self.kit.step(START_TICKET, self.kit.run_dir, "01")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        base = self.main_tip()
        self.assertEqual(self.one_line(p), f"OK 01 building branch={BRANCH} base={base[:7]}")
        self.assertEqual(self.remote_tip(BRANCH), base)
        self.assertEqual(self.status(self.ticket), "building")

    def test_marks_each_issue_started_creating_the_label_once(self):
        self.write_ticket("ready", "#5, #6")
        self.kit.answer("api-user", out="rpvsilva\n")
        self.one_line(self.kit.step(START_TICKET, self.kit.run_dir, "01"))
        calls = [c for c in self.kit.gh_calls() if c[:2] != ["api", "user"]]
        self.assertEqual(calls, [
            ["label", "create", "in progress", "--force"],
            ["issue", "edit", "5", "--add-label", "in progress"],
            ["issue", "edit", "5", "--add-assignee", "rpvsilva"],
            ["issue", "edit", "6", "--add-label", "in progress"],
            ["issue", "edit", "6", "--add-assignee", "rpvsilva"],
        ])

    def test_the_in_progress_setting_replaces_the_label(self):
        self.settings(in_progress="Status: In progress")
        self.kit.answer("api-user", out="rpvsilva\n")
        line = self.one_line(self.kit.step(START_TICKET, self.kit.run_dir, "01"))
        self.assertTrue(line.startswith("OK 01 building ") and line.endswith(" started=manual"), line)
        calls = [c for c in self.kit.gh_calls() if c[:2] != ["api", "user"]]
        self.assertEqual(calls, [["issue", "edit", "5", "--add-assignee", "rpvsilva"]])

    def test_another_tracker_leaves_github_alone(self):
        self.settings(tracker="jira")
        line = self.one_line(self.kit.step(START_TICKET, self.kit.run_dir, "01"))
        self.assertTrue(line.endswith(" started=manual"), line)
        self.assertEqual(self.kit.gh_calls(), [])

    def test_a_ticket_with_no_issue_touches_no_tracker(self):
        self.write_ticket("ready", "none")
        self.assertTrue(self.one_line(self.kit.step(START_TICKET, self.kit.run_dir, "01")).startswith("OK 01 building"))
        self.assertEqual(self.kit.gh_calls(), [])

    def test_a_failed_push_fails_and_leaves_the_ticket_ready(self):
        hook = self.kit.origin / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\necho 'refusing every push' >&2\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
        p = self.kit.step(START_TICKET, self.kit.run_dir, "01")
        self.assertEqual(p.returncode, 1)
        line = self.one_line(p)
        self.assertTrue(line.startswith("FAIL 01 push (log: "), line)
        self.assertIn("refusing every push", self.log_of(line).read_text(encoding="utf-8"))
        self.assertEqual(self.status(self.ticket), "ready")
        self.assertEqual(self.kit.gh_calls(), [])

    def test_a_started_ticket_is_refused(self):
        self.kit.answer("api-user", out="rpvsilva\n")
        self.one_line(self.kit.step(START_TICKET, self.kit.run_dir, "01"))
        calls = len(self.kit.gh_calls())
        p = self.kit.step(START_TICKET, self.kit.run_dir, "01")
        self.assertEqual(p.returncode, 1)
        self.assertTrue(self.one_line(p).startswith("FAIL 01 already-started status=building (log: "), p.stdout)
        self.assertEqual(len(self.kit.gh_calls()), calls)

    def test_a_ticket_not_ready_or_without_a_branch_is_refused_and_pushes_nothing(self):
        planned = f"# 01: JSON\nStatus: planned\nIssue: #5\nBranch: {BRANCH}\n"
        no_branch = "# 01: JSON\nStatus: ready\nIssue: #5\n"
        for text, reason in ((planned, "not-ready status=planned"), (no_branch, "no-branch")):
            with self.subTest(reason):
                self.ticket.write_text(text, encoding="utf-8")
                p = self.kit.step(START_TICKET, self.kit.run_dir, "01")
                self.assertEqual(p.returncode, 1)
                self.assertTrue(self.one_line(p).startswith(f"FAIL 01 {reason} (log: "), p.stdout)
                self.assertEqual(self.ticket.read_text(encoding="utf-8"), text)
                self.assertIsNone(self.remote_tip(BRANCH))
                self.assertEqual(self.kit.gh_calls(), [])

    def test_identity_changed_mid_start_holds_the_update(self):
        # The human's login, then the identity on the label update, then another login on the assign.
        for n, login in enumerate(("rpvsilva", "agent-bot", "someone-else"), 1):
            self.kit.answer("api-user", out=login + "\n", n=n)
        p = self.kit.step(START_TICKET, self.kit.run_dir, "01")
        self.assertEqual(p.returncode, 1)
        line = self.one_line(p)
        self.assertTrue(line.startswith("FAIL 01 identity-changed (log: "), line)
        self.log_of(line)
        self.assertNotIn(["issue", "edit", "5", "--add-assignee", "rpvsilva"], self.kit.gh_calls())
        t = tracker.Tracker(self.kit.run_dir, self.kit.clone, io.StringIO())
        self.assertEqual(t.held(), [["assign", [5, "rpvsilva"]]])


class TrackerTest(StartCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, self.kit.env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.log = io.StringIO()
        self.t = tracker.Tracker(self.kit.run_dir, self.kit.clone, self.log)

    def updates(self):
        return [c for c in self.kit.gh_calls() if c[:2] != ["api", "user"]]

    def test_confirms_the_login_before_every_update(self):
        self.kit.answer("api-user", out="agent-bot\n")
        self.t.assign(5, "rpvsilva")
        self.t.remove_label(5, "blocked")
        self.assertEqual(self.kit.gh_calls(), [
            ["api", "user", "--jq", ".login"], ["issue", "edit", "5", "--add-assignee", "rpvsilva"],
            ["api", "user", "--jq", ".login"], ["issue", "edit", "5", "--remove-label", "blocked"],
        ])

    def test_creates_each_label_once_per_run(self):
        self.kit.answer("api-user", out="agent-bot\n")
        self.t.add_label(5, "blocked")
        tracker.Tracker(self.kit.run_dir, self.kit.clone, self.log).add_label(6, "blocked")
        self.assertEqual(self.updates(), [
            ["label", "create", "blocked", "--force"],
            ["issue", "edit", "5", "--add-label", "blocked"],
            ["issue", "edit", "6", "--add-label", "blocked"],
        ])

    def test_held_updates_apply_in_order_once_the_identity_is_back(self):
        self.kit.answer("api-user", out="agent-bot\n", n=1)
        self.kit.answer("api-user", out="someone-else\n", n=2)
        self.kit.answer("api-user", out="someone-else\n", n=3)
        self.kit.answer("api-user", out="agent-bot\n")
        self.t.assign(5, "rpvsilva")
        for update in (lambda: self.t.close(5, "https://pr/1"), lambda: self.t.remove_label(5, "in progress")):
            with self.assertRaises(core.Fail) as e:
                update()
            self.assertEqual(e.exception.reason, "identity-changed")
        self.assertEqual(len(self.updates()), 1)
        self.t.add_label(6, "blocked")
        self.assertEqual(self.updates(), [
            ["issue", "edit", "5", "--add-assignee", "rpvsilva"],
            ["issue", "close", "5", "--reason", "completed", "--comment", "https://pr/1"],
            ["issue", "edit", "5", "--remove-label", "in progress"],
            ["label", "create", "blocked", "--force"],
            ["issue", "edit", "6", "--add-label", "blocked"],
        ])
        self.assertEqual(self.t.held(), [])

    def test_an_app_installation_token_is_an_identity(self):
        self.kit.answer("api-user", err="HTTP 403: Resource not accessible by integration\n", rc=1)
        self.t.close(5, "done")
        self.assertIn(["api", "installation/repositories", "--jq", ".total_count"], self.kit.gh_calls())
        self.assertIn(["issue", "close", "5", "--reason", "completed", "--comment", "done"], self.kit.gh_calls())

    def test_plan_comment_is_edited_when_one_exists(self):
        self.kit.answer("api-user", out="agent-bot\n")
        self.kit.answer(key("api", "repos/{owner}/{repo}/issues/5/comments"), out="", n=1)
        self.kit.answer(key("api", "repos/{owner}/{repo}/issues/5/comments"), out="", n=2)
        self.kit.answer(key("api", "repos/{owner}/{repo}/issues/5/comments"), out="4242\n", n=3)
        self.t.upsert_plan(5, "first plan")
        self.t.upsert_plan(5, f"{tracker.MARKER}\nsecond plan")
        writes = [c for c in self.updates() if "--paginate" not in c]
        self.assertEqual(writes, [
            ["api", "repos/{owner}/{repo}/issues/5/comments", "-f", f"body={tracker.MARKER}\nfirst plan"],
            ["api", "repos/{owner}/{repo}/issues/comments/4242", "-X", "PATCH", "-f",
             f"body={tracker.MARKER}\nsecond plan"],
        ])

    def test_reads_issue_numbers_from_the_issue_line(self):
        self.assertEqual(tracker.issues("#8, #9"), [8, 9])
        self.assertEqual(tracker.issues("8"), [8])
        self.assertEqual(tracker.issues("none"), [])
        self.assertEqual(tracker.issues(None), [])


class StartTaskTest(StartCase):
    def setUp(self):
        super().setUp()
        self.write_ticket("building", "none")
        self.kit.git("push", "-q", "origin", f"origin/main:refs/heads/{BRANCH}", cwd=self.kit.clone)
        self.commit(BRANCH, "ticket.txt", "ticket work\n", "ticket work")
        tasks = self.kit.run_dir / "tasks"
        (tasks / "01-01-lib.md").write_text("# 01-01: Lib\nStatus: ready\nTicket: 01\nBlocked by: none\n",
                                            encoding="utf-8")
        (tasks / "01-02-hub.md").write_text("# 01-02: Hub\nStatus: ready\nTicket: 01\nBlocked by: 01, ticket 02\n",
                                            encoding="utf-8")

    def task(self, name):
        return self.kit.run_dir / "tasks" / name

    def worktrees(self):
        return self.kit.git("worktree", "list", "--porcelain", cwd=self.kit.clone)

    def test_adds_the_task_worktree_on_its_branch_from_the_ticket_tip(self):
        p = self.kit.step(START_TASK, self.kit.run_dir, "01-01")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        wt = self.kit.run_dir / "worktrees" / "01-01"
        tip = self.remote_tip(BRANCH)
        self.assertEqual(self.one_line(p), f"OK 01-01 in-progress wt={wt} tip={tip[:7]}")
        self.assertEqual(self.kit.git("rev-parse", "HEAD", cwd=wt), tip)
        self.assertEqual(self.kit.git("branch", "--show-current", cwd=wt), "task/gh-1-v1.2-json/01")
        self.assertEqual(self.status(self.task("01-01-lib.md")), "in-progress")

    def test_a_hub_task_also_merges_the_default_branch(self):
        self.commit("main", "hub.md", "merged ticket\n", "another ticket")
        main = self.main_tip()
        p = self.kit.step(START_TASK, self.kit.run_dir, "01-02")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        wt = self.kit.run_dir / "worktrees" / "01-02"
        head = self.kit.git("rev-parse", "HEAD", cwd=wt)
        self.assertEqual(self.one_line(p), f"OK 01-02 in-progress wt={wt} tip={head[:7]} synced={main[:7]}")
        self.kit.git("merge-base", "--is-ancestor", main, head, cwd=wt)
        self.kit.git("merge-base", "--is-ancestor", self.remote_tip(BRANCH), head, cwd=wt)
        self.assertEqual((wt / "hub.md").read_text(encoding="utf-8"), "merged ticket\n")

    def test_a_hub_tasks_writes_gains_only_what_it_changed_not_what_its_base_merge_brought_in(self):
        # #521: 02-02's Writes in the hub-sync-conflict eval gained ticket 01's files from main.
        self.commit("main", "other_ticket.py", "from another ticket\n", "another ticket")
        self.one_line(self.kit.step(START_TASK, self.kit.run_dir, "01-02"))
        wt = self.kit.run_dir / "worktrees" / "01-02"
        self.assertTrue((wt / "other_ticket.py").is_file())
        (wt / "hub.txt").write_text("hub work\n", encoding="utf-8")
        task = self.task("01-02-hub.md")
        runfolder.set_header(task, "Writes", "README.md")
        p = self.kit.step(SCRIPTS / "finish_task.py", self.kit.run_dir, "01-02")
        self.assertTrue(self.one_line(p).startswith("OK 01-02 committed "), p.stdout)
        self.assertEqual(runfolder.get(task, "Writes"), "README.md, hub.txt")

    def test_a_hub_task_conflict_is_left_in_its_worktree_for_the_implementer(self):
        self.commit(BRANCH, "README.md", "ticket side\n", "ticket edit")
        self.commit("main", "README.md", "main side\n", "main edit")
        main = self.main_tip()
        p = self.kit.step(START_TASK, self.kit.run_dir, "01-02")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        wt = self.kit.run_dir / "worktrees" / "01-02"
        head = self.kit.git("rev-parse", "HEAD", cwd=wt)
        self.assertEqual(head, self.remote_tip(BRANCH))
        self.assertEqual(self.one_line(p),
                         f"OK 01-02 in-progress wt={wt} tip={head[:7]} synced={main[:7]} conflict=README.md")
        self.assertEqual(self.kit.git("rev-parse", "MERGE_HEAD", cwd=wt), main)
        text = (wt / "README.md").read_text(encoding="utf-8")
        self.assertIn("<<<<<<<", text)
        self.assertIn("ticket side", text)
        self.assertIn("main side", text)
        self.assertEqual(self.kit.git("branch", "--show-current", cwd=wt), "task/gh-1-v1.2-json/02")
        self.assertEqual(self.status(self.task("01-02-hub.md")), "in-progress")

    def test_a_resolved_base_merge_then_the_task_finishes_and_merges_into_the_ticket(self):
        self.commit(BRANCH, "README.md", "ticket side\n", "ticket edit")
        self.commit("main", "README.md", "main side\n", "main edit")
        main = self.main_tip()
        self.one_line(self.kit.step(START_TASK, self.kit.run_dir, "01-02"))
        wt = self.kit.run_dir / "worktrees" / "01-02"
        (wt / "README.md").write_text("ticket side\nmain side\n", encoding="utf-8")  # the merge left uncommitted
        (wt / "hub.txt").write_text("hub work\n", encoding="utf-8")
        (self.kit.run_dir / "map.md").write_text("# Map\n\n## Checks\n\n```\ntrue\n```\n", encoding="utf-8")
        runfolder.set_header(self.task("01-02-hub.md"), "Tier", "low")
        p = self.kit.step(SCRIPTS / "finish_task.py", self.kit.run_dir, "01-02")
        self.assertTrue(self.one_line(p).startswith("OK 01-02 committed "), p.stdout)
        p = self.kit.step(SCRIPTS / "merge_task.py", self.kit.run_dir, "01-02")
        self.assertTrue(self.one_line(p).startswith("OK 01-02 done "), p.stdout)
        tip = self.kit.git("rev-parse", f"refs/heads/{BRANCH}", cwd=self.kit.clone)
        self.kit.git("merge-base", "--is-ancestor", main, tip, cwd=self.kit.clone)
        self.assertEqual(self.kit.git("show", f"{tip}:README.md", cwd=self.kit.clone), "ticket side\nmain side")
        self.assertEqual(self.kit.git("show", f"{tip}:hub.txt", cwd=self.kit.clone), "hub work")

    def test_a_started_task_is_refused_without_a_second_worktree(self):
        self.one_line(self.kit.step(START_TASK, self.kit.run_dir, "01-01"))
        before = self.worktrees()
        p = self.kit.step(START_TASK, self.kit.run_dir, "01-01")
        self.assertEqual(p.returncode, 1)
        self.assertTrue(self.one_line(p).startswith("FAIL 01-01 already-started status=in-progress (log: "))
        runfolder.set_header(self.task("01-01-lib.md"), "Status", "ready")  # a lost status write
        p = self.kit.step(START_TASK, self.kit.run_dir, "01-01")
        self.assertTrue(self.one_line(p).startswith("FAIL 01-01 already-started worktree="), p.stdout)
        self.assertEqual(self.worktrees(), before)

    def test_a_task_of_a_ticket_not_building_is_refused(self):
        self.write_ticket("ready", "none")
        p = self.kit.step(START_TASK, self.kit.run_dir, "01-01")
        self.assertTrue(self.one_line(p).startswith("FAIL 01-01 ticket-not-building status=ready (log: "))
        self.assertFalse((self.kit.run_dir / "worktrees").exists())


    def test_a_task_not_ready_a_ticket_without_a_branch_or_a_leftover_branch_is_refused(self):
        lib = self.task("01-01-lib.md")
        lib.write_text(lib.read_text(encoding="utf-8").replace("Status: ready", "Status: planned"), encoding="utf-8")
        p = self.kit.step(START_TASK, self.kit.run_dir, "01-01")
        self.assertTrue(self.one_line(p).startswith("FAIL 01-01 not-ready status=planned (log: "), p.stdout)
        lib.write_text(lib.read_text(encoding="utf-8").replace("Status: planned", "Status: ready"), encoding="utf-8")
        self.ticket.write_text("# 01: JSON\nStatus: building\nIssue: none\n", encoding="utf-8")
        p = self.kit.step(START_TASK, self.kit.run_dir, "01-01")
        self.assertTrue(self.one_line(p).startswith("FAIL 01-01 no-branch (log: "), p.stdout)
        self.write_ticket("building", "none")
        self.kit.git("branch", "task/gh-1-v1.2-json/01", "main", cwd=self.kit.clone)
        p = self.kit.step(START_TASK, self.kit.run_dir, "01-01")
        self.assertTrue(self.one_line(p).startswith("FAIL 01-01 already-started branch=task/gh-1-v1.2-json/01 (log: "),
                        p.stdout)
        self.assertFalse((self.kit.run_dir / "worktrees").exists())
        self.assertEqual(self.status(lib), "ready")

class RefspecShellTest(StartCase):
    """Bug 6: a zsh `:r` modifier broke a refspec built from a branch name with `.` in it."""

    def start_both(self, shell):
        cmd = (f'run="{self.kit.run_dir}"; py="{sys.executable}"; dir="{SCRIPTS}";'
               f' $py -I $dir/start_ticket.py $run 01 && $py -I $dir/start_task.py $run 01-01')
        return self.kit.shell(shell, cmd)

    def check(self, shell):
        self.write_ticket("ready", "none")
        (self.kit.run_dir / "tasks" / "01-01-lib.md").write_text("# 01-01\nStatus: ready\nBlocked by: none\n",
                                                                 encoding="utf-8")
        p = self.start_both(shell)
        lines = p.stdout.splitlines()
        self.assertEqual(len(lines), 2, p.stdout + p.stderr)
        base = self.main_tip()
        self.assertEqual(lines[0], f"OK 01 building branch={BRANCH} base={base[:7]}")
        self.assertEqual(self.remote_tip(BRANCH), base)
        wt = self.kit.run_dir / "worktrees" / "01-01"
        self.assertEqual(lines[1], f"OK 01-01 in-progress wt={wt} tip={base[:7]}")
        self.assertEqual(self.kit.git("branch", "--show-current", cwd=wt), "task/gh-1-v1.2-json/01")

    def test_bash(self):
        self.check("bash")

    @skip_without_zsh
    def test_zsh(self):
        self.check(zsh)


if __name__ == "__main__":
    unittest.main()
