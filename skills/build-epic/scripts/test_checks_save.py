"""Tests for the saved suite results: `<run>/reports/<id>.checks.json`, written by `merge_task.py` on every merge.

Per suite it holds `passed`, `failed`, `skipped`, `seconds` and `skip_reasons`, counted from pytest's `-rs` summary,
unittest's `Ran N tests` / `OK (skipped=N)`, Jest's `Tests:` line, or this repo's `<suite>: ok` and `skipped` lines
(only inside its runners' `== ` framing), and `null` counts with the exit code when the output says none of these.
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_merge_task as tm  # noqa: E402
from steplib import checks, runfolder  # noqa: E402
from testkit import Kit  # noqa: E402


def shell_echo(text):
    """A one-line suite command printing `text` (no backslashes in it), newlines and all."""
    return "printf '%b\\n' '" + text.replace("'", "'\\''").replace("\n", "\\n") + "'"


class SaveTest(unittest.TestCase):
    """`checks.run_all` then `checks.save`, as a merge runs them, read back from the saved file."""

    def saved(self, *cmds):
        with Kit() as kit, open(kit.tmp / "log", "w") as log:
            checks.save(kit.run_dir, "01-01", checks.run_all(list(cmds), kit.tmp, log))
            return json.loads((kit.run_dir / "reports" / "01-01.checks.json").read_text())

    def test_pytest_skip_lines_save_the_skip_count_and_both_reasons(self):
        out = "\n".join([
            "============================= test session starts ==============================",
            "collected 5 items",
            "",
            "tests/test_a.py ..s.s                                                    [100%]",
            "",
            "=========================== short test summary info ============================",
            "SKIPPED [1] tests/test_a.py:12: no docker",
            "SKIPPED [1] tests/test_b.py:40: no docker",
            "========================= 3 passed, 2 skipped in 0.12s =========================",
        ])
        [suite] = self.saved(shell_echo(out))
        self.assertEqual((suite["passed"], suite["failed"], suite["skipped"]), (3, 0, 2))
        self.assertEqual(suite["skip_reasons"], ["no docker", "no docker"])
        self.assertEqual(suite["rc"], 0)
        self.assertIsInstance(suite["seconds"], (int, float))

    def test_pytest_failures_and_errors_count_as_failed(self):
        [suite] = self.saved(shell_echo("==== 1 failed, 4 passed, 1 error in 2.01s ====") + "; exit 1")
        self.assertEqual((suite["passed"], suite["failed"], suite["skipped"], suite["rc"]), (4, 2, 0, 1))

    def test_unittest_ran_and_ok_skipped_counts(self):
        out = "\n".join(["test_a (m.T.test_a) ... ok", "test_b (m.T.test_b) ... skipped 'no zsh'", "",
                         "-" * 70, "Ran 7 tests in 0.300s", "", "OK (skipped=1)"])
        [suite] = self.saved(shell_echo(out) + " >&2")
        self.assertEqual((suite["passed"], suite["failed"], suite["skipped"]), (6, 0, 1))
        self.assertEqual(suite["skip_reasons"], ["no zsh"])

    def test_unittest_failed_line_counts_failures_and_errors(self):
        [suite] = self.saved(shell_echo("Ran 10 tests in 1.0s\n\nFAILED (failures=2, errors=1, skipped=3)"))
        self.assertEqual((suite["passed"], suite["failed"], suite["skipped"]), (4, 3, 3))

    def test_repo_suite_ok_and_skipped_lines(self):
        out = "\n".join([
            "== shellcheck: not installed, skipped (install 0.11.0, or docker to run its image)",
            "== tests/a.test.sh (1s)", "a.test.sh: ok",
            "== tests/ui.test.sh (2s)", "ui: ok",
            "== tests/remote-smoke.test.sh (0s)", 'remote-smoke.test.sh: skipped (opt in with REMOTE_SMOKE=1)',
            "== tests/b.test.sh (1s)", "Ran 3 tests in 0.1s", "OK",
        ])
        [suite] = self.saved(shell_echo(out))
        self.assertEqual((suite["passed"], suite["failed"], suite["skipped"]), (2, 0, 2))
        self.assertEqual(suite["skip_reasons"], [
            "shellcheck: not installed (install 0.11.0, or docker to run its image)",
            "remote-smoke.test.sh: opt in with REMOTE_SMOKE=1"])

    def test_repo_failed_suite_counts_as_failed(self):
        [suite] = self.saved(shell_echo("a.test.sh: ok\n== tests/b.test.sh: FAILED (exit 1)") + "; exit 1")
        self.assertEqual((suite["passed"], suite["failed"], suite["skipped"]), (1, 1, 0))

    def test_a_stray_ok_line_above_a_pytest_failure_saves_the_failure(self):
        out = "\n".join(["log: ok", "cache: skipped, stale", "==== 1 failed, 9 passed in 0.50s ===="])
        [suite] = self.saved(shell_echo(out) + "; exit 1")
        self.assertEqual((suite["passed"], suite["failed"], suite["skipped"]), (9, 1, 0))

    def test_a_stray_ok_line_above_a_unittest_summary_saves_the_unittest_counts(self):
        [suite] = self.saved(shell_echo("setup: ok\nRan 4 tests in 0.1s\n\nOK"))
        self.assertEqual((suite["passed"], suite["failed"], suite["skipped"]), (4, 0, 0))

    def test_jest_summary_counts(self):
        out = "\n".join([
            "PASS src/a.test.js", "FAIL src/b.test.js",
            "Test Suites: 1 failed, 1 passed, 2 total",
            "Tests:       1 failed, 2 skipped, 40 passed, 43 total",
            "Snapshots:   0 total", "Time:        1.2 s",
        ])
        [suite] = self.saved(shell_echo(out) + "; exit 1")
        self.assertEqual((suite["passed"], suite["failed"], suite["skipped"]), (40, 1, 2))

    def test_jest_summary_without_failures(self):
        [suite] = self.saved(shell_echo("Tests: 2 skipped, 1 todo, 40 passed, 43 total"))
        self.assertEqual((suite["passed"], suite["failed"], suite["skipped"]), (40, 0, 3))

    def test_unparseable_suite_saves_null_counts_with_its_exit_code(self):
        green, red = self.saved("echo suite two", shell_echo("make: *** [all] Error 3") + "; exit 3")
        for suite, rc in ((green, 0), (red, 3)):
            self.assertIsNone(suite["passed"])
            self.assertIsNone(suite["failed"])
            self.assertIsNone(suite["skipped"])
            self.assertEqual(suite["skip_reasons"], [])
            self.assertEqual(suite["rc"], rc)


class MergeSavesTest(unittest.TestCase):
    """Through `merge_task.py`, with `test_merge_task`'s throwaway origin and stub `gh`."""

    setUp = tm.MergeTaskTest.setUp
    checks = tm.MergeTaskTest.checks
    commit = tm.MergeTaskTest.commit
    merge = tm.MergeTaskTest.merge

    def saved(self):
        return json.loads((self.kit.run_dir / "reports" / "01-01.checks.json").read_text())

    def test_a_merge_writes_the_file_and_a_later_merge_overwrites_it(self):
        self.checks(shell_echo("==== 2 passed, 1 skipped in 0.1s ====\nSKIPPED [1] t.py:3: no docker") + "; exit 1")
        self.assertTrue(self.merge().startswith("FAIL 01-01 red "))
        [first] = self.saved()
        self.assertEqual((first["passed"], first["skipped"], first["rc"]), (2, 1, 1))
        self.assertEqual(first["skip_reasons"], ["no docker"])

        self.checks(shell_echo("Ran 5 tests in 0.2s\n\nOK"), "true")
        self.assertRegex(self.merge(), r"^OK 01-01 done suites=2 ")
        self.assertEqual(runfolder.get(self.task, "Status"), "done")
        unit, bare = self.saved()
        self.assertEqual((unit["passed"], unit["failed"], unit["skipped"], unit["skip_reasons"], unit["rc"]),
                         (5, 0, 0, [], 0))
        self.assertEqual((bare["command"], bare["passed"], bare["rc"]), ("true", None, 0))


if __name__ == "__main__":
    unittest.main()
