"""Tests for `steplib/union.py` through the steps that merge: a conflict only in hub files settles without an agent.

Each test builds a throwaway origin whose `cli.py` is a dispatcher: every `from cmds import <name>` line must have its
`<name>` in `COMMANDS`, which `decisions.md`'s `test_command:` (`check.py`) checks.
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import runfolder  # noqa: E402
from testkit import SCRIPTS, Kit  # noqa: E402
from test_merge_task import PR_VIEW, REPO  # noqa: E402

START_TASK, FINISH, MERGE_TASK, CATCH_UP = (SCRIPTS / f"{s}.py" for s in ("start_task", "finish_task", "merge_task",
                                                                             "catch_up"))
TICKET = "feat/t"
HUBS = "Hub files: `docs/table.md`, `cli.py`. Hub order: 01, 02.\n"
TABLE = "| command | what |\n| --- | --- |\n| list | lists |\n"
CLI = "from cmds import list\n\nCOMMANDS = [list]\n"
CHECK = '''import re, sys
text = open("cli.py").read()
names = re.findall(r"^from cmds import (\\w+)$", text, re.M)
registered = re.findall(r"^COMMANDS = \\[(.*)\\]$", text, re.M)[-1].replace(" ", "").split(",")
missing = [n for n in names if n not in registered]
sys.exit(f"unregistered: {missing}" if missing else 0)
'''


class UnionCase(unittest.TestCase):
    """Ticket 01 (`feat/t`, building) and its tasks 01-01 and hub task 01-02, on an origin holding the dispatcher."""

    def setUp(self):
        self.kit = kit = Kit()
        self.addCleanup(kit.close)
        self.clone = kit.make_origin()
        self.commit("main", {"docs/table.md": TABLE, "cli.py": CLI, "check.py": CHECK, "README.md": "hello\n"})
        kit.git("reset", "-q", "--hard", "origin/main", cwd=self.clone)
        kit.branch_from_main(TICKET)
        kit.init_run()
        run = kit.run_dir
        for d in ("tickets", "tasks", "worktrees"):
            (run / d).mkdir()
        (run / "tickets" / "01-t.md").write_text(
            f"# 01: T\nStatus: building\nIssue: none\nBranch: {TICKET}\nBlocked by: none\n", encoding="utf-8")
        (run / "tasks" / "01-01-a.md").write_text(
            "# 01-01: A\nStatus: in-progress\nTicket: 01\nBlocked by: none\nWrites: docs/table.md, cli.py\n"
            "PR: https://github.com/o/r/pull/5\n", encoding="utf-8")
        (run / "tasks" / "01-02-hub.md").write_text(
            "# 01-02: Hub\nStatus: ready\nTicket: 01\nBlocked by: 01, ticket 02\n", encoding="utf-8")
        self.map(HUBS)
        p = kit.step(SCRIPTS / "decide.py", run, "set", "test_command", f"{sys.executable} -I check.py")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def map(self, lanes):
        (self.kit.run_dir / "map.md").write_text(
            f"# Map\n\n## Checks\n\n```\n{sys.executable} -I check.py\n```\n\n## Lanes\n\n- Lane 1: 01, 02.\n\n{lanes}",
            encoding="utf-8")

    def commit(self, branch, files, start=None):
        """Commits `files` (path to text, appended to the file's text with `+` first) on origin's `branch`."""
        work = self.kit.tmp / "scratch"
        self.kit.git("fetch", "-q", "origin", cwd=self.clone)
        self.kit.git("worktree", "add", "-q", "--detach", str(work), f"origin/{start or branch}", cwd=self.clone)
        for name, text in files.items():
            path = work / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if text.startswith("+"):
                text = path.read_text(encoding="utf-8") + text[1:]
            path.write_text(text, encoding="utf-8")
        self.kit.git("add", "-A", cwd=work)
        self.kit.git("commit", "-qm", f"change {', '.join(files)}", cwd=work)
        self.kit.git("push", "-q", "origin", f"HEAD:refs/heads/{branch}", cwd=work)
        self.kit.git("worktree", "remove", "--force", str(work), cwd=self.clone)

    def row(self, name):
        return f"+| {name} | {name}s |\n"

    def register(self, name):
        """The dispatcher with `name` imported and registered beside `list`: the two lines git conflicts on."""
        return f"from cmds import list\nfrom cmds import {name}\n\nCOMMANDS = [list, {name}]\n"

    def line(self, p):
        self.assertEqual(p.stdout.count("\n"), 1, p.stdout + p.stderr)
        line = p.stdout.rstrip("\n")
        self.assertEqual(p.returncode, 0 if line.startswith("OK ") else 1, line)
        return line

    def read(self, where, name):
        return (Path(where) / name).read_text(encoding="utf-8")


class HubSyncTest(UnionCase):
    """`start_task.py`'s hub sync: `origin/main` merged into the hub task's new worktree."""

    def start(self):
        return self.line(self.kit.step(START_TASK, self.kit.run_dir, "01-02"))

    def test_two_tickets_appending_rows_to_a_hub_table_merge_with_both_rows(self):
        self.commit(TICKET, {"docs/table.md": self.row("add")})
        self.commit("main", {"docs/table.md": self.row("done")})
        line = self.start()
        self.assertTrue(line.startswith("OK 01-02 in-progress "), line)
        self.assertIn(" union=docs/table.md ", line)
        wt = self.kit.run_dir / "worktrees" / "01-02"
        self.assertEqual(self.read(wt, "docs/table.md"), TABLE + "| add | adds |\n| done | dones |\n")
        self.assertEqual(self.kit.git("status", "--porcelain", cwd=wt), "")
        self.assertEqual(self.kit.git("rev-list", "--count", "--merges", "origin/feat/t..HEAD", cwd=wt), "1")

    def test_a_union_leaving_an_import_unregistered_is_refused(self):
        self.commit(TICKET, {"cli.py": self.register("add")})
        self.commit("main", {"cli.py": self.register("done")})
        line = self.start()
        self.assertTrue(line.startswith("FAIL 01-02 sync-conflict cli.py (log: "), line)
        self.assertFalse((self.kit.run_dir / "worktrees" / "01-02").exists())
        self.assertEqual(runfolder.get(self.kit.run_dir / "tasks" / "01-02-hub.md", "Status"), "ready")
        log = Path(line.rsplit("(log: ", 1)[1][:-1]).read_text(encoding="utf-8")
        self.assertIn("unregistered: ['add']", log)

    def test_a_conflict_touching_a_non_hub_file_is_never_resolved(self):
        self.commit(TICKET, {"docs/table.md": self.row("add"), "README.md": "ticket\n"})
        self.commit("main", {"docs/table.md": self.row("done"), "README.md": "main\n"})
        line = self.start()
        self.assertTrue(line.startswith("FAIL 01-02 sync-conflict README.md docs/table.md (log: "), line)
        self.assertNotIn("union:", Path(line.rsplit("(log: ", 1)[1][:-1]).read_text(encoding="utf-8"))

    def test_no_hub_files_line_means_no_union(self):
        self.map("Hub order: 01, 02.\n")
        self.commit(TICKET, {"docs/table.md": self.row("add")})
        self.commit("main", {"docs/table.md": self.row("done")})
        line = self.start()
        self.assertTrue(line.startswith("FAIL 01-02 sync-conflict docs/table.md (log: "), line)


class FinishTest(UnionCase):
    """`finish_task.py`'s merge of the ticket branch into a task worktree (`taskwt.merge_upstream`)."""

    def setUp(self):
        super().setUp()
        self.wt = self.kit.run_dir / "worktrees" / "01-01"
        self.kit.git("worktree", "add", "-q", "-b", "task/t/01", str(self.wt), f"origin/{TICKET}", cwd=self.clone)

    def finish(self):
        return self.line(self.kit.step(FINISH, self.kit.run_dir, "01-01"))

    def test_both_rows_are_kept_and_the_merge_committed(self):
        self.commit(TICKET, {"docs/table.md": self.row("done")})
        (self.wt / "docs" / "table.md").write_text(TABLE + "| add | adds |\n", encoding="utf-8")
        self.assertEqual(self.finish(), "OK 01-01 committed files=1 union=docs/table.md")
        self.assertEqual(self.read(self.wt, "docs/table.md"), TABLE + "| add | adds |\n| done | dones |\n")
        self.assertEqual(self.kit.git("status", "--porcelain", cwd=self.wt), "")

    def test_a_red_union_is_left_for_the_implementer_with_no_commit(self):
        self.commit(TICKET, {"cli.py": self.register("done")})
        (self.wt / "cli.py").write_text(self.register("add"), encoding="utf-8")
        line = self.finish()
        self.assertTrue(line.startswith("FAIL 01-01 conflict cli.py (log: "), line)
        self.assertIn("<<<<<<<", self.read(self.wt, "cli.py"))
        self.assertEqual(self.kit.git("rev-parse", "-q", "--verify", "MERGE_HEAD", cwd=self.wt),
                         self.kit.git("rev-parse", f"origin/{TICKET}", cwd=self.clone))
        self.assertEqual(self.kit.git("log", "-1", "--format=%P", cwd=self.wt).count(" "), 0)


class MergeTaskTest(UnionCase):
    """`merge_task.py`'s catch-up of the ticket branch before its Checks."""

    def setUp(self):
        super().setUp()
        kit = self.kit
        (kit.bin / "gh").rename(kit.bin / "gh-kit")
        (kit.bin / "gh").write_text(PR_VIEW.format(python=sys.executable, kit_gh=str(kit.bin / "gh-kit"),
                                                   origin=str(kit.origin)), encoding="utf-8")
        (kit.bin / "gh").chmod(0o755)
        (kit.home / ".agents" / "bin").mkdir(parents=True)
        (kit.home / ".agents" / "bin" / "settings").symlink_to(REPO / "scripts" / "settings.sh")
        kit.answer("pr-list", out="[]")
        kit.answer("pr-create", out="https://github.com/o/r/pull/9\n")
        self.wt = kit.run_dir / "worktrees" / "01-01"
        kit.git("worktree", "add", "-q", "-b", "task/t/01", str(self.wt), f"origin/{TICKET}", cwd=self.clone)

    def test_the_catch_up_keeps_both_rows_and_the_task_merges(self):
        (self.wt / "docs" / "table.md").write_text(TABLE + "| add | adds |\n", encoding="utf-8")
        self.kit.git("commit", "-qam", "feat: add", cwd=self.wt)
        self.commit(TICKET, {"docs/table.md": self.row("done")})
        line = self.line(self.kit.step(MERGE_TASK, self.kit.run_dir, "01-01"))
        self.assertRegex(line, r"^OK 01-01 done suites=1 \d+s union=docs/table\.md$")
        table = self.kit.git("--git-dir", str(self.kit.origin), "show", "task/t/01:docs/table.md")
        self.assertEqual(table + "\n", TABLE + "| add | adds |\n| done | dones |\n")


class CatchUpTest(UnionCase):
    """`catch_up.py --apply`: a new base merged into a started ticket's branch."""

    def test_a_hub_conflict_with_the_new_base_is_settled_and_pushed(self):
        self.commit(TICKET, {"docs/table.md": self.row("add")})
        self.commit("int/x", {"docs/table.md": self.row("done")}, start="main")
        self.kit.answer("pr-view", out=json.dumps({"state": "OPEN", "headRefOid": "x", "baseRefName": "int/x"}))
        line = self.line(self.kit.step(CATCH_UP, self.kit.run_dir, "int/x", "--apply"))
        self.assertTrue(line.startswith("OK run caught-up base=int/x "), line)
        self.assertTrue(line.endswith(" union=01:docs/table.md"), line)
        table = self.kit.git("--git-dir", str(self.kit.origin), "show", f"{TICKET}:docs/table.md")
        self.assertEqual(table + "\n", TABLE + "| add | adds |\n| done | dones |\n")


if __name__ == "__main__":
    unittest.main()
