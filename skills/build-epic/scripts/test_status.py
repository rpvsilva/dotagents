"""Tests for `status.py` (the run at a glance) and `resume.py` (SKILL.md's resume rules in one call)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import runfolder  # noqa: E402
from testkit import SCRIPTS, Kit, skip_without_zsh, zsh  # noqa: E402

STATUS = SCRIPTS / "status.py"
RESUME = SCRIPTS / "resume.py"

# `gh pr view <n>` answers `{"state": <answers/pr-<n>.state>, "headRefOid": ""}` (default OPEN). Every other call goes
# to the testkit stub.
PR_VIEW = r'''#!{python} -IS
import os, sys
d = os.environ["GH_STUB_DIR"]
args = sys.argv[1:]
if args[:2] != ["pr", "view"]:
    if os.name == "nt":  # Windows starts no `#!` script, and its os.execv returns before the child ends
        import subprocess
        sys.exit(subprocess.run([sys.executable, "-IS", {kit_gh!r}, *args]).returncode)
    os.execv(sys.executable, [sys.executable, "-IS", {kit_gh!r}, *args])
import json
with open(os.path.join(d, "gh.log"), "a") as f:
    f.write(json.dumps(args) + "\n")
p = os.path.join(d, "answers", "pr-" + args[2] + ".state")
state = open(p).read().strip() if os.path.exists(p) else "OPEN"
print(json.dumps({{"state": state, "headRefOid": ""}}))
'''


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def ticket(nn, title, status, branch, pr=None, blocked="none"):
    return (f"# {nn}: {title}\nStatus: {status}\nIssue: #4{nn[-1]}\nBranch: {branch}\nBlocked by: {blocked}\n"
            + (f"PR: {pr}\n" if pr else ""))


def task(tid, title, status, pr=None, blocked="none"):
    return (f"# {tid}: {title}\nStatus: {status}\nTicket: {tid.split('-')[0]}\nBlocked by: {blocked}\nWrites: x\n"
            + (f"PR: {pr}\n" if pr else ""))


class StatusTest(unittest.TestCase):
    def setUp(self):
        self.kit = Kit()
        self.addCleanup(self.kit.close)
        run = self.kit.run_dir
        write(run / "tickets" / "01-alpha.md", ticket("01", "Alpha things", "building", "feat/alpha",
                                                      pr="https://github.com/o/r/pull/12"))
        write(run / "tickets" / "02-beta.md", ticket("02", "Beta", "ready", "feat/beta", blocked="01"))
        write(run / "tasks" / "01-01-a.md", task("01-01", "First", "done", pr="https://github.com/o/r/pull/5"))
        write(run / "tasks" / "01-02-b.md", task("01-02", "Second", "in-progress", blocked="01"))
        write(run / "tasks" / "01-tests.md", "# 01-tests: tune Alpha things\nStatus: ready\nTicket: 01\n")
        write(run / "tasks" / "02-01-c.md", task("02-01", "Third", "ready", blocked="ticket 01"))

    def status(self, run=None):
        p = self.kit.step(STATUS, run or self.kit.run_dir)
        self.assertEqual(p.stderr, "")
        return p

    def test_prints_one_line_per_ticket_and_task_and_exits_0(self):
        p = self.status()
        self.assertEqual(p.returncode, 0, p.stdout)
        lines = p.stdout.splitlines()
        self.assertEqual([line.split()[0] for line in lines],
                         ["01", "01-01", "01-02", "01-tests", "02", "02-01"])

    def test_each_line_holds_status_pr_blockers_and_title(self):
        rows = {line.split()[0]: line.split() for line in self.status().stdout.splitlines()}
        self.assertEqual(rows["01"][1:4], ["building", "#12", "-"])
        self.assertEqual(rows["01-01"][1:4], ["done", "#5", "-"])
        self.assertEqual(rows["01-02"][1:4], ["in-progress", "-", "01"])
        self.assertEqual(rows["02"][1:4], ["ready", "-", "01"])
        self.assertEqual(rows["02-01"][1:4], ["ready", "-", "ticket-01"])
        self.assertIn("Alpha things", self.status().stdout.splitlines()[0])

    def test_reads_the_run_folder_only(self):
        self.status()
        self.assertEqual(self.kit.gh_calls(), [])

    def test_works_through_a_linked_run_path(self):
        p = self.status(self.kit.link(self.kit.run_dir, "run-link"))
        self.assertEqual(p.returncode, 0)
        self.assertEqual(len(p.stdout.splitlines()), 6)

    def test_a_missing_run_folder_is_one_fail_line(self):
        p = self.kit.step(STATUS, self.kit.tmp / "nope")
        self.assertEqual(p.returncode, 1)
        self.assertRegex(p.stdout, r"^FAIL run no-run-folder: .*nope\n$")


class ResumeTest(unittest.TestCase):
    """A run on a real origin: ticket 01 `building` on `feat/alpha`, ticket 02 `in-review`."""

    def setUp(self):
        self.kit = kit = Kit()
        self.addCleanup(kit.close)
        self.clone = clone = kit.make_origin()
        (kit.bin / "gh").rename(kit.bin / "gh-kit")
        write(kit.bin / "gh", PR_VIEW.format(python=sys.executable, kit_gh=str(kit.bin / "gh-kit"))).chmod(0o755)
        kit.branch_from_main("feat/alpha")
        kit.init_run()
        self.run_dir = kit.run_dir
        self.t01 = write(self.run_dir / "tickets" / "01-alpha.md", ticket("01", "Alpha", "building", "feat/alpha"))
        self.t02 = write(self.run_dir / "tickets" / "02-beta.md",
                         ticket("02", "Beta", "in-review", "feat/beta", pr="https://github.com/o/r/pull/20"))
        kit.answer("pr-list", out="[]")
        kit.answer("pr-create", out="https://github.com/o/r/pull/30\n")

    # Helpers

    def add_task(self, tid, status, pr=None, worktree=True):
        path = write(self.run_dir / "tasks" / f"{tid}-x.md", task(tid, "Task", status, pr=pr))
        if worktree:
            wt = self.run_dir / "worktrees" / tid
            self.kit.git("worktree", "add", "-q", "-b", f"task/alpha/{tid.split('-')[1]}", str(wt),
                         "origin/feat/alpha", cwd=self.clone)
        return path

    def merged(self, pr):
        write(self.kit.answers / f"pr-{pr}.state", "MERGED\n")

    def resume(self, run=None):
        p = self.kit.step(RESUME, run or self.run_dir)
        self.assertEqual(p.stdout.count("\n"), 1, p.stdout + p.stderr)
        line = p.stdout.rstrip("\n")
        self.assertEqual(p.returncode, 0 if line.startswith("OK ") else 1, line)
        if "(log: " in line:
            self.assertTrue(Path(line.rsplit("(log: ", 1)[1][:-1]).is_file(), line)
        return line

    def branch_exists(self, name):
        return bool(self.kit.git("branch", "--list", name, cwd=self.clone))

    def status(self, path):
        return runfolder.get(path, "Status")

    # Tickets

    def test_an_in_review_ticket_whose_pr_merged_becomes_merged(self):
        self.merged(20)
        self.assertEqual(self.resume(), "OK run resumed 1 actions")
        self.assertEqual(self.status(self.t02), "merged")
        self.assertEqual(self.status(self.t01), "building")

    def test_an_in_review_ticket_whose_pr_is_open_stays_in_review(self):
        self.assertEqual(self.resume(), "OK run resumed 0 actions")
        self.assertEqual(self.status(self.t02), "in-review")

    # Tasks

    def test_an_in_progress_task_with_no_pr_goes_back_to_ready_without_worktree_and_branch(self):
        t = self.add_task("01-01", "in-progress")
        self.assertEqual(self.resume(), "OK run resumed 1 actions")
        self.assertEqual(self.status(t), "ready")
        self.assertFalse((self.run_dir / "worktrees" / "01-01").exists())
        self.assertFalse(self.branch_exists("task/alpha/01"))

    def test_an_in_progress_task_with_no_pr_and_no_worktree_goes_back_to_ready(self):
        t = self.add_task("01-01", "in-progress", worktree=False)
        self.assertEqual(self.resume(), "OK run resumed 1 actions")
        self.assertEqual(self.status(t), "ready")

    def test_a_task_whose_pr_merged_becomes_done_with_ticket_ci(self):
        t = self.add_task("01-01", "in-progress", pr="https://github.com/o/r/pull/5")
        self.merged(5)
        self.assertEqual(self.resume(), "OK run resumed 1 actions")
        self.assertEqual(self.status(t), "done")
        self.assertFalse((self.run_dir / "worktrees" / "01-01").exists())
        self.assertFalse(self.branch_exists("task/alpha/01"))
        creates = [c for c in self.kit.gh_calls() if c[:2] == ["pr", "create"]]
        self.assertEqual(len(creates), 1, creates)
        self.assertIn("--draft", creates[0])
        self.assertEqual(runfolder.get(self.t01, "PR"), "https://github.com/o/r/pull/30")

    def test_a_merged_task_on_a_ticket_with_a_pr_opens_no_second_ticket_pr(self):
        runfolder.set_header(self.t01, "PR", "https://github.com/o/r/pull/12")
        self.add_task("01-01", "in-progress", pr="https://github.com/o/r/pull/5")
        self.merged(5)
        self.resume()
        self.assertEqual([c for c in self.kit.gh_calls() if c[:2] == ["pr", "create"]], [])

    def test_ticket_ci_off_opens_no_ticket_pr(self):
        write(self.run_dir / "decisions.md", "# Decisions\nci: off\n")
        t = self.add_task("01-01", "in-progress", pr="https://github.com/o/r/pull/5")
        self.merged(5)
        self.resume()
        self.assertEqual(self.status(t), "done")
        self.assertEqual([c for c in self.kit.gh_calls() if c[:2] == ["pr", "create"]], [])

    def test_a_task_with_an_open_pr_is_left_for_merge(self):
        t = self.add_task("01-01", "in-progress", pr="https://github.com/o/r/pull/5")
        self.assertEqual(self.resume(), "OK run resumed 0 actions")
        self.assertEqual(self.status(t), "in-progress")
        self.assertTrue((self.run_dir / "worktrees" / "01-01").is_dir())

    def test_ready_and_done_tasks_are_untouched(self):
        ready = self.add_task("01-01", "ready", worktree=False)
        done = self.add_task("01-02", "done", pr="https://github.com/o/r/pull/6", worktree=False)
        self.merged(6)
        self.assertEqual(self.resume(), "OK run resumed 0 actions")
        self.assertEqual((self.status(ready), self.status(done)), ("ready", "done"))
        self.assertEqual([c for c in self.kit.gh_calls() if c[:3] == ["pr", "view", "6"]], [])

    def test_every_state_at_once_then_a_rerun_does_nothing(self):
        self.merged(20)
        back = self.add_task("01-01", "in-progress")
        done = self.add_task("01-02", "in-progress", pr="https://github.com/o/r/pull/5")
        self.merged(5)
        left = self.add_task("01-03", "in-progress", pr="https://github.com/o/r/pull/6")
        self.assertEqual(self.resume(), "OK run resumed 3 actions")
        self.assertEqual([self.status(p) for p in (self.t02, back, done, left)],
                         ["merged", "ready", "done", "in-progress"])
        self.assertEqual(self.resume(), "OK run resumed 0 actions")

    # Dispatcher pin

    def pin(self):
        sha = self.kit.git("rev-parse", "origin/main", cwd=self.clone)
        write(self.run_dir / "dispatcher.sha", sha + "\n")
        newer = self.kit.tmp / "newer"
        self.kit.git("clone", "-q", str(self.kit.origin), str(newer))
        write(newer / "later.txt", "later\n")
        self.kit.git("add", "later.txt", cwd=newer)
        self.kit.git("commit", "-qm", "later", cwd=newer)
        self.kit.git("push", "-q", "origin", "main", cwd=newer)
        return sha

    def test_a_missing_dispatcher_worktree_is_recreated_at_the_pinned_sha_not_a_newer_one(self):
        sha = self.pin()
        self.kit.git("fetch", "-q", "origin", cwd=self.clone)
        self.assertEqual(self.resume(), "OK run resumed 1 actions")
        wt = self.run_dir / "worktrees" / "dispatcher"
        self.assertEqual(self.kit.git("rev-parse", "HEAD", cwd=wt), sha)
        self.assertEqual((self.run_dir / "dispatcher.sha").read_text().strip(), sha)

    def test_a_pinned_sha_the_clone_has_not_fetched_is_fetched_and_used(self):
        newer = self.kit.tmp / "newer"
        self.pin()
        sha = self.kit.git("rev-parse", "HEAD", cwd=newer)
        write(self.run_dir / "dispatcher.sha", sha + "\n")
        self.assertEqual(self.resume(), "OK run resumed 1 actions")
        self.assertEqual(self.kit.git("rev-parse", "HEAD", cwd=self.run_dir / "worktrees" / "dispatcher"), sha)

    def test_an_existing_dispatcher_worktree_is_reused(self):
        sha = self.pin()
        wt = self.run_dir / "worktrees" / "dispatcher"
        self.kit.git("worktree", "add", "-q", "--detach", str(wt), sha, cwd=self.clone)
        self.assertEqual(self.resume(), "OK run resumed 0 actions")
        self.assertEqual(self.kit.git("rev-parse", "HEAD", cwd=wt), sha)

    def test_a_dispatcher_worktree_at_another_commit_is_moved_back_to_the_pin(self):
        sha = self.pin()
        wt = self.run_dir / "worktrees" / "dispatcher"
        self.kit.git("fetch", "-q", "origin", cwd=self.clone)
        self.kit.git("worktree", "add", "-q", "--detach", str(wt), "origin/main", cwd=self.clone)
        self.assertEqual(self.resume(), "OK run resumed 1 actions")
        self.assertEqual(self.kit.git("rev-parse", "HEAD", cwd=wt), sha)

    def test_no_dispatcher_sha_pins_nothing(self):
        self.resume()
        self.assertFalse((self.run_dir / "worktrees" / "dispatcher").exists())
        self.assertFalse((self.run_dir / "dispatcher.sha").exists())

    def test_a_bad_dispatcher_sha_fails(self):
        write(self.run_dir / "dispatcher.sha", "not-a-sha\n")
        self.assertRegex(self.resume(), r"^FAIL run bad-dispatcher-sha .*\(log: .+\)$")

    # Contract

    def test_a_missing_run_folder_fails_and_is_not_created(self):
        missing = self.kit.tmp / "nope"
        self.assertEqual(self.resume(missing), f"FAIL run no-run-folder: {missing}")
        self.assertFalse(missing.exists())

    def test_works_through_a_linked_run_path(self):
        self.merged(20)
        self.assertEqual(self.resume(self.kit.link(self.run_dir, "run-link")), "OK run resumed 1 actions")

    def test_works_under_bash_c(self):
        self.merged(20)
        p = self.kit.shell("bash", f"python3 -I '{RESUME}' '{self.run_dir}'")
        self.assertEqual(p.stdout, "OK run resumed 1 actions\n", p.stderr)

    @skip_without_zsh
    def test_works_under_zsh_c(self):
        self.merged(20)
        p = self.kit.shell(zsh, f"python3 -I '{RESUME}' '{self.run_dir}'")
        self.assertEqual(p.stdout, "OK run resumed 1 actions\n", p.stderr)

    def test_a_gh_failure_fails_and_changes_nothing_after_it(self):
        self.kit.answer("pr-view", rc=1, err="HTTP 404: Not Found")
        (self.kit.bin / "gh").unlink()
        (self.kit.bin / "gh-kit").rename(self.kit.bin / "gh")
        self.assertRegex(self.resume(), r"^FAIL run pr-view \(log: .+\)$")
        self.assertEqual(self.status(self.t02), "in-review")
        self.assertEqual(self.kit.gh_calls()[0][:3], ["pr", "view", "20"])


if __name__ == "__main__":
    unittest.main()
