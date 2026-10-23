import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
FIXTURES = HERE / "fixtures"


def extract(*args):
    proc = subprocess.run(
        [sys.executable, str(HERE / "extract.py"), *args],
        capture_output=True, text=True,
    )
    return proc


def run(*args):
    proc = extract(*args)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


class ModelInvokedSkill(unittest.TestCase):
    """The model started the skill through its Skill tool; the session has no /telemetry call."""

    def setUp(self):
        self.run = run("--session", str(FIXTURES / "model-invoked.jsonl"), "--skill", "renovate-prs")

    def test_run_starts_at_the_skill_call_and_ends_with_the_session(self):
        self.assertEqual(self.run["start"], "2026-10-23T11:00:03.000Z")
        self.assertEqual(self.run["end"], "2026-10-23T11:00:30.000Z")
        self.assertEqual(self.run["wallClockSeconds"], 27)

    def test_counts_turns_and_tool_calls(self):
        self.assertEqual(self.run["turns"], 3)
        self.assertEqual(self.run["toolCalls"], 2)
        self.assertEqual(self.run["tools"], {"Skill": 1, "Bash": 1})

    def test_sums_tokens_per_model(self):
        self.assertEqual(self.run["tokensByModel"], {
            "claude-opus-5-5": {
                "input": 30, "output": 300, "cacheRead": 3000,
                "cacheWrite5m": 0, "cacheWrite1h": 600,
            },
        })


class SkillTypedTwice(unittest.TestCase):
    """/renovate-prs typed twice, each followed by /telemetry; the second pair is measured."""

    def setUp(self):
        self.run = run("--session", str(FIXTURES / "run-twice.jsonl"), "--skill", "renovate-prs")

    def test_run_starts_at_the_last_invocation(self):
        self.assertEqual(self.run["start"], "2026-10-23T10:03:00.000Z")

    def test_run_stops_at_the_last_telemetry_call(self):
        # Bash ran once in the first execution and once in the second; the reply after
        # the final /telemetry is left out.
        self.assertEqual(self.run["tools"]["Bash"], 1)
        self.assertEqual(self.run["tools"]["mcp__datadog-mcp__search_logs"], 1)
        self.assertEqual(self.run["tools"]["mcp__datadog-mcp__get_trace"], 1)

    def test_includes_the_subagent_started_in_the_run(self):
        self.assertEqual(self.run["subagents"], [{
            "type": "general-purpose",
            "description": "Check PR",
            "turns": 2,
            "toolCalls": 1,
            "tools": {"Read": 1},
            "tokensByModel": {"claude-haiku-5-5": {
                "input": 10, "output": 100, "cacheRead": 1000,
                "cacheWrite5m": 200, "cacheWrite1h": 0,
            }},
        }])

    def test_totals_add_the_subagent_to_the_main_thread(self):
        # Main thread: r4-r7, four turns, four tool calls. Subagent: two turns, one tool call.
        self.assertEqual(self.run["turns"], 6)
        self.assertEqual(self.run["toolCalls"], 5)
        self.assertEqual(self.run["tokensByModel"], {
            "claude-opus-5-5": {
                "input": 40, "output": 400, "cacheRead": 4000,
                "cacheWrite5m": 0, "cacheWrite1h": 800,
            },
            "claude-haiku-5-5": {
                "input": 10, "output": 100, "cacheRead": 1000,
                "cacheWrite5m": 200, "cacheWrite1h": 0,
            },
        })

    def test_wall_clock_waits_for_the_background_subagent(self):
        self.assertEqual(self.run["end"], "2026-10-23T10:06:30.000Z")
        self.assertEqual(self.run["wallClockSeconds"], 210)


class NoSkillNamed(unittest.TestCase):
    def test_run_starts_at_the_first_typed_message_after_the_previous_telemetry_call(self):
        # The previous /telemetry's own reply (r3) belongs to that report, not to this run.
        run_ = run("--session", str(FIXTURES / "run-twice.jsonl"))
        self.assertEqual(run_["start"], "2026-10-23T10:03:00.000Z")
        # Main thread r4-r7 plus the subagent's two.
        self.assertEqual(run_["turns"], 6)

    def test_run_covers_the_whole_session_without_an_earlier_telemetry_call(self):
        run_ = run("--session", str(FIXTURES / "model-invoked.jsonl"))
        self.assertEqual(run_["start"], "2026-10-23T11:00:00.000Z")
        self.assertEqual(run_["wallClockSeconds"], 30)


class NothingToMeasure(unittest.TestCase):
    def test_says_so_when_telemetry_runs_twice_in_a_row(self):
        lines = (FIXTURES / "run-twice.jsonl").read_text().splitlines()
        report_call = next(line for line in lines if "/telemetry</command-name>" in line)
        with tempfile.TemporaryDirectory() as folder:
            session = Path(folder) / "s.jsonl"
            session.write_text("\n".join([*lines, report_call, report_call]) + "\n")
            proc = extract("--session", str(session))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("Nothing to measure", proc.stderr)


class SkillNotFound(unittest.TestCase):
    def test_stops_and_lists_the_skills_that_did_run(self):
        proc = extract("--session", str(FIXTURES / "run-twice.jsonl"), "--skill", "deploy")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertIn("deploy", proc.stderr)
        self.assertIn("renovate-prs", proc.stderr)

    def test_leaves_built_in_commands_out_of_the_list(self):
        proc = extract("--session", str(FIXTURES / "run-twice.jsonl"), "--skill", "deploy")
        self.assertNotIn("clear", proc.stderr)


class DefaultSession(unittest.TestCase):
    """With no --session, measure the session Claude Code says is running, not the newest log."""

    def test_uses_the_session_id_from_the_environment(self):
        with tempfile.TemporaryDirectory() as home:
            project_dir = Path(home) / "work" / "app"
            project_dir.mkdir(parents=True)
            logs = Path(home) / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9-]", "-", str(project_dir.resolve()))
            logs.mkdir(parents=True)
            shutil.copy(FIXTURES / "model-invoked.jsonl", logs / "this-session.jsonl")
            shutil.copy(FIXTURES / "run-twice.jsonl", logs / "other-session.jsonl")
            os.utime(logs / "other-session.jsonl")  # the other session wrote last
            env = {**os.environ, "HOME": home, "CLAUDE_CODE_SESSION_ID": "this-session"}
            proc = subprocess.run([sys.executable, str(HERE / "extract.py"), "--cwd", str(project_dir)],
                                  capture_output=True, text=True, env=env)
            self.assertEqual(json.loads(proc.stdout)["sessionId"], "this-session", proc.stderr)


if __name__ == "__main__":
    unittest.main()
