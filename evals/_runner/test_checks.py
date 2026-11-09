"""Tests for `checks.py`: each case check run in the finished fixture repo with the `EVAL_*` env, plus the built-in
`no-github` check, and the hash of a case's checks."""
import json
import os
import shutil
import stat
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import checks  # noqa: E402
from testlib import EvalSandbox  # noqa: E402

NAMES = ("WORK", "SCRATCH", "INPUT", "HOME", "TRANSCRIPT", "GH_CALLS", "CHECKOUT")


class ChecksTest(EvalSandbox):
    def setUp(self):
        super().setUp()
        self.run_dir = self.tmp / "run"
        self.paths = {"work": self.run_dir / "work", "scratch": self.run_dir / "scratch",
                      "input": self.run_dir / "input", "home": self.run_dir / "home",
                      "transcript": self.run_dir / "transcript.jsonl", "gh_calls": self.run_dir / "gh-calls",
                      "checkout": self.tmp / "checkout"}
        for name in ("work", "scratch", "input", "home", "checkout"):
            self.paths[name].mkdir(parents=True)
        self.paths["transcript"].write_text("{}\n")
        self.paths["gh_calls"].write_text("")
        self.case = self.tmp / "case"
        (self.case / "checks").mkdir(parents=True)

    def check(self, name, body):
        path = self.case / "checks" / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def run_checks(self):
        found = {p.stem: p for p in sorted((self.case / "checks").iterdir())}
        return checks.run(found, self.paths)

    def test_every_check_and_no_github_pass_when_each_exits_zero(self):
        self.check("first.sh", "exit 0\n")
        self.check("second.sh", "true\n")
        self.assertEqual(self.run_checks(), {"first": True, "second": True, "no-github": True})

    def test_a_check_exiting_non_zero_fails_alone(self):
        self.check("good.sh", "exit 0\n")
        self.check("bad.sh", "exit 3\n")
        self.assertEqual(self.run_checks(), {"bad": False, "good": True, "no-github": True})

    def test_a_check_that_cannot_start_fails(self):
        path = self.case / "checks" / "broken.sh"
        path.write_text("#!/no/such/interpreter\n")
        path.chmod(0o755)
        self.assertEqual(self.run_checks(), {"broken": False, "no-github": True})

    def test_a_check_runs_in_work_with_the_eval_env_and_no_token(self):
        dump = self.tmp / "env.json"
        # The caller's own EVAL_* values never reach a check: the run's paths do.
        for name in NAMES:
            os.environ["EVAL_" + name] = "/caller/value"
            self.addCleanup(os.environ.pop, "EVAL_" + name, None)
        self.check("env.sh", f'python3 -c "import json,os; print(json.dumps({{**os.environ, \'PWD\': os.getcwd()}}))" '
                             f'>{dump}\n')
        self.assertEqual(self.run_checks()["env"], True)
        env = json.loads(dump.read_text())
        self.assertEqual(Path(env["PWD"]).resolve(), self.paths["work"].resolve())
        for name in NAMES:
            self.assertEqual(env["EVAL_" + name], str(self.paths[name.lower()]), name)
        for var, token in self.tokens.items():
            self.assertNotIn(var, env)
            self.assertNotIn(token, json.dumps(env))

    def test_no_github_fails_once_gh_was_called(self):
        self.check("ok.sh", "exit 0\n")
        self.paths["gh_calls"].write_text("pr list\n")
        self.assertEqual(self.run_checks(), {"ok": True, "no-github": False})

    def test_no_github_passes_without_a_calls_file(self):
        self.paths["gh_calls"].unlink()
        self.assertEqual(self.run_checks(), {"no-github": True})

    def test_a_hung_check_fails_at_its_time_limit(self):
        self.check("hang.sh", "exec sleep 30\n")
        found = {p.stem: p for p in (self.case / "checks").iterdir()}
        self.assertEqual(checks.run(found, self.paths, secs=1), {"hang": False, "no-github": True})

    def test_checks_hash_follows_the_checks_files(self):
        self.check("a.sh", "exit 0\n")
        first = checks.checks_hash(self.case)
        self.assertEqual(checks.checks_hash(self.case), first)
        (self.case / "prompt.md").write_text("not a check\n")
        self.assertEqual(checks.checks_hash(self.case), first)
        self.check("a.sh", "exit 1\n")
        self.assertNotEqual(checks.checks_hash(self.case), first)
        second = checks.checks_hash(self.case)
        shutil.copy(self.case / "checks" / "a.sh", self.case / "checks" / "b.sh")
        self.assertNotEqual(checks.checks_hash(self.case), second)
