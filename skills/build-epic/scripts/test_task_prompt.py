"""Tests for `task_prompt.py`: one prompt for a task's agent, here and remote: the `implementer` role's input JSON."""
import json
import shlex
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from testkit import SCRIPTS, Kit  # noqa: E402

PROMPT = SCRIPTS / "task_prompt.py"
CHECK = SCRIPTS.parents[1] / "_shared" / "protocol" / "check.py"
FIRST = "You are an epic-implementer. Follow ~/.agents/skills/build-epic/implementer-brief.md."
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
        """The OK line, its `key=value` fields (`args=` runs to the end of the line), the prompt's first line and inputs."""
        p = self.kit.step(PROMPT, self.run_dir, *args)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(p.stdout.count("\n"), 1, p.stdout)
        line = p.stdout.rstrip("\n")
        head, _, rest = line.partition(" args=")
        fields = dict(f.split("=", 1) for f in head.split(" ")[3:])
        if rest:
            fields["args"] = rest
        first, _, inputs = Path(fields["prompt"]).read_text(encoding="utf-8").partition("\n")
        self.assertEqual(first, FIRST)
        return line, fields, json.loads(inputs)

    def test_local_implementer_prompt_names_every_input_by_path(self):
        line, fields, inputs = self.ok("01-02")
        self.assertTrue(line.startswith("OK 01-02 local prompt="), line)
        self.assertEqual(fields["agent"], "epic-implementer")
        run = self.kit.run_dir
        self.assertEqual(inputs, {
            "task": str(self.task.resolve()), "run": str(run), "worktree": str(run / "worktrees" / "01-02"),
            "ticket_branch": "feat/alpha", "test_command": TEST_COMMAND,
            "output": str(run / "reports" / "01-02.json")})
        self.assertEqual(Path(fields["prompt"]), run / "prompts" / "01-02-1.md")
        check = subprocess.run([sys.executable, "-I", str(CHECK), "implementer", "input", fields["prompt"]],
                               capture_output=True, text=True)
        self.assertEqual(check.stdout, "OK implementer input\n")

    def test_remote_prompt_names_sandbox_paths_no_output_and_attaches_what_it_names(self):
        line, fields, inputs = self.ok("01-02", "--remote")
        self.assertTrue(line.startswith("OK 01-02 remote prompt="), line)
        run = self.kit.run_dir
        self.assertEqual(inputs, {
            "task": "~/attached/01-02-json-export.md", "remote": True, "spec": "~/attached/spec.md",
            "map": "~/attached/map.md", "notes": "~/attached/notes", "worktree": "~/work",
            "ticket_branch": "feat/alpha", "test_command": TEST_COMMAND})
        self.assertEqual(shlex.split(fields["args"]), [
            "--name", "01-02-json-export", "--agent", "epic-implementer",
            "--attach", str(self.task.resolve()), "--attach", str(run / "spec.md"), "--attach", str(run / "map.md"),
            "--attach", str(run / "notes"), f"@{fields['prompt']}", str(run / "worktrees" / "01-02")])

    def test_a_remote_prompt_gets_the_remote_test_command_when_setup_recorded_one(self):
        with open(self.run_dir / "decisions.md", "a", encoding="utf-8") as f:
            f.write("remote_test_command: make-env-then-test\n")
        _, _, remote = self.ok("01-02", "--remote")
        self.assertEqual(remote["test_command"], "make-env-then-test")
        _, _, local = self.ok("01-02")
        self.assertEqual(local["test_command"], TEST_COMMAND)

    def test_resolver_cap_and_failure_inputs(self):
        log = self.kit.tmp / "red.log"
        log.write_text("FAILED test_x\n", encoding="utf-8")
        _, fields, inputs = self.ok("01-02", "--remote", "--resolve", "a.txt b.txt",
                                    "--cap", "memory killed pytest (--mb 2560 --secs 300)", "--failure", str(log))
        self.assertEqual((inputs["resolve"], inputs["cap"], inputs["failure"]),
                         (["a.txt", "b.txt"], "memory killed pytest (--mb 2560 --secs 300)", "~/attached/red.log"))
        self.assertIn(str(log.resolve()), shlex.split(fields["args"]))
        _, _, here = self.ok("01-02", "--failure", str(log))
        self.assertEqual(here["failure"], str(log.resolve()))

    def test_a_base_merge_conflict_adds_sync_and_runs_here_only(self):
        _, _, inputs = self.ok("01-02", "--sync", "a.txt,b.txt")
        self.assertEqual(inputs["sync"], ["a.txt", "b.txt"])
        p = self.kit.step(PROMPT, self.run_dir, "01-02", "--remote", "--sync", "a.txt")
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertTrue(p.stdout.startswith("FAIL 01-02 sync-remote "), p.stdout)

    def test_reissue_keeps_the_broken_output_where_the_next_run_cannot_overwrite_it(self):
        output = self.kit.run_dir / "reports" / "01-02.json"
        output.write_text("status: done\nchanged: a.txt\n", encoding="utf-8")
        self.ok("01-02")
        _, fields, inputs = self.ok("01-02", "--reissue")
        kept = self.kit.run_dir / "prompts" / "01-02-2-output.json"
        self.assertEqual(Path(fields["prompt"]).name, "01-02-2.md")
        self.assertEqual(inputs["reissue"], str(kept))
        output.write_text("{}", encoding="utf-8")
        self.assertEqual(kept.read_text(encoding="utf-8"), "status: done\nchanged: a.txt\n")

    def test_bad_options_and_missing_files_fail_and_write_nothing(self):
        for args, prefix in ((["--resolve"], "FAIL 01-02 usage: "), (["--sync"], "FAIL 01-02 usage: "), (["--bogus"], "FAIL 01-02 usage: "),
                             (["--failure", "/nowhere.log"], "FAIL 01-02 no-failure-file: "),
                             (["--reissue"], "FAIL 01-02 no-output: ")):
            p = self.kit.step(PROMPT, self.run_dir, "01-02", *args)
            self.assertEqual(p.returncode, 1, p.stdout)
            self.assertTrue(p.stdout.startswith(prefix), p.stdout)
        self.assertEqual(list((self.kit.run_dir / "prompts").glob("01-02-*.md")), [])


if __name__ == "__main__":
    unittest.main()
