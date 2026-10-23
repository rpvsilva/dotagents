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

OPUS_TURN = {"input": 10, "output": 100, "cacheRead": 1000, "cacheWrite5m": 0, "cacheWrite1h": 200}
HAIKU_TURN = {"input": 5, "output": 50, "cacheRead": 500, "cacheWrite5m": 100, "cacheWrite1h": 0}


def times(turn, n):
    return {kind: value * n for kind, value in turn.items()}


def extract(*args):
    return subprocess.run(
        [sys.executable, str(HERE / "extract.py"), *args],
        capture_output=True, text=True,
    )


def run(*args):
    proc = extract(*args)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def by_name(run_):
    return {span["name"]: span for span in run_["spans"]}


class Spans(unittest.TestCase):
    """spans.jsonl: chat, /grill-with-docs (loads grilling), /model, /renovate-prs twice, /telemetry."""

    def setUp(self):
        self.run = run("--session", str(FIXTURES / "spans.jsonl"))
        self.spans = by_name(self.run)

    def test_each_typed_skill_starts_a_span_in_order(self):
        # /model is a built-in command and loads no skill, so it starts no span.
        self.assertEqual([s["name"] for s in self.run["spans"]],
                         ["no skill", "grill-with-docs", "renovate-prs", "renovate-prs-2"])

    def test_activity_before_any_skill_is_its_own_span(self):
        span = self.spans["no skill"]
        self.assertIsNone(span["skill"])
        self.assertEqual((span["turns"], span["toolCalls"], span["wallClockSeconds"]), (1, 0, 2))

    def test_skills_loaded_inside_a_span_count_toward_it(self):
        # q2 (Skill: grilling), q3 and q4 (Bash); 09:01:00 to the /model line at 09:03:00.
        span = self.spans["grill-with-docs"]
        self.assertEqual(span["skill"], "grill-with-docs")
        self.assertEqual(span["turns"], 3)
        self.assertEqual(span["tools"], {"Skill": 1, "Bash": 1})
        self.assertEqual(span["wallClockSeconds"], 120)
        self.assertEqual(span["tokensByModel"], {"claude-opus-5-5": times(OPUS_TURN, 3)})

    def test_a_subagent_belongs_to_the_span_that_started_it(self):
        # q5, q6 plus the subagent's t1, t2; the background subagent ends at 09:06:00.
        span = self.spans["renovate-prs"]
        self.assertEqual(span["turns"], 4)
        self.assertEqual(span["toolCalls"], 2)
        self.assertEqual(span["wallClockSeconds"], 120)
        self.assertEqual(span["subagents"], [{
            "type": "general-purpose", "description": "Fix PR", "turns": 2, "toolCalls": 1,
            "tools": {"Read": 1}, "tokensByModel": {"claude-haiku-5-5": times(HAIKU_TURN, 2)},
        }])
        self.assertEqual(self.spans["renovate-prs-2"]["subagents"], [])

    def test_the_same_skill_typed_again_starts_a_numbered_span(self):
        span = self.spans["renovate-prs-2"]
        self.assertEqual(span["skill"], "renovate-prs")
        self.assertEqual((span["start"], span["turns"], span["wallClockSeconds"]),
                         ("2026-10-23T09:05:00.000Z", 1, 4))

    def test_the_run_lasts_from_the_first_span_to_whatever_ends_last(self):
        self.assertEqual(self.run["start"], "2026-10-23T09:00:00.000Z")
        self.assertEqual(self.run["end"], "2026-10-23T09:06:00.000Z")
        self.assertEqual(self.run["wallClockSeconds"], 360)


class SkillNamed(unittest.TestCase):
    def test_narrows_the_report_to_that_skills_last_span(self):
        run_ = run("--session", str(FIXTURES / "spans.jsonl"), "--skill", "renovate-prs")
        self.assertEqual([s["start"] for s in run_["spans"]], ["2026-10-23T09:05:00.000Z"])
        self.assertEqual(run_["spans"][0]["name"], "renovate-prs")
        self.assertEqual(run_["wallClockSeconds"], 4)

    def test_finds_the_skill_before_an_earlier_telemetry_call(self):
        # run-twice.jsonl: /renovate-prs, /telemetry, /renovate-prs, /telemetry.
        run_ = run("--session", str(FIXTURES / "run-twice.jsonl"), "--skill", "renovate-prs")
        span = run_["spans"][0]
        self.assertEqual(span["start"], "2026-10-23T10:03:00.000Z")
        # The reply after the final /telemetry is left out; r4-r7 plus the subagent's two.
        self.assertEqual(span["turns"], 6)
        self.assertEqual(span["tools"], {"mcp__datadog-mcp__search_logs": 1, "Agent": 1, "Bash": 1,
                                         "mcp__datadog-mcp__get_trace": 1, "Read": 1})

    def test_merges_log_lines_of_one_api_call(self):
        # r4 and r6 are each split over two lines; usage is counted once per call.
        span = run("--session", str(FIXTURES / "run-twice.jsonl"), "--skill", "renovate-prs")["spans"][0]
        self.assertEqual(span["tokensByModel"]["claude-opus-5-5"], times(OPUS_TURN, 4))

    def test_wall_clock_waits_for_the_background_subagent(self):
        run_ = run("--session", str(FIXTURES / "run-twice.jsonl"), "--skill", "renovate-prs")
        self.assertEqual(run_["end"], "2026-10-23T10:06:30.000Z")
        self.assertEqual(run_["wallClockSeconds"], 210)

    def test_stops_and_lists_the_skills_that_did_run(self):
        proc = extract("--session", str(FIXTURES / "spans.jsonl"), "--skill", "deploy")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertIn("Skills that ran: grill-with-docs, renovate-prs", proc.stderr)


class NoSkillNamed(unittest.TestCase):
    def test_starts_at_the_first_typed_message_after_the_previous_telemetry_call(self):
        # The previous /telemetry's own reply (r3) belongs to that report, not to this run.
        run_ = run("--session", str(FIXTURES / "run-twice.jsonl"))
        self.assertEqual(run_["start"], "2026-10-23T10:03:00.000Z")
        self.assertEqual([(s["name"], s["turns"]) for s in run_["spans"]], [("renovate-prs", 6)])

    def test_a_skill_the_model_started_outside_any_span_starts_one(self):
        run_ = run("--session", str(FIXTURES / "model-invoked.jsonl"))
        # "update deps" alone made no turns, so the empty "no skill" span is left out.
        self.assertEqual([s["name"] for s in run_["spans"]], ["renovate-prs"])
        span = run_["spans"][0]
        self.assertEqual((span["start"], span["wallClockSeconds"], span["turns"], span["toolCalls"]),
                         ("2026-10-23T11:00:03.000Z", 27, 3, 2))
        self.assertEqual(run_["start"], "2026-10-23T11:00:00.000Z")
        self.assertEqual(run_["wallClockSeconds"], 30)

    def test_says_so_when_telemetry_runs_twice_in_a_row(self):
        lines = (FIXTURES / "run-twice.jsonl").read_text().splitlines()
        report_call = next(line for line in lines if "/telemetry</command-name>" in line)
        with tempfile.TemporaryDirectory() as folder:
            session = Path(folder) / "s.jsonl"
            session.write_text("\n".join([*lines, report_call, report_call]) + "\n")
            proc = extract("--session", str(session))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("Nothing to measure", proc.stderr)


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
