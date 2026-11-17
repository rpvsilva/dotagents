"""Tests for `verify_tuner.py`: a tuning task's worktree holding a tiny unittest suite, checked against its report."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from testkit import SCRIPTS, Kit, env_tool_setup  # noqa: E402

VERIFY = SCRIPTS / "verify_tuner.py"

CALC = """def add(a, b):
    return a + b


def sub(a, b):
    return a - b


def mul(a, b):
    return a * b
"""

TESTS = """import unittest

import calc


class CalcTest(unittest.TestCase):
    def test_add(self):
        self.assertEqual(calc.add(2, 3), 5)
"""

# What the tuner adds: a test for `sub`, which line 6 of `calc.py` with `a - b` made `a + b` breaks. `mul` stays untested.
NEW_TEST = """
    def test_sub(self):
        self.assertEqual(calc.sub(5, 3), 2)
"""

TICKET = "# 01: Calc\nStatus: building\nIssue: #7\nBranch: feat/t\nBlocked by: none\n"
TASK = "# 01-tests: tune Calc\nStatus: in-progress\nTicket: 01\nRange: a..b\n"
# `capped` is not installed yet: this stub runs the command after `--`, so `--secs` is read from the Checks line.
CAPPED = '#!/bin/sh\nwhile [ "$1" != "--" ]; do shift; done\nshift\nexec "$@"\n'


class VerifyTunerCase(unittest.TestCase):
    """The sandbox and helpers; `test_verify_tuner_limits.py` shares them."""

    def setUp(self):
        self.kit = kit = Kit()
        self.addCleanup(kit.close)
        clone = kit.make_origin()
        (clone / "calc.py").write_text(CALC, encoding="utf-8")
        (clone / "test_calc.py").write_text(TESTS, encoding="utf-8")
        kit.git("add", ".", cwd=clone)
        kit.git("commit", "-qm", "calc", cwd=clone)
        kit.git("update-ref", "refs/remotes/origin/feat/t", "HEAD", cwd=clone)
        kit.init_run()
        for d in ("tickets", "tasks", "worktrees", "reports"):
            (kit.run_dir / d).mkdir()
        (kit.run_dir / "tickets" / "01-calc.md").write_text(TICKET, encoding="utf-8")
        (kit.run_dir / "tasks" / "01-tests.md").write_text(TASK, encoding="utf-8")
        self.wt = kit.run_dir / "worktrees" / "01-tests"
        kit.git("worktree", "add", "-q", "-b", "task/t/tests", str(self.wt), "origin/feat/t", cwd=clone)
        (kit.bin / "capped").write_text(CAPPED, encoding="utf-8")
        (kit.bin / "capped").chmod(0o755)
        self.checks("capped --mb 2000 --secs 8 -- python3 -m unittest -q")
        self.report = kit.run_dir / "reports" / "01-tests.json"
        self.tune(NEW_TEST)

    def checks(self, *cmds):
        body = "\n".join(cmds)
        (self.kit.run_dir / "map.md").write_text(f"# Map\n\n## Checks\n\n```\n{body}\n```\n\n## Gaps\n",
                                                encoding="utf-8")

    def tune(self, added, commit=True):
        """The tuner's change: `added` appended to the test file, committed on the tuning branch."""
        test = self.wt / "test_calc.py"
        test.write_text(test.read_text(encoding="utf-8") + added, encoding="utf-8")
        if commit:
            self.kit.git("commit", "-qam", "test: more", cwd=self.wt)

    def write_report(self, mutations=(), moved=(), covered=(), **extra):
        """The tuner's output: each mutation `(path, line, old, new)`, each moved `(assertion, now)`, each covered
        `(entry, test)`."""
        out = {"status": "done", "commit": "abc1234", "files": ["test_calc.py"], "baseline": [], "time": [],
               "covered": [{"entry": e, "test": t} for e, t in covered],
               "moved": [{"assertion": a, "now": n} for a, n in moved],
               "mutations": [{"path": f, "line": n, "old": o, "new": w} for f, n, o, w in mutations], "left": []}
        out.update(extra)
        self.report.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")

    def verify(self, *extra):
        p = self.kit.step(VERIFY, self.kit.run_dir, "01-tests", "--report", self.report, *extra)
        self.assertEqual(len(p.stdout.splitlines()), 1, p.stdout + p.stderr)
        return p

    def snapshot(self):
        """Every file of the worktree outside `.git`, with its bytes and mode, and git's status."""
        files = {}
        for path in sorted(self.wt.rglob("*")):
            if ".git" not in path.relative_to(self.wt).parts and path.is_file():
                files[str(path.relative_to(self.wt))] = (path.read_bytes(), path.stat().st_mode)
        return files, self.kit.git("status", "--porcelain", cwd=self.wt)

    def assertFails(self, p, reason):
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertTrue(p.stdout.startswith(f"FAIL 01-tests {reason} (log: "), p.stdout)
        log = p.stdout.rsplit("(log: ", 1)[1].rstrip(")\n")
        self.assertTrue(Path(log).is_file(), log)


class VerifyTunerTest(VerifyTunerCase):
    def test_a_mutation_that_turns_the_suite_red_passes(self):
        self.write_report([("calc.py", 6, "a - b", "a + b")])
        before = self.snapshot()
        p = self.verify()
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertEqual(p.stdout, "OK 01-tests verified mutations=1\n")
        self.assertEqual(self.snapshot(), before)

    def test_checks_calling_a_tool_setup_installed_in_the_env_run_green(self):
        suite = "capped --mb 2000 --secs 8 -- python3 -m unittest -q"
        (self.kit.run_dir / "map.md").write_text(
            f"# Map\n\n## Checks\n\n```\nenvtool\n{suite}\n```\n\n" + env_tool_setup("envtool", "from the env")
            + "\n## Gaps\n", encoding="utf-8")
        p = self.kit.step(SCRIPTS / "setup.py", self.kit.run_dir)
        self.assertTrue(p.stdout.startswith("OK run set up "), p.stdout + p.stderr)
        self.write_report([("calc.py", 6, "a - b", "a + b")])
        p = self.verify()
        self.assertEqual(p.stdout, "OK 01-tests verified mutations=1\n")
        self.assertIn("from the env", (self.kit.run_dir / "logs" / "01-tests-verify_tuner.log").read_text(encoding="utf-8"))

    def test_mutations_listed_under_one_key_are_each_applied(self):
        self.tune("\n    def test_mul(self):\n        self.assertEqual(calc.mul(2, 3), 6)\n")
        self.write_report([("calc.py", 6, "a - b", "a + b"), ("calc.py", 10, "a * b", "a + b")])
        self.assertEqual(self.verify().stdout, "OK 01-tests verified mutations=2\n")

    def test_a_mutation_that_stays_green_fails_naming_it(self):
        self.write_report([("calc.py", 6, "a - b", "a + b"), ("calc.py", 10, "a * b", "a + b")])
        before = self.snapshot()
        p = self.verify()
        self.assertFails(p, "mutation-survived calc.py:10")
        self.assertEqual(self.snapshot(), before)

    def test_a_mutation_whose_old_text_is_not_on_its_line_fails(self):
        self.write_report([("calc.py", 5, "a - b", "a + b")])
        self.assertFails(self.verify(), "bad-mutation calc.py:5")

    def test_a_mutation_outside_the_worktree_fails(self):
        self.write_report([("../../tickets/01-calc.md", 1, "Calc", "X")])
        self.assertFails(self.verify(), "bad-mutation ../../tickets/01-calc.md:1")

    def test_a_red_suite_fails_before_any_mutation(self):
        self.tune("\n    def test_broken(self):\n        self.assertEqual(calc.add(1, 1), 3)\n")
        self.write_report([("calc.py", 6, "a - b", "a + b")])
        self.assertFails(self.verify(), "red capped --mb 2000 --secs 8 -- python3 -m unittest -q")

    def test_a_removed_assertion_without_a_moved_line_fails_naming_it(self):
        test = self.wt / "test_calc.py"
        text = test.read_text(encoding="utf-8")
        test.write_text(text.replace("        self.assertEqual(calc.add(2, 3), 5)\n", "        pass\n"),
                        encoding="utf-8")
        self.kit.git("commit", "-qam", "test: drop", cwd=self.wt)
        self.write_report([("calc.py", 6, "a - b", "a + b")])
        self.assertFails(self.verify(), "assertion-removed test_calc.py:8")

    def test_a_removed_assertion_named_in_a_moved_line_passes(self):
        test = self.wt / "test_calc.py"
        text = test.read_text(encoding="utf-8")
        test.write_text(text.replace("        self.assertEqual(calc.add(2, 3), 5)\n", "        pass\n"),
                        encoding="utf-8")
        self.kit.git("commit", "-qam", "test: drop", cwd=self.wt)
        self.write_report([("calc.py", 6, "a - b", "a + b")],
                          moved=[("`self.assertEqual(calc.add(2, 3), 5)`", "covered by test_sub's setup")])
        self.assertEqual(self.verify().stdout, "OK 01-tests verified mutations=1\n")

    def test_an_assertion_moved_within_the_diff_needs_no_moved_line(self):
        test = self.wt / "test_calc.py"
        text = test.read_text(encoding="utf-8")
        test.write_text(text.replace("        self.assertEqual(calc.add(2, 3), 5)\n", "        pass\n"),
                        encoding="utf-8")
        (self.wt / "test_more.py").write_text(TESTS.replace("CalcTest", "MoreTest"), encoding="utf-8")
        self.kit.git("add", ".", cwd=self.wt)
        self.kit.git("commit", "-qm", "test: move", cwd=self.wt)
        self.write_report([("calc.py", 6, "a - b", "a + b")])
        self.assertEqual(self.verify().stdout, "OK 01-tests verified mutations=1\n")

    def test_a_changed_non_test_file_fails_naming_it(self):
        (self.wt / "calc.py").write_text(CALC + "\n# faster\n", encoding="utf-8")
        self.kit.git("commit", "-qam", "perf", cwd=self.wt)
        self.write_report([("calc.py", 6, "a - b", "a + b")])
        self.assertFails(self.verify(), "non-test-file calc.py")

    def test_an_uncommitted_new_non_test_file_fails_naming_it(self):
        (self.wt / "helper.py").write_text("X = 1\n", encoding="utf-8")
        self.write_report([("calc.py", 6, "a - b", "a + b")])
        self.assertFails(self.verify(), "non-test-file helper.py")

    def test_uncommitted_test_changes_are_verified_too(self):
        self.tune("\n    def test_mul(self):\n        self.assertEqual(calc.mul(2, 3), 6)\n", commit=False)
        self.write_report([("calc.py", 10, "a * b", "a + b")])
        before = self.snapshot()
        self.assertEqual(self.verify().stdout, "OK 01-tests verified mutations=1\n")
        self.assertEqual(self.snapshot(), before)

    def test_a_report_with_no_mutations_still_checks_files_and_suite(self):
        self.write_report()
        self.assertEqual(self.verify().stdout, "OK 01-tests verified mutations=0\n")

    def base_commit(self, files):
        """Adds `files` to the ticket branch, under the tuning branch's fork point."""
        for name, text in files.items():
            (self.wt / name).parent.mkdir(parents=True, exist_ok=True)
            (self.wt / name).write_text(text, encoding="utf-8")
        self.kit.git("add", ".", cwd=self.wt)
        self.kit.git("commit", "-qm", "base", cwd=self.wt)
        self.kit.git("update-ref", "refs/remotes/origin/feat/t", "HEAD", cwd=self.wt)

    def test_a_removed_shell_assertion_without_a_moved_line_fails_naming_it(self):
        lines = ['check "exit code" 0 "$rc"', "cmp -s want got || fail 'output differs'",
                 "grep -q ok out.txt || fail 'no ok'", '[ "$rc" -eq 0 ] || fail "rc $rc"',
                 'grep -q bad out.txt && fail "bad"']
        self.base_commit({"tests/guard.test.sh": "#!/bin/sh\nrc=0\n" + "\n".join(lines) + "\n"})
        for n, line in enumerate(lines, start=3):
            with self.subTest(line):
                test = self.wt / "tests" / "guard.test.sh"
                original = test.read_text(encoding="utf-8")
                test.write_text(original.replace(line + "\n", ""), encoding="utf-8")
                self.write_report([("calc.py", 6, "a - b", "a + b")])
                self.assertFails(self.verify(), f"assertion-removed tests/guard.test.sh:{n}")
                test.write_text(original, encoding="utf-8")

    def test_a_mutation_on_a_test_file_fails(self):
        self.write_report([("test_calc.py", 8, "5", "6")])
        self.assertFails(self.verify(), "test-file-mutation test_calc.py:8")

    def test_covered_entries_without_a_mutation_each_fail(self):
        self.write_report(covered=[("calc.sub", "test_sub")])
        self.assertFails(self.verify(), "unproven-covered covered=1 mutations=0")
        self.write_report([("calc.py", 6, "a - b", "a + b")], covered=[("calc.sub", "test_sub"), ("calc.mul", "test_mul")])
        self.assertFails(self.verify(), "unproven-covered covered=2 mutations=1")

    def test_test_harness_files_under_tests_are_not_test_files(self):
        for name in ("tests/run.sh", "tests/suites.txt", "tests/affected.sh"):
            with self.subTest(name):
                (self.wt / "tests").mkdir(exist_ok=True)
                (self.wt / name).write_text("x\n", encoding="utf-8")
                self.write_report([("calc.py", 6, "a - b", "a + b")])
                self.assertFails(self.verify(), f"non-test-file {name}")
                (self.wt / name).unlink()

    def test_fixtures_and_test_helpers_count_as_test_files(self):
        self.base_commit({"tests/fixtures/in.json": "{}\n", "tests/helpers.py": "X = 1\n"})
        (self.wt / "tests" / "fixtures" / "in.json").write_text('{"a": 1}\n', encoding="utf-8")
        (self.wt / "tests" / "helpers.py").write_text("X = 2\n", encoding="utf-8")
        self.kit.git("commit", "-qam", "test: data", cwd=self.wt)
        self.write_report([("calc.py", 6, "a - b", "a + b")])
        self.assertEqual(self.verify().stdout, "OK 01-tests verified mutations=1\n")

    def test_an_assertion_copied_into_an_untracked_file_still_counts_as_removed(self):
        test = self.wt / "test_calc.py"
        text = test.read_text(encoding="utf-8")
        test.write_text(text.replace("        self.assertEqual(calc.add(2, 3), 5)\n", "        pass\n"),
                        encoding="utf-8")
        self.kit.git("commit", "-qam", "test: drop", cwd=self.wt)
        (self.wt / "test_more.py").write_text(TESTS.replace("CalcTest", "MoreTest"), encoding="utf-8")
        self.write_report([("calc.py", 6, "a - b", "a + b")])
        self.assertFails(self.verify(), "assertion-removed test_calc.py:8")

    def test_a_missing_report_fails(self):
        self.assertFails(self.verify(), f"no-report {self.report}")

    def test_an_unknown_argument_fails_before_any_suite_runs(self):
        self.write_report([("calc.py", 6, "a - b", "a + b")])
        self.assertFails(self.verify("--fast"), "usage: unknown argument --fast")
        log = (self.kit.run_dir / "logs" / "01-tests-verify_tuner.log").read_text(encoding="utf-8")
        self.assertNotIn("$ capped", log)

    def test_a_task_without_a_worktree_fails(self):
        self.write_report([("calc.py", 6, "a - b", "a + b")])
        p = self.kit.step(VERIFY, self.kit.run_dir, "02-tests", "--report", self.report)
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertTrue(p.stdout.startswith("FAIL 02-tests no-worktree: "), p.stdout)

    def test_a_mutation_entry_missing_a_field_fails_naming_it(self):
        self.report.write_text(json.dumps({"status": "done", "commit": "", "files": [], "baseline": [], "time": [],
                                           "covered": [], "moved": [], "left": [],
                                           "mutations": [{"path": "calc.py", "line": 6, "old": "a - b"}]}),
                               encoding="utf-8")
        self.assertFails(self.verify(), "bad-report $.mutations[0]: missing new")

    def test_a_report_that_is_not_json_fails(self):
        self.report.write_text("status: done\nmutations: calc.py:6 a - b -> a + b\n", encoding="utf-8")
        self.assertFails(self.verify(), "bad-report json")

    def test_a_fenced_report_is_read(self):
        self.write_report([("calc.py", 6, "a - b", "a + b")])
        self.report.write_text("```json\n" + self.report.read_text(encoding="utf-8") + "```\n", encoding="utf-8")
        self.assertEqual(self.verify().stdout, "OK 01-tests verified mutations=1\n")

    def test_the_default_report_is_the_json_output_in_reports(self):
        self.write_report([("calc.py", 6, "a - b", "a + b")])
        p = self.kit.step(VERIFY, self.kit.run_dir, "01-tests")
        self.assertEqual(p.stdout, "OK 01-tests verified mutations=1\n", p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
