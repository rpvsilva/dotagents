"""Tests for `tracker_cmd.py`, `clean.py` and `pin_dispatcher.py`, against a bare origin and a stub `gh`."""
import io
import re
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import runfolder, tracker  # noqa: E402
from testkit import SCRIPTS, Kit  # noqa: E402

TRACKER = SCRIPTS / "tracker_cmd.py"
CLEAN = SCRIPTS / "clean.py"
PIN = SCRIPTS / "pin_dispatcher.py"
START_TASK = SCRIPTS / "start_task.py"
MARKER = "<!-- build-epic-plan -->"


def key(*args):
    """The stub `gh`'s answer key for a call starting with `args`."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", "-".join(args[:2]))


def comments(issue):
    return key("api", f"repos/{{owner}}/{{repo}}/issues/{issue}/comments")


class RunCase(unittest.TestCase):
    def setUp(self):
        self.kit = Kit()
        self.addCleanup(self.kit.close)
        self.kit.make_origin()
        self.kit.init_run()
        (self.kit.run_dir / "tickets").mkdir()
        (self.kit.run_dir / "tasks").mkdir()
        self.ticket("01", "json", "ready", "#5")
        self.ticket("02", "csv", "ready", "#6, #7")
        self.task("01-01-lib.md", "Lib", "ready", "none")
        self.task("01-02-hub.md", "Hub", "ready", "01, ticket 02")
        self.task("02-01-csv.md", "CSV", "ready", "none")

    def ticket(self, tt, slug, status, issue):
        path = self.kit.run_dir / "tickets" / f"{tt}-{slug}.md"
        path.write_text(f"# {tt}: {slug.upper()}\nStatus: {status}\nIssue: {issue}\n"
                        f"Branch: feat/gh-{int(tt)}-{slug}\nBlocked by: none\n")
        return path

    def task(self, name, title, status, blocked):
        path = self.kit.run_dir / "tasks" / name
        tt = name.split("-", 1)[0]
        path.write_text(f"# {name[:5]}: {title}\nStatus: {status}\nTicket: {tt}\nBlocked by: {blocked}\n"
                        "Writes: a.py\n")
        return path

    def one_line(self, p):
        self.assertEqual(p.stdout.count("\n"), 1, p.stdout + p.stderr)
        return p.stdout.rstrip("\n")

    def ok(self, p):
        line = self.one_line(p)
        self.assertEqual(p.returncode, 0, line + p.stderr)
        return line

    def log_of(self, line):
        self.assertRegex(line, r"\(log: .+\)$")
        path = Path(line.rsplit("(log: ", 1)[1][:-1])
        self.assertTrue(path.is_file(), path)
        return path

    def updates(self):
        return [c for c in self.kit.gh_calls() if c[:2] != ["api", "user"]]

    def run_files(self):
        return sorted(str(p.relative_to(self.kit.run_dir)) for p in self.kit.run_dir.rglob("*")
                      if p.is_file() and "logs" not in p.parts and "worktrees" not in p.parts)


class PlannedTest(RunCase):
    def test_comments_the_plan_on_every_issue_and_counts_them(self):
        self.kit.answer("api-user", out="agent-bot\n")
        self.ticket("02", "csv", "claimed", "#6, #7")
        line = self.ok(self.kit.step(TRACKER, self.kit.run_dir, "planned"))
        self.assertEqual(line, "OK plan commented 3 issues")
        posts = {c[1]: c[3] for c in self.updates() if "--paginate" not in c}
        self.assertEqual(sorted(posts), [f"repos/{{owner}}/{{repo}}/issues/{n}/comments" for n in (5, 6, 7)])
        body = posts["repos/{owner}/{repo}/issues/5/comments"]
        self.assertTrue(body.startswith(f"body={MARKER}\n"), body)
        self.assertIn("01-01: Lib (blocked by: none)", body)
        self.assertIn("01-02: Hub (blocked by: 01, ticket 02)", body)
        self.assertNotIn("CSV", body)

    def test_edits_the_existing_marked_comment_instead_of_adding_a_second(self):
        self.kit.answer("api-user", out="agent-bot\n")
        self.ticket("02", "csv", "ready", "none")
        self.kit.answer(comments(5), out="4242\n", n=1)
        line = self.ok(self.kit.step(TRACKER, self.kit.run_dir, "planned"))
        self.assertEqual(line, "OK plan commented 1 issues")
        writes = [c[:4] for c in self.updates() if "--paginate" not in c]
        self.assertEqual(writes, [["api", "repos/{owner}/{repo}/issues/comments/4242", "-X", "PATCH"]])

    def test_the_orchestrators_ticket_notes_go_in_the_comment(self):
        self.kit.answer("api-user", out="agent-bot\n")
        self.ticket("02", "csv", "ready", "none")
        (self.kit.run_dir / "plan").mkdir()
        (self.kit.run_dir / "plan" / "01.md").write_text("Open question 2: decided JSON lines.\n")
        self.ok(self.kit.step(TRACKER, self.kit.run_dir, "planned"))
        body = [c[3] for c in self.updates() if "--paginate" not in c][0]
        self.assertIn("Open question 2: decided JSON lines.", body)

    def test_a_failed_comment_fails_with_the_log(self):
        self.kit.answer("api-user", out="agent-bot\n")
        self.kit.answer(comments(5), err="HTTP 404: Not Found\n", rc=1)
        p = self.kit.step(TRACKER, self.kit.run_dir, "planned")
        self.assertEqual(p.returncode, 1)
        line = self.one_line(p)
        self.assertTrue(line.startswith("FAIL plan gh-comment (log: "), line)
        self.assertIn("HTTP 404", self.log_of(line).read_text())

    def test_flush_applies_the_updates_held_while_the_identity_was_away(self):
        self.kit.answer("api-user", out="agent-bot\n", n=1)
        self.kit.answer("api-user", out="someone-else\n", n=2)
        self.kit.answer("api-user", out="agent-bot\n")
        self.ok(self.kit.step(TRACKER, self.kit.run_dir, "unblocked", "01"))
        p = self.kit.step(TRACKER, self.kit.run_dir, "unblocked", "01")
        self.assertTrue(self.one_line(p).startswith("FAIL 01 identity-changed (log: "), p.stdout)
        self.assertEqual(len(self.updates()), 1)
        self.assertEqual(self.ok(self.kit.step(TRACKER, self.kit.run_dir, "flush")), "OK tracker flushed 1 held")
        self.assertEqual(self.updates(), [["issue", "edit", "5", "--remove-label", "blocked"]] * 2)
        t = tracker.Tracker(self.kit.run_dir, self.kit.clone, io.StringIO())
        self.assertEqual(t.held(), [])


class BlockedTest(RunCase):
    def test_blocked_labels_every_issue_and_comments_the_task_and_why(self):
        self.kit.answer("api-user", out="agent-bot\n")
        line = self.ok(self.kit.step(TRACKER, self.kit.run_dir, "blocked", "01-02", "the", "map is wrong"))
        self.assertEqual(line, "OK 01 blocked 1 issues")
        self.assertEqual(self.updates(), [
            ["label", "create", "blocked", "--force"],
            ["issue", "edit", "5", "--add-label", "blocked"],
            ["issue", "comment", "5", "--body", "Blocked: task 01-02 (Hub): the map is wrong"],
        ])

    def test_unblocked_removes_the_label(self):
        self.kit.answer("api-user", out="agent-bot\n")
        line = self.ok(self.kit.step(TRACKER, self.kit.run_dir, "unblocked", "02"))
        self.assertEqual(line, "OK 02 unblocked 2 issues")
        self.assertEqual(self.updates(), [
            ["issue", "edit", "6", "--remove-label", "blocked"],
            ["issue", "edit", "7", "--remove-label", "blocked"],
        ])

    def test_blocked_without_a_why_is_refused(self):
        p = self.kit.step(TRACKER, self.kit.run_dir, "blocked", "01")
        self.assertEqual(p.returncode, 1)
        self.assertTrue(self.one_line(p).startswith("FAIL 01 usage: "), p.stdout)
        self.assertEqual(self.kit.gh_calls(), [])


class ClaimsTest(RunCase):
    def test_a_ticket_assigned_to_someone_else_is_claimed(self):
        self.kit.answer("api-user", out="rpvsilva\n")
        self.kit.answer("issue-view", out="rpvsilva\n", n=1)        # #5: the human's own
        self.kit.answer("issue-view", out="", n=2)                  # #6: nobody
        self.kit.answer("issue-view", out="alice\nrpvsilva\n", n=3)  # #7: another developer
        line = self.ok(self.kit.step(TRACKER, self.kit.run_dir, "claims"))
        self.assertEqual(line, "OK claims claimed 1 tickets 02")
        t1, t2 = sorted((self.kit.run_dir / "tickets").glob("*.md"))
        self.assertEqual(runfolder.get(t1, "Status"), "ready")
        self.assertEqual(runfolder.get(t2, "Status"), "claimed")
        self.assertEqual(runfolder.get(t2, "Claimed by"), "alice")
        self.assertEqual([c for c in self.updates() if c[:2] != ["issue", "view"]], [])

    def test_no_claims_leaves_every_ticket(self):
        self.kit.answer("api-user", out="rpvsilva\n")
        line = self.ok(self.kit.step(TRACKER, self.kit.run_dir, "claims"))
        self.assertEqual(line, "OK claims claimed 0 tickets")

    def test_an_unknown_command_is_refused(self):
        p = self.kit.step(TRACKER, self.kit.run_dir, "launch")
        self.assertEqual(p.returncode, 2)
        self.assertTrue(self.one_line(p).startswith("FAIL launch usage: "), p.stdout)


class CloseEpicTest(RunCase):
    def state(self, call, state):
        """The n-th `gh issue view` answers `state`."""
        self.kit.answer(key("issue", "view"), out=state + "\n", n=call)

    def close_epic(self, *args):
        return self.kit.step(TRACKER, self.kit.run_dir, "close-epic", *args)

    def test_closes_the_epic_once_every_ticket_issue_is_closed(self):
        runfolder.set_header(self.kit.run_dir / "run.md", "Epic", "#4")
        for n in (1, 2, 3):
            self.state(n, "CLOSED")
        self.assertEqual(self.ok(self.close_epic()), "OK epic closed #4")
        views = [c[2] for c in self.updates() if c[:2] == ["issue", "view"]]
        self.assertEqual(views, ["5", "6", "7"])
        closes = [c for c in self.updates() if c[:2] == ["issue", "close"]]
        self.assertEqual([c[2] for c in closes], ["4"])

    def test_an_open_ticket_issue_leaves_the_epic_open(self):
        self.state(1, "CLOSED")
        self.state(2, "OPEN")
        self.state(3, "CLOSED")
        self.assertEqual(self.ok(self.close_epic("4")), "OK epic open #6")
        self.assertEqual([c for c in self.updates() if c[:2] == ["issue", "close"]], [])

    def test_without_an_epic_issue_it_is_refused(self):
        line = self.one_line(self.close_epic())
        self.assertRegex(line, r"^FAIL epic no-epic: ")
        self.assertEqual([c for c in self.updates() if c[:2] == ["issue", "close"]], [])


class CleanTest(RunCase):
    def setUp(self):
        super().setUp()
        self.ticket("01", "json", "building", "#5")
        self.kit.git("push", "-q", "origin", "origin/main:refs/heads/feat/gh-1-json", cwd=self.kit.clone)
        self.ok(self.kit.step(START_TASK, self.kit.run_dir, "01-01"))
        runfolder.set_header(self.kit.run_dir / "tasks" / "01-01-lib.md", "Status", "done")
        self.kit.git("branch", "task/gh-1-json/02", cwd=self.kit.clone)  # a branch whose worktree is gone
        self.kit.git("branch", "unrelated", cwd=self.kit.clone)

    def test_removes_worktrees_task_branches_and_closed_issues_labels(self):
        self.kit.answer("api-user", out="agent-bot\n")
        self.kit.answer("issue-view", out="CLOSED\nin progress\nbug\n", n=1)  # #5
        self.kit.answer("issue-view", out="OPEN\nin progress\n", n=2)         # #6
        self.kit.answer("issue-view", out="CLOSED\n", n=3)                    # #7
        files = self.run_files()
        line = self.ok(self.kit.step(CLEAN, self.kit.run_dir))
        self.assertEqual(line, "OK run clean 4 removed")
        self.assertFalse((self.kit.run_dir / "worktrees" / "01-01").exists())
        self.assertNotIn("01-01", self.kit.git("worktree", "list", cwd=self.kit.clone))
        self.assertEqual(self.kit.git("branch", "--list", "task/*", cwd=self.kit.clone), "")
        self.assertIn("unrelated", self.kit.git("branch", "--list", cwd=self.kit.clone))
        self.assertEqual([c for c in self.updates() if c[:2] != ["issue", "view"]],
                         [["issue", "edit", "5", "--remove-label", "in progress"]])
        self.assertLessEqual(set(files), set(self.run_files()))

    def test_a_second_clean_removes_nothing(self):
        self.kit.answer("issue-view", out="OPEN\n")
        self.ok(self.kit.step(CLEAN, self.kit.run_dir))
        self.assertEqual(self.ok(self.kit.step(CLEAN, self.kit.run_dir)), "OK run clean 0 removed")

    def test_a_task_in_progress_is_refused(self):
        runfolder.set_header(self.kit.run_dir / "tasks" / "01-01-lib.md", "Status", "in-progress")
        p = self.kit.step(CLEAN, self.kit.run_dir)
        self.assertEqual(p.returncode, 1)
        self.assertTrue(self.one_line(p).startswith("FAIL run in-progress 01-01 (log: "), p.stdout)
        self.assertTrue((self.kit.run_dir / "worktrees" / "01-01").is_dir())


class PinDispatcherTest(RunCase):
    def setUp(self):
        super().setUp()
        script = self.kit.clone / "scripts" / "remote-agent.sh"
        script.parent.mkdir()
        script.write_text("#!/bin/sh\n")
        self.kit.git("add", "scripts", cwd=self.kit.clone)
        self.kit.git("commit", "-qm", "dispatcher", cwd=self.kit.clone)
        self.kit.git("push", "-q", "origin", "HEAD:main", cwd=self.kit.clone)
        self.link = self.kit.home / ".agents" / "bin" / "remote-agent"
        self.link.parent.mkdir(parents=True)
        self.wt = self.kit.run_dir / "worktrees" / "dispatcher"

    def main_tip(self):
        return self.kit.git("ls-remote", str(self.kit.origin), "refs/heads/main").split()[0]

    def push_main(self, name):
        (self.kit.clone / name).write_text(name)
        self.kit.git("add", name, cwd=self.kit.clone)
        self.kit.git("commit", "-qm", name, cwd=self.kit.clone)
        self.kit.git("push", "-q", "origin", "HEAD:main", cwd=self.kit.clone)

    def test_a_link_into_the_epics_repo_pins_the_default_tip(self):
        self.link.symlink_to(self.kit.clone / "scripts" / "remote-agent.sh")
        old = self.main_tip()
        self.push_main("later")
        self.kit.git("update-ref", "refs/remotes/origin/main", old, cwd=self.kit.clone)  # a stale last fetch
        tip = self.main_tip()
        self.assertEqual(self.ok(self.kit.step(PIN, self.kit.run_dir)), f"OK dispatcher pinned {tip[:7]}")
        self.assertEqual((self.kit.run_dir / "dispatcher.sha").read_text().strip(), tip)
        self.assertEqual(self.kit.git("rev-parse", "HEAD", cwd=self.wt), tip)
        self.assertTrue((self.wt / "scripts" / "remote-agent.sh").is_file())

    def test_an_existing_pin_is_reused_and_its_worktree_recreated(self):
        self.link.symlink_to(self.kit.clone / "scripts" / "remote-agent.sh")
        old = self.main_tip()
        self.ok(self.kit.step(PIN, self.kit.run_dir))
        shutil.rmtree(str(self.wt))  # deleted by hand: git still lists it until pruned
        self.push_main("newer")
        self.assertEqual(self.ok(self.kit.step(PIN, self.kit.run_dir)), f"OK dispatcher pinned {old[:7]}")
        self.assertEqual(self.kit.git("rev-parse", "HEAD", cwd=self.wt), old)
        self.assertEqual(self.ok(self.kit.step(PIN, self.kit.run_dir)), f"OK dispatcher pinned {old[:7]}")

    def test_a_dispatcher_worktree_moved_off_its_pin_or_a_bad_pin_is_refused(self):
        self.link.symlink_to(self.kit.clone / "scripts" / "remote-agent.sh")
        pin = self.main_tip()
        self.ok(self.kit.step(PIN, self.kit.run_dir))
        self.push_main("newer")
        self.kit.git("fetch", "-q", "origin", cwd=self.wt)
        self.kit.git("checkout", "-q", "--detach", "origin/main", cwd=self.wt)
        p = self.kit.step(PIN, self.kit.run_dir)
        self.assertEqual(p.returncode, 1)
        line = self.one_line(p)
        self.assertTrue(line.startswith(f"FAIL dispatcher dispatcher-moved: {self.wt} is not at {pin[:7]} (log: "),
                        line)
        self.assertNotEqual(self.kit.git("rev-parse", "HEAD", cwd=self.wt), pin)  # reported, never moved back
        sha_file = self.kit.run_dir / "dispatcher.sha"
        sha_file.write_text("main\n")
        line = self.one_line(self.kit.step(PIN, self.kit.run_dir))
        self.assertTrue(line.startswith(f"FAIL dispatcher bad-dispatcher-sha: {sha_file} (log: "), line)

    def test_a_link_into_another_checkout_needs_no_pin(self):
        other = self.kit.tmp / "other" / "scripts" / "remote-agent.sh"
        other.parent.mkdir(parents=True)
        other.write_text("#!/bin/sh\n")
        self.link.symlink_to(other)
        self.assertEqual(self.ok(self.kit.step(PIN, self.kit.run_dir)), "OK dispatcher installed")
        self.assertFalse((self.kit.run_dir / "dispatcher.sha").exists())
        self.assertFalse(self.wt.exists())

    def test_a_missing_link_needs_no_pin(self):
        self.assertEqual(self.ok(self.kit.step(PIN, self.kit.run_dir)), "OK dispatcher installed")
        self.assertFalse((self.kit.run_dir / "dispatcher.sha").exists())


if __name__ == "__main__":
    unittest.main()
