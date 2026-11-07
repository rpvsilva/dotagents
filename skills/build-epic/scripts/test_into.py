"""Tests for a run into a base other than the default branch (`decisions.md`'s `base:`): the ticket branch is cut from
it, a hub task catches up with it, and the draft ticket PR opens against it. Against a bare origin and a stub `gh`."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import runfolder  # noqa: E402
from test_merge_task import PR_VIEW  # noqa: E402
from testkit import SCRIPTS, Kit  # noqa: E402

DECIDE = SCRIPTS / "decide.py"
REPO = SCRIPTS.parents[2]
BASE = "int/x"
BRANCH = "feat/t"


class IntoCase(unittest.TestCase):
    def setUp(self):
        self.kit = kit = Kit()
        self.addCleanup(kit.close)
        kit.make_origin()
        kit.init_run()
        for d in ("tickets", "tasks", "worktrees"):
            (kit.run_dir / d).mkdir()
        kit.branch_from_main(BASE)
        self.base_tip = self.commit(BASE, "base.txt", "integration work\n", "feat: on the base")
        self.ticket = kit.run_dir / "tickets" / "01-t.md"
        self.decide("base", BASE)

    def decide(self, key, value):
        p = self.kit.step(DECIDE, self.kit.run_dir, "set", key, value)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def commit(self, branch, name, text, message):
        """Commits `name` on origin's `branch` from the clone, pushes it and returns the new tip."""
        clone = self.kit.clone
        self.kit.git("fetch", "-q", "origin", cwd=clone)
        self.kit.git("checkout", "-q", "-B", branch, f"origin/{branch}", cwd=clone)
        (clone / name).write_text(text, encoding="utf-8")
        self.kit.git("add", name, cwd=clone)
        self.kit.git("commit", "-qm", message, cwd=clone)
        self.kit.git("push", "-q", "origin", branch, cwd=clone)
        self.kit.git("checkout", "-q", "--detach", cwd=clone)
        return self.kit.git("rev-parse", "HEAD", cwd=clone)

    def origin_tip(self, ref):
        return self.kit.git("--git-dir", str(self.kit.origin), "rev-parse", ref)

    def step(self, script, *args):
        p = self.kit.step(SCRIPTS / script, self.kit.run_dir, *args)
        self.assertEqual(p.stdout.count("\n"), 1, p.stdout + p.stderr)
        line = p.stdout.rstrip("\n")
        self.assertTrue(line.startswith("OK "), line)
        return line


class StartTicketIntoTest(IntoCase):
    def test_the_ticket_branch_is_cut_from_the_base_tip(self):
        self.ticket.write_text(f"# 01: T\nStatus: ready\nIssue: none\nBranch: {BRANCH}\n", encoding="utf-8")
        self.assertEqual(self.step("start_ticket.py", "01"),
                         f"OK 01 building branch={BRANCH} base={self.base_tip[:7]}")
        self.assertEqual(self.origin_tip(BRANCH), self.base_tip)
        self.assertNotEqual(self.origin_tip(BRANCH), self.origin_tip("main"))

    def test_with_no_base_the_ticket_branch_is_cut_from_the_default_branch(self):
        (self.kit.run_dir / "decisions.md").unlink()
        self.ticket.write_text(f"# 01: T\nStatus: ready\nIssue: none\nBranch: {BRANCH}\n", encoding="utf-8")
        self.step("start_ticket.py", "01")
        self.assertEqual(self.origin_tip(BRANCH), self.origin_tip("main"))


class HubTaskIntoTest(IntoCase):
    def test_a_hub_task_catches_up_with_the_base_not_the_default_branch(self):
        self.kit.branch_from_main(BRANCH)
        main_tip = self.commit("main", "main.txt", "default branch only\n", "feat: on main")
        self.ticket.write_text(f"# 01: T\nStatus: building\nIssue: none\nBranch: {BRANCH}\n", encoding="utf-8")
        (self.kit.run_dir / "tasks" / "01-02-hub.md").write_text(
            "# 01-02: Hub\nStatus: ready\nTicket: 01\nBlocked by: ticket 02\n", encoding="utf-8")
        line = self.step("start_task.py", "01-02")
        self.assertTrue(line.endswith(f" synced={self.base_tip[:7]}"), line)
        wt = self.kit.run_dir / "worktrees" / "01-02"
        head = self.kit.git("rev-parse", "HEAD", cwd=wt)
        self.kit.git("merge-base", "--is-ancestor", self.base_tip, head, cwd=wt)
        p = subprocess.run(["git", "-C", str(wt), "merge-base", "--is-ancestor", main_tip, head], env=self.kit.env)
        self.assertEqual(p.returncode, 1, "the default branch's new commit was merged in")
        self.assertEqual((wt / "base.txt").read_text(encoding="utf-8"), "integration work\n")


class TicketPrIntoTest(IntoCase):
    def setUp(self):
        super().setUp()
        kit = self.kit
        (kit.bin / "gh").rename(kit.bin / "gh-kit")
        (kit.bin / "gh").write_text(PR_VIEW.format(python=sys.executable, kit_gh=str(kit.bin / "gh-kit"),
                                                   origin=str(kit.origin)), encoding="utf-8")
        (kit.bin / "gh").chmod(0o755)
        (kit.home / ".agents" / "bin").mkdir(parents=True)
        (kit.home / ".agents" / "bin" / "settings").symlink_to(REPO / "scripts" / "settings.sh")
        kit.env.pop("XDG_CONFIG_HOME", None)
        self.decide("ci", "on")
        kit.git("--git-dir", str(kit.origin), "update-ref", f"refs/heads/{BRANCH}", self.base_tip)
        kit.git("fetch", "-q", "origin", cwd=kit.clone)
        self.ticket.write_text(f"# 01: T\nStatus: building\nIssue: #7\nBranch: {BRANCH}\nBlocked by: none\n", encoding="utf-8")
        task = kit.run_dir / "tasks" / "01-01-a.md"
        task.write_text("# 01-01: a\nStatus: in-progress\nTicket: 01\nBlocked by: none\nWrites: x\n"
                        "PR: https://github.com/o/r/pull/5\n", encoding="utf-8")
        (kit.run_dir / "map.md").write_text("# Map\n\n## Checks\n\n```\ntrue\n```\n", encoding="utf-8")
        kit.answer("pr-list", out="[]")
        kit.answer("pr-create", out="https://github.com/o/r/pull/9\n")
        wt = kit.run_dir / "worktrees" / "01-01"
        kit.git("worktree", "add", "-q", "-b", "task/t/01", str(wt), f"origin/{BRANCH}", cwd=kit.clone)
        (wt / "task.txt").write_text("task\n", encoding="utf-8")
        kit.git("add", "task.txt", cwd=wt)
        kit.git("commit", "-qm", "feat: task one", cwd=wt)

    def calls(self, *prefix):
        return [c for c in self.kit.gh_calls() if c[:len(prefix)] == list(prefix)]

    def test_the_draft_ticket_pr_opens_against_the_base(self):
        self.step("merge_task.py", "01-01")
        create = self.calls("pr", "create")
        self.assertEqual(len(create), 1, json.dumps(self.kit.gh_calls()))
        self.assertEqual(create[0][create[0].index("--base") + 1], BASE)
        listed = self.calls("pr", "list")
        self.assertEqual(listed[0][listed[0].index("--base") + 1], BASE)
        self.assertEqual(runfolder.get(self.ticket, "PR"), "https://github.com/o/r/pull/9")


if __name__ == "__main__":
    unittest.main()
