"""Tests for `task_prompt.py`: one template for a task's prompt, here and remote."""
import shlex
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from testkit import SCRIPTS, Kit  # noqa: E402

PROMPT = SCRIPTS / "task_prompt.py"
TICKET = "# Alpha\nStatus: building\nBranch: feat/alpha\n"
TASK = "# 01-02: JSON export\nStatus: in-progress\nTicket: 01\nBlocked by: none\nWrites: a.txt\n"
TUNING = "# Tune alpha\nStatus: in-progress\nRange: abc1234..def5678\n"
TEST_COMMAND = "capped -- python -m pytest -q"


class TaskPromptTest(unittest.TestCase):
    def setUp(self):
        self.kit = Kit()
        self.addCleanup(self.kit.close)
        self.kit.make_origin()
        self.kit.init_run()
        self.run_dir = run = self.kit.run_dir
        for folder in ("tickets", "tasks", "reports", "notes"):
            (run / folder).mkdir(exist_ok=True)
        (run / "tickets" / "01-alpha.md").write_text(TICKET, encoding="utf-8")
        self.task = run / "tasks" / "01-02-json-export.md"
        self.task.write_text(TASK, encoding="utf-8")
        (run / "tasks" / "01-tests.md").write_text(TUNING, encoding="utf-8")
        for name in ("spec.md", "map.md"):
            (run / name).write_text(name + "\n", encoding="utf-8")
        (run / "decisions.md").write_text(f"# Decisions\ntest_command: {TEST_COMMAND}\n", encoding="utf-8")

    def ok(self, *args):
        """The OK line, its `key=value` fields (`args=` runs to the end of the line) and the prompt's lines."""
        p = self.kit.step(PROMPT, self.run_dir, *args)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(p.stdout.count("\n"), 1, p.stdout)
        line = p.stdout.rstrip("\n")
        head, _, rest = line.partition(" args=")
        fields = dict(f.split("=", 1) for f in head.split(" ")[3:])
        if rest:
            fields["args"] = rest
        return line, fields, Path(fields["prompt"]).read_text(encoding="utf-8").splitlines()

    def test_local_implementer_prompt_names_every_input_by_path_report_last(self):
        line, fields, lines = self.ok("01-02")
        self.assertTrue(line.startswith("OK 01-02 local prompt="), line)
        self.assertEqual(fields["agent"], "epic-implementer")
        run = self.kit.run_dir
        wt = run / "worktrees" / "01-02"
        self.assertEqual(lines, [
            "You are an epic-implementer. Follow ~/.agents/skills/build-epic/implementer-brief.md.",
            f"task: {self.task.resolve()}", f"run: {run}", f"worktree: {wt}", "ticket branch: feat/alpha",
            f"test_command: {TEST_COMMAND}", f"report: {run / 'reports' / '01-02.md'}"])
        self.assertEqual(Path(fields["prompt"]), run / "prompts" / "01-02-1.md")

    def test_remote_prompt_is_the_same_lines_with_sandbox_paths_and_attaches_what_it_names(self):
        line, fields, lines = self.ok("01-02", "--remote")
        self.assertTrue(line.startswith("OK 01-02 remote prompt="), line)
        run = self.kit.run_dir
        self.assertEqual(lines, [
            "You are an epic-implementer. Follow ~/.agents/skills/build-epic/implementer-brief.md.",
            "task: ~/attached/01-02-json-export.md", "spec: ~/attached/spec.md", "map: ~/attached/map.md",
            "notes: ~/attached/notes", "worktree: ~/work", "ticket branch: feat/alpha",
            f"test_command: {TEST_COMMAND}", "report: ~/out/report.md"])
        self.assertEqual(shlex.split(fields["args"]), [
            "--name", "01-02-json-export", "--agent", "epic-implementer",
            "--attach", str(self.task.resolve()), "--attach", str(run / "spec.md"), "--attach", str(run / "map.md"),
            "--attach", str(run / "notes"), f"@{fields['prompt']}", str(run / "worktrees" / "01-02")])

    def test_resolver_cap_and_failure_lines_come_before_the_report(self):
        log = self.kit.tmp / "red.log"
        log.write_text("FAILED test_x\n", encoding="utf-8")
        _, fields, lines = self.ok("01-02", "--remote", "--resolve", "a.txt b.txt",
                                   "--cap", "memory killed pytest (--mb 2560 --secs 300)", "--failure", str(log))
        self.assertEqual(lines[-4:], ["resolve: a.txt b.txt", "cap: memory killed pytest (--mb 2560 --secs 300)",
                                      "failure: ~/attached/red.log", "report: ~/out/report.md"])
        self.assertIn(str(log.resolve()), shlex.split(fields["args"]))
        _, _, here = self.ok("01-02", "--failure", str(log))
        self.assertEqual(here[-2], f"failure: {log.resolve()}")

    def test_a_base_merge_conflict_adds_a_sync_line_and_runs_here_only(self):
        _, _, lines = self.ok("01-02", "--sync", "a.txt b.txt")
        self.assertEqual(lines[-2:], ["sync: a.txt b.txt", f"report: {self.kit.run_dir / 'reports' / '01-02.md'}"])
        p = self.kit.step(PROMPT, self.run_dir, "01-02", "--remote", "--sync", "a.txt")
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertTrue(p.stdout.startswith("FAIL 01-02 sync-remote "), p.stdout)

    def test_reissue_keeps_the_broken_report_where_the_next_run_cannot_overwrite_it(self):
        report = self.kit.run_dir / "reports" / "01-02.md"
        report.write_text("Built it.\nstatus: done\nchanged: a.txt\n", encoding="utf-8")
        self.ok("01-02")
        _, fields, lines = self.ok("01-02", "--reissue")
        kept = self.kit.run_dir / "prompts" / "01-02-2-report.md"
        self.assertEqual(Path(fields["prompt"]).name, "01-02-2.md")
        self.assertEqual(lines[-2], f"reissue: {kept}")
        report.write_text("status: done\n", encoding="utf-8")
        self.assertEqual(kept.read_text(encoding="utf-8"), "Built it.\nstatus: done\nchanged: a.txt\n")

    def test_bad_options_and_missing_files_fail_and_write_nothing(self):
        for args, prefix in ((["--resolve"], "FAIL 01-02 usage: "), (["--sync"], "FAIL 01-02 usage: "), (["--bogus"], "FAIL 01-02 usage: "),
                             (["--failure", "/nowhere.log"], "FAIL 01-02 no-failure-file: "),
                             (["--reissue"], "FAIL 01-02 no-report: ")):
            p = self.kit.step(PROMPT, self.run_dir, "01-02", *args)
            self.assertEqual(p.returncode, 1, p.stdout)
            self.assertTrue(p.stdout.startswith(prefix), p.stdout)
        self.assertEqual(list((self.kit.run_dir / "prompts").glob("01-02-*.md")), [])


if __name__ == "__main__":
    unittest.main()
