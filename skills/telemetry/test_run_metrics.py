"""Tests for `run_metrics.py`: a run's outcome metrics from its run folder and a stub `gh`."""
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "run_metrics.py"
FIXTURE = HERE / "fixtures" / "run-folder"

# Answers `pr view <PR> --json ...` from `<answers>/<PR number>.json`, logging each call's argv; no file, exit 1.
STUB = """import json, pathlib, sys
answers, log = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
args = sys.argv[3:]
with log.open("a") as f:
    f.write(json.dumps(args) + "\\n")
answer = answers / (args[2].rstrip("/").rsplit("/", 1)[-1] + ".json")
if args[:2] != ["pr", "view"] or not answer.is_file():
    sys.exit(1)
sys.stdout.write(answer.read_text(encoding="utf-8"))
"""


def sandbox(tmp):
    """(gh, calls log, env) in `tmp`: the stub `gh` command, and a `gh` on PATH that only leaves `path-gh-ran`."""
    (tmp / "gh_stub.py").write_text(STUB, encoding="utf-8")
    calls = tmp / "calls.log"
    gh = shlex.join([sys.executable, "-I", str(tmp / "gh_stub.py"), str(FIXTURE / "gh"), str(calls)])
    bin_ = tmp / "bin"
    bin_.mkdir()
    (bin_ / "gh").write_text(f"#!/bin/sh\ntouch {shlex.quote(str(tmp / 'path-gh-ran'))}\nexit 1\n", encoding="utf-8")
    (bin_ / "gh").chmod(0o755)
    return gh, calls, {**os.environ, "PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}"}


def run_metrics(env, cwd, run, *extra):
    return subprocess.run([sys.executable, "-I", str(SCRIPT), str(run), *extra], capture_output=True, text=True,
                          encoding="utf-8", env=env, cwd=str(cwd), stdin=subprocess.DEVNULL)


class FixtureRun(unittest.TestCase):
    """The committed fixture run folder through the stub `gh`, measured once for every case."""

    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.tmp = Path(tmp.name)
        gh, calls, env = sandbox(cls.tmp)
        res = run_metrics(env, cls.tmp, FIXTURE / "run", "--gh", gh)
        assert res.returncode == 0, res.stderr
        cls.got = json.loads(res.stdout)
        cls.calls = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]

    def test_fixture_run_folder_gives_the_expected_metrics(self):
        expected = json.loads((FIXTURE / "expected.json").read_text(encoding="utf-8"))
        self.assertEqual(self.got, expected)

    def test_pr_times_and_per_ticket_minutes_come_from_github(self):
        got = self.got
        prs = got["prs"]
        self.assertEqual((prs["first_created"], prs["last_merged"], prs["wall_minutes_first_pr_to_last_merge"]),
                         ("2026-11-01T10:00:00+00:00", "2026-11-01T16:00:00+00:00", 360.0))
        # Ticket 01: its first task PR (#10) opened at 10:00, the ticket PR (#20) merged at 16:00.
        self.assertEqual(got["per_ticket"]["01"], {"tasks": 4, "fix_tasks": 1, "pr": 20, "state": "MERGED",
                                                   "minutes_first_task_pr_to_ticket_merge": 360.0})
        self.assertEqual(got["per_ticket"]["02"]["minutes_first_task_pr_to_ticket_merge"], None)
        self.assertEqual(got["per_ticket"]["03"], {"tasks": 0, "fix_tasks": 0, "pr": None, "state": None,
                                                   "minutes_first_task_pr_to_ticket_merge": None})

    def test_github_is_read_only_through_the_given_command(self):
        self.assertFalse((self.tmp / "path-gh-ran").exists(), "the gh on PATH ran")
        calls = self.calls
        self.assertEqual(sorted(c[2].rsplit("/", 1)[-1] for c in calls), ["10", "11", "12", "13", "20", "21"])
        self.assertTrue(all(c[:2] == ["pr", "view"] and c[3] == "--json" for c in calls), calls)

    def test_round_trips_are_the_quotes_recorded_after_approval(self):
        # Four dated quotes (one spanning two lines): the first recorded before the clauses, the second in the
        # approval reply right after them; the other two later in the run.
        self.assertEqual(self.got["human_round_trips"], 2)

    def test_hand_written_scripts_skip_env_worktrees_and_remote(self):
        # poll.sh and logs/tally.py; env/, worktrees/ and remote/ hold a .py each that does not count.
        self.assertEqual(self.got["hand_written_scripts"], 2)


class RunMetrics(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.gh, self.calls, self.env = sandbox(self.tmp)

    def metrics(self, run, *extra):
        res = run_metrics(self.env, self.tmp, run, *extra)
        self.assertEqual(res.returncode, 0, res.stderr)
        return json.loads(res.stdout)

    @unittest.skipIf(os.name == "nt", "a #! stub cannot be started as `gh` on Windows; there the real gh is gh.exe")
    def test_without_gh_flag_the_gh_on_path_is_the_command(self):
        got = self.metrics(FIXTURE / "run")
        self.assertTrue((self.tmp / "path-gh-ran").exists())
        self.assertEqual(sum(1 for g in got["gaps"] if g.startswith("github: ")), 6)

    def test_an_empty_run_folder_names_its_gaps(self):
        (self.tmp / "empty").mkdir()
        got = self.metrics(self.tmp / "empty", "--gh", self.gh)
        self.assertEqual(got["tickets"]["count"], 0)
        self.assertEqual(got["remote"]["runs"], 0)
        self.assertEqual(got["gaps"], ["plan: no tickets/*.md or tasks/*.md", "suite results: no reports/*.checks.json",
                                       "human round trips: no decisions.md"])
        self.assertFalse(self.calls.exists())

    def test_a_run_without_decisions_has_no_round_trips_to_count(self):
        run = self.tmp / "nodecisions"
        shutil.copytree(FIXTURE / "run", run)
        (run / "decisions.md").unlink()
        got = self.metrics(run, "--gh", self.gh)
        self.assertIsNone(got["human_round_trips"])
        self.assertIn("human round trips: no decisions.md", got["gaps"])

    def test_quotes_before_any_approval_are_not_round_trips(self):
        run = self.tmp / "unapproved"
        run.mkdir()
        (run / "decisions.md").write_text("# Decisions\nbase: main\n\n## Authorizations\n\n| clause | waived | quote |\n"
                                          "| --- | --- | --- |\n\n## Decisions\n\n- 2026-11-01: install it\n",
                                          encoding="utf-8")
        self.assertEqual(self.metrics(run, "--gh", self.gh)["human_round_trips"], 0)

    def test_approval_without_the_decide_log_cannot_be_placed(self):
        run = self.tmp / "nolog"
        shutil.copytree(FIXTURE / "run", run)
        (run / "logs" / "decisions-decide.log").unlink()
        got = self.metrics(run, "--gh", self.gh)
        self.assertIsNone(got["human_round_trips"])
        self.assertIn("human round trips: no logs/decisions-decide.log to place the approval", got["gaps"])

    def approved_run(self, name, log, quotes):
        """A run folder with one Authorizations row, `quotes` dated Decisions and `log` as its decide log."""
        run = self.tmp / name
        (run / "logs").mkdir(parents=True)
        lines = "".join(f"- 2026-11-01: {q}\n" for q in quotes)
        (run / "decisions.md").write_text("# Decisions\nbase: main\n\n## Authorizations\n\n| clause | waived | quote |\n"
                                          "| --- | --- | --- |\n| Merge | yes | Rui: \"go\" |\n\n## Decisions\n\n" + lines,
                                          encoding="utf-8")
        (run / "logs" / "decisions-decide.log").write_text(log, encoding="utf-8")
        return run

    CLAUSES = ("== decide decisions clause Merge yes Rui: \"go\" at 2026-11-01T09:30:00\nOK decisions clause waived=yes\n"
               "== decide decisions clause Approve no Rui: \"mine\" at 2026-11-01T09:30:20\nOK decisions clause waived=no\n")

    def test_quotes_from_the_approval_reply_after_its_clauses_are_not_round_trips(self):
        log = self.CLAUSES + ("== decide decisions quote run 02 first at 2026-11-01T09:31:00\nOK decisions quote 2026-11-01\n"
                              "== decide decisions quote skip docs,\n  they are fine at 2026-11-01T09:31:30\n"
                              "OK decisions quote 2026-11-01\n")
        run = self.approved_run("approval-quotes", log, ["run 02 first", "skip docs,\n  they are fine"])
        self.assertEqual(self.metrics(run, "--gh", self.gh)["human_round_trips"], 0)

    def test_a_quote_logged_at_a_later_separate_step_is_a_round_trip(self):
        log = self.CLAUSES + ("== decide decisions quote run 02 first at 2026-11-01T09:31:00\nOK decisions quote 2026-11-01\n"
                              "== decide decisions quote rebase 02 at 2026-11-01T13:00:00\nOK decisions quote 2026-11-01\n")
        run = self.approved_run("later-quote", log, ["run 02 first", "rebase 02"])
        self.assertEqual(self.metrics(run, "--gh", self.gh)["human_round_trips"], 1)

    def test_a_run_killed_for_memory_then_timeout_counts_as_a_memory_kill(self):
        run = self.tmp / "kills"
        (run / "remote").mkdir(parents=True)
        (run / "remote" / "01-01.json").write_text(json.dumps({
            "status": "failed", "total_cost_usd": 1.0, "killed": "timeout",
            "capped_kills": [{"kind": "memory", "cmd": "pytest"}, {"kind": "timeout", "cmd": None}]}), encoding="utf-8")
        (run / "remote" / "01-02.json").write_text(json.dumps({"status": "failed", "total_cost_usd": 1.0,
                                                               "killed": "timeout"}), encoding="utf-8")
        self.assertEqual(self.metrics(run, "--gh", self.gh)["remote"]["killed"], {"memory": 1, "timeout": 2})

    def test_docstring_names_both_new_fields(self):
        doc = SCRIPT.read_text(encoding="utf-8").split('"""')[1]
        for field in ("human_round_trips", "hand_written_scripts"):
            self.assertIn(f"`{field}`", doc)

    def test_old_checks_file_is_read_from_its_last_line(self):
        run = self.tmp / "old"
        (run / "reports").mkdir(parents=True)
        (run / "reports" / "01-01.checks").write_text("noise 99 passed\n\n== 7 passed, 2 failed in 3.1s ==\n\n", encoding="utf-8")
        got = self.metrics(run, "--gh", self.gh)["suites"]
        self.assertEqual((got["runs"], got["passed"], got["failed"], got["unparsed"]), (1, 7, 2, 1))

    def test_checks_json_wins_over_the_old_file_of_the_same_id(self):
        run = self.tmp / "both"
        shutil.copytree(FIXTURE / "run" / "reports", run / "reports")
        (run / "reports" / "02-01.checks").unlink()
        got = self.metrics(run, "--gh", self.gh)["suites"]
        self.assertEqual((got["merges"], got["runs"], got["passed"]), (2, 4, 63))

    def test_unreadable_results_are_named_as_gaps_and_counted_without_cost(self):
        run = self.tmp / "broken"
        for d in ("reports", "remote", "tasks"):
            (run / d).mkdir(parents=True)
        (run / "reports" / "01-01.checks.json").write_text("{not json", encoding="utf-8")
        (run / "reports" / "01-02.checks.json").write_text('[{"command": "true", "rc": 0, "passed": 3, '
                                                           '"failed": 0, "skipped": 0, "seconds": 1.5}]', encoding="utf-8")
        (run / "remote" / "01-01.json").write_text("{not json", encoding="utf-8")
        (run / "remote" / "01-02.json").write_text("[1]", encoding="utf-8")
        (run / "remote" / "01-03.json").write_text('{"status": "done", "total_cost_usd": 1.25}', encoding="utf-8")
        (run / "tasks" / "01-01-a.md").write_text("# 01-01: a\nStatus: done\nTicket: 01\nPR: https://x/o/r/pull/7\n", encoding="utf-8")
        answers = self.tmp / "answers"
        answers.mkdir()
        (answers / "7.json").write_text("not json\n", encoding="utf-8")
        gh = shlex.join([sys.executable, "-I", str(self.tmp / "gh_stub.py"), str(answers), str(self.calls)])
        got = self.metrics(run, "--gh", gh)
        self.assertEqual(got["gaps"], ["suite results: 01-01.checks.json is not JSON",
                                       "remote: 01-01.json is not a result", "remote: 01-02.json is not a result",
                                       "remote: 2 run(s) without cost", "github: https://x/o/r/pull/7: no JSON",
                                       "human round trips: no decisions.md"])
        self.assertEqual((got["suites"]["merges"], got["suites"]["runs"], got["suites"]["passed"]), (1, 1, 3))
        remote = got["remote"]
        self.assertEqual((remote["runs"], remote["runs_without_cost"], remote["cost_usd"], remote["by_status"]),
                         (3, 2, 1.25, {"done": 1, "unreadable": 2}))
        self.assertEqual([(r["pr"], r["state"]) for r in got["prs"]["list"]], [("https://x/o/r/pull/7", None)])

    def test_cli_refuses_a_missing_run_folder_and_an_empty_gh(self):
        for run, extra, message in ((self.tmp / "nowhere", [], "no run folder"), (self.tmp, ["--gh", " "],
                                                                                  "--gh is empty")):
            res = run_metrics(self.env, self.tmp, run, *extra)
            self.assertEqual(res.returncode, 2, res.stderr)
            self.assertIn(message, res.stderr)
            self.assertEqual(res.stdout, "")

    def test_no_gh35_specifics_remain_in_the_code(self):
        code = SCRIPT.read_text(encoding="utf-8")
        for pattern in (r"gh-35", r"\b31-", r"'31'", r'"31"', r"-hub\b", r"agents-feature", r"into int/", r"\bint/"):
            self.assertIsNone(re.search(pattern, code), pattern)


if __name__ == "__main__":
    unittest.main()
