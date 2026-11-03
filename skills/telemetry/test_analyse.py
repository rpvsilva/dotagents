import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import analyse

HERE = Path(__file__).parent
STEPS_FIXTURE = HERE / "fixtures" / "run-steps.json"
SECTIONS = ("Slowest", "Failing", "Repeated", "Retries")


def span(skill):
    return {"name": (skill or "no skill").split(":")[-1], "skill": skill, "start": "", "end": "",
            "wallClockSeconds": 60, "turns": 1, "toolCalls": 1, "tools": {},
            "tokensByModel": {}, "subagents": []}


def step(tool, label, seconds=1.0, failed=False, skill="deploy", subagent=None, **extra):
    return {"tool": tool, "label": label, "start": "", "seconds": seconds, "failed": failed,
            "span": (skill or "no skill").split(":")[-1], "skill": skill, "subagent": subagent, **extra}


def run(session, *steps, skills=("deploy",), day="2026-10-01"):
    out = {"schemaVersion": 3, "sessionId": session, "whole": True, "date": day,
           "start": f"{day}T10:00:00.000Z", "spans": [span(s) for s in skills]}
    if steps != (None,):
        out["steps"] = [{**s, "start": f"{day}T10:00:{i:02d}.000Z"} for i, s in enumerate(steps)]
    return out


class Analyse(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def save(self, *runs):
        for r in runs:
            (self.dir / f"{r['sessionId']}.json").write_text(json.dumps(r), encoding="utf-8")

    def analyse(self, *args):
        """Run analyse.py's main in this process (a fresh interpreter per case costs ~60 ms)."""
        argv = ["analyse.py", "--dir", str(self.dir), *args]
        out = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")  # main reconfigures stdout
        err = io.StringIO()
        code = 0
        with mock.patch.object(sys, "argv", argv), redirect_stdout(out), redirect_stderr(err):
            try:
                analyse.main()
            except SystemExit as e:
                code = e.code
            out.flush()
        return subprocess.CompletedProcess(argv, code, out.buffer.getvalue().decode("utf-8"), err.getvalue())

    def ok(self, *args):
        proc = self.analyse(*args)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def section(self, out, name):
        """The body rows of the section's table as lists of cells; None when it says `none`."""
        lines = out.splitlines()
        start = lines.index(name)
        if lines[start + 1] == "none":
            return None
        found = []
        for line in lines[start + 1:]:
            if not line.startswith(("│", "┌", "├", "└")):
                break
            if line.startswith("│"):
                found.append([c.strip() for c in line.strip("│").split("│")])
        return found[1:]

    # Slowest

    def test_slowest_ranks_groups_by_median_seconds(self):
        self.save(run("a", step("Bash", "npm test", 10), step("Bash", "npm test", 30), step("Read", "a.py", 0.5)),
                  run("b", step("Bash", "npm test", 20), step("Bash", "make", 40)))
        rows = self.section(self.ok("deploy"), "Slowest")
        self.assertEqual([r[:3] for r in rows], [["deploy", "Bash", "make"], ["deploy", "Bash", "npm test"],
                                                 ["deploy", "Read", "a.py"]])
        self.assertEqual(rows[1][3:], ["20.0 s", "30.0 s", "3", "2"])

    def test_slowest_keeps_only_the_top_n(self):
        self.save(run("a", *(step("Bash", f"cmd {i}", i) for i in range(1, 15))))
        self.assertEqual(len(self.section(self.ok("deploy"), "Slowest")), 10)
        rows = self.section(self.ok("deploy", "--top", "2"), "Slowest")
        self.assertEqual([r[2] for r in rows], ["cmd 14", "cmd 13"])

    def test_slowest_leaves_out_questions_agents_background_and_unfinished_steps(self):
        self.save(run("a", step("AskUserQuestion", "", 99), step("Agent", "Explore: look", 98),
                      step("Bash", "npm run dev", 97, background=True), step("Bash", "gh pr merge 1", None),
                      step("Read", "a.py", 0.5)))
        rows = self.section(self.ok("deploy"), "Slowest")
        self.assertEqual([r[1:3] for r in rows], [["Read", "a.py"]])

    def test_slowest_shows_minutes_past_two_minutes_and_two_decimals_under_a_second(self):
        self.save(run("a", step("Bash", "build", 125), step("Bash", "lint", 1.5), step("Read", "a.py", 0.25)))
        rows = self.section(self.ok("deploy"), "Slowest")
        self.assertEqual([r[3] for r in rows], ["2m 05s", "1.5 s", "0.25 s"])

    def test_slowest_breaks_a_median_tie_by_the_max(self):
        self.save(run("a", step("Bash", "a", 5), step("Bash", "a", 5), step("Bash", "a", 6),
                      step("Bash", "b", 5), step("Bash", "b", 5), step("Bash", "b", 9)))
        rows = self.section(self.ok("deploy"), "Slowest")
        self.assertEqual([r[2:5] for r in rows], [["b", "5.0 s", "9.0 s"], ["a", "5.0 s", "6.0 s"]])

    # Failing

    def test_failing_shows_failed_over_total_and_runs(self):
        self.save(run("a", step("Bash", "npm test", failed=True), step("Bash", "npm test")),
                  run("b", step("Bash", "npm test", failed=True), step("Read", "a.py")),
                  run("c", step("Bash", "npm test")))
        rows = self.section(self.ok("deploy"), "Failing")
        self.assertEqual(rows, [["deploy", "Bash", "npm test", "2/4", "2"]])

    def test_failing_ranks_by_failures_then_by_share_failed(self):
        self.save(run("a", step("Bash", "often", failed=True), step("Bash", "often", failed=True),
                      *(step("Bash", "often") for _ in range(4)),
                      step("Bash", "most", failed=True), step("Bash", "half", failed=True), step("Bash", "half")))
        rows = self.section(self.ok("deploy"), "Failing")
        self.assertEqual([r[2:4] for r in rows], [["often", "2/6"], ["most", "1/1"], ["half", "1/2"]])

    def test_failing_is_none_without_failures(self):
        self.save(run("a", step("Bash", "ls")))
        self.assertIsNone(self.section(self.ok("deploy"), "Failing"))

    # Repeated

    def test_repeated_shows_a_step_run_twice_within_one_run(self):
        self.save(run("a", step("Read", "a.py"), step("Read", "a.py"), step("Read", "a.py"), step("Read", "b.py")),
                  run("b", step("Read", "a.py"), step("Read", "a.py"), step("Read", "b.py")),
                  run("c", step("Read", "b.py")))
        rows = self.section(self.ok("deploy"), "Repeated")
        self.assertEqual(rows, [["deploy", "Read", "a.py", "3, 2", "2"]])

    def test_the_same_step_once_in_each_of_two_runs_is_not_repeated(self):
        self.save(run("a", step("Read", "a.py")), run("b", step("Read", "a.py")))
        self.assertIsNone(self.section(self.ok("deploy"), "Repeated"))

    def test_repeated_leaves_out_questions(self):
        self.save(run("a", step("AskUserQuestion", ""), step("AskUserQuestion", "")))
        self.assertIsNone(self.section(self.ok("deploy"), "Repeated"))

    # Retries

    def test_retries_find_a_failed_step_then_a_near_identical_one(self):
        self.save(run("a", step("Bash", "npm test -- --ci", failed=True), step("Read", "x"),
                      step("Bash", "npm test -- --ci -v")),
                  run("b", step("Bash", "npm test -- --ci", failed=True), step("Bash", "npm test -- --ci")))
        rows = self.section(self.ok("deploy"), "Retries")
        self.assertEqual(rows, [["deploy", "Bash", "npm test -- --ci", "2", "2"]])

    def test_a_different_label_after_a_failure_is_not_a_retry(self):
        self.save(run("a", step("Bash", "npm test", failed=True), step("Bash", "git log --oneline -20")))
        self.assertIsNone(self.section(self.ok("deploy"), "Retries"))

    def test_a_retry_must_come_within_the_next_three_steps_of_the_same_where(self):
        self.save(run("a", step("Bash", "npm test", failed=True),
                      step("Read", "1"), step("Read", "2"), step("Read", "3"), step("Bash", "npm test")))
        self.assertIsNone(self.section(self.ok("deploy"), "Retries"))
        self.save(run("a", step("Bash", "npm test", failed=True),
                      *(step("Read", str(i), subagent="Explore") for i in range(5)),
                      step("Read", "1"), step("Read", "2"), step("Bash", "npm test")))
        self.assertEqual(self.section(self.ok("deploy"), "Retries"), [["deploy", "Bash", "npm test", "1", "1"]])

    def test_the_same_tool_is_needed_for_a_retry(self):
        self.save(run("a", step("Bash", "a.py", failed=True), step("Read", "a.py")))
        self.assertIsNone(self.section(self.ok("deploy"), "Retries"))

    # Where

    def test_steps_without_a_label_are_not_retries(self):
        self.save(run("a", step("mcp__x__call", "", failed=True), step("mcp__x__call", "")))
        self.assertIsNone(self.section(self.ok("deploy"), "Retries"))

    def test_findings_inside_a_subagent_name_the_subagent(self):
        self.save(run("a", step("Bash", "npm test", 5, failed=True, subagent="Explore"),
                      step("Bash", "npm test", 5, subagent="Explore")))
        out = self.ok("deploy")
        for name in SECTIONS:
            self.assertEqual(self.section(out, name)[0][0], "Explore", name)

    def test_steps_of_other_skills_are_left_out_and_a_plugin_skill_matches_by_short_name(self):
        self.save(run("a", step("Bash", "deploy it", skill="plug:deploy"), step("Bash", "review it", skill="review"),
                      skills=("plug:deploy", "review")))
        out = self.ok("deploy")
        self.assertEqual([r[:3] for r in self.section(out, "Slowest")], [["deploy", "Bash", "deploy it"]])
        self.assertNotIn("review it", out)

    def test_the_fixture_run(self):
        shutil.copy(STEPS_FIXTURE, self.dir / "run.json")
        out = self.ok("renovate-prs")
        self.assertEqual([r[1:3] for r in self.section(out, "Slowest")],
                         [["Bash", "gh pr list --author app/renovate"],
                          ["Glob", "**/package-lock.json"], ["Read", "/home/dev/projects/shop/package-lock.json"]])
        self.assertEqual(self.section(out, "Slowest")[1][0], "Explore")
        self.assertEqual(self.section(self.ok("grill-with-docs"), "Failing"),
                         [["grill-with-docs", "Bash", "npm test", "1/1", "1"]])

    # Runs that cannot be analysed

    def test_runs_without_steps_are_skipped_and_counted(self):
        self.save(run("a", step("Bash", "ls")), run("b", None), run("c", None))
        proc = self.analyse("deploy")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Skipped 2 saved runs without steps.", proc.stdout)

    def test_only_runs_without_steps_still_exit_0_with_every_section_none(self):
        self.save(run("b", None))
        out = self.ok("deploy")
        for name in SECTIONS:
            self.assertIsNone(self.section(out, name), name)
        self.assertIn("Skipped 1 saved run without steps.", out)

    def test_no_run_of_the_skill_says_so_on_stderr_and_exits_2(self):
        self.save(run("a", step("Bash", "ls")))
        proc = self.analyse("review")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertIn("No saved run of review", proc.stderr)

    def test_no_run_of_the_skill_still_names_the_skipped_files_on_stderr(self):
        (self.dir / "bad.json").write_text("{", encoding="utf-8")
        proc = self.analyse("deploy")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("Skipped 1 saved file", proc.stderr)

    def test_dir_can_be_given_twice(self):
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        self.save(run("a", step("Bash", "here")))
        (Path(other.name) / "b.json").write_text(json.dumps(run("b", step("Bash", "there"))), encoding="utf-8")
        out = self.ok("--dir", other.name, "deploy")
        self.assertIn("across 2 saved runs", out)
        self.assertEqual(sorted(r[2] for r in self.section(out, "Slowest")), ["here", "there"])

    def test_reports_saved_files_that_could_not_be_read(self):
        self.save(run("a", step("Bash", "ls")))
        (self.dir / "bad.json").write_text("{", encoding="utf-8")
        self.assertIn("Skipped 1 saved file", self.ok("deploy"))

    def test_malformed_steps_are_ignored_not_a_crash(self):
        bad = run("a", step("Bash", "ls"))
        bad["steps"] += ["oops", {"tool": "Bash"}, step("Bash", 3), step("Bash", "x", seconds="slow")]
        self.save(bad)
        rows = self.section(self.ok("deploy"), "Slowest")
        self.assertEqual([r[2] for r in rows], ["ls"])

    def test_the_script_runs_as_a_command(self):
        self.save(run("a", step("Bash", "ls")))
        proc = subprocess.run([sys.executable, str(HERE / "analyse.py"), "deploy", "--dir", str(self.dir)],
                              capture_output=True, encoding="utf-8")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.section(proc.stdout, "Slowest")[0][2], "ls")


if __name__ == "__main__":
    unittest.main()
