"""Tests for `review_prompt.py`: one golden prompt per mode, the shared rules, and the usage failures."""
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "review_prompt.py"
GOLDEN = HERE / "fixtures" / "review_prompt"
MODES = ("task", "hub", "ticket", "re-review", "security")

COMMON = ["--pr", "42", "--worktree", "/wt/pr-42", "--head", "abc1234", "--base", "origin/main"]
RUN = ["--run", "/runs/r1"]
ARGS = {
    "task": COMMON + RUN + ["--task", "/runs/r1/tasks/03-02-review-prompt.md"],
    "hub": COMMON + RUN + ["--task", "/runs/r1/tasks/03-09-hub.md", "--ticket", "/runs/r1/tickets/03-review.md",
                           "--task-base", "origin/feat/03-review"],
    "ticket": COMMON + RUN + ["--task", "/runs/r1/tickets/03-review.md"],
    "re-review": ["--pr", "42", "--worktree", "/wt/pr-42", "--head", "abc1234", "--base", "def5678", *RUN,
                  "--findings", "@FINDINGS@"],
    "security": COMMON + RUN + ["--task", "/runs/r1/tickets/03-review.md"],
}


# Git Bash's `bash`: a bare `bash` on Windows is WSL's.
BASH = shutil.which("bash") or "bash"


def posix(out, args):
    """`out` with each absolute path of `args` as the goldens spell it. The script makes them absolute for the
    platform: `/wt/pr-42` renders as `D:\\wt\\pr-42` on Windows, and quoted in the diff command."""
    for path in sorted((a for a in args if a.startswith("/")), key=len, reverse=True):
        native = os.path.abspath(path)
        out = out.replace(shlex.quote(native), shlex.quote(path)).replace(native, path)
    return out


def run(*args, cwd=None):
    return subprocess.run([sys.executable, "-I", str(SCRIPT), *args], capture_output=True, text=True, cwd=cwd)


class ReviewPromptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.findings = self.tmp / "findings.md"
        self.findings.write_text("1. blocker: x.py:3 crashes on empty input\n")

    def render(self, mode, args=None):
        args = [a.replace("@FINDINGS@", str(self.findings)) for a in (args or ARGS[mode])]
        got = run(mode, *args)
        self.assertEqual(got.returncode, 0, got.stderr)
        return posix(got.stdout.replace(str(self.findings), "@FINDINGS@"), args)

    def test_each_mode_matches_its_golden_prompt(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                self.assertEqual(self.render(mode), (GOLDEN / f"{mode}.txt").read_text())

    def test_every_mode_diffs_against_the_merge_base_and_reads_before_testing(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                out = self.render(mode)
                base = ARGS[mode][ARGS[mode].index("--base") + 1]
                self.assertIn(f"merge-base {base} abc1234", out)
                self.assertIn("not a finding", out)
                self.assertIn("Run tests only after you have read the whole diff", out)

    def test_hub_has_a_task_section_and_a_whole_ticket_section(self):
        out = self.render("hub")
        a, b = out.index("## A."), out.index("## B.")
        self.assertLess(a, b)
        self.assertIn("merge-base origin/feat/03-review abc1234", out[a:b])
        self.assertIn("/runs/r1/tasks/03-09-hub.md", out[a:b])
        self.assertIn("merge-base origin/main abc1234", out[b:])
        self.assertIn("/runs/r1/tickets/03-review.md", out[b:])

    def test_re_review_names_only_the_new_diff_and_the_numbered_findings(self):
        out = self.render("re-review")
        self.assertIn("@FINDINGS@", out)
        self.assertIn("each numbered finding", out)
        self.assertIn("def5678 is the head last reviewed", out)
        self.assertNotIn("tasks/", out)

    def test_without_a_run_the_decisions_and_ci_lines_are_left_out(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                args = [a for a in ARGS[mode] if a not in RUN]
                out = self.render(mode, args)
                self.assertNotIn("decisions.md", out)
                self.assertNotIn("ci:", out)
                self.assertNotIn("/runs/r1/map.md", out)
                self.assertIn("merge-base", out)

    def test_a_decisions_file_is_named_with_or_without_a_run(self):
        args = [a for a in ARGS["task"] if a not in RUN]
        out = self.render("task", args + ["--decisions", "/d/decisions.md"])
        self.assertIn("- /d/decisions.md: a rule it waives is never a finding", out)
        self.assertIn("its `ci:` line", out)
        self.assertNotIn("map.md", out)
        out = self.render("task", ARGS["task"] + ["--decisions", "/d/decisions.md"])
        self.assertIn("- /d/decisions.md: a rule it waives", out)
        self.assertNotIn("/runs/r1/decisions.md", out)
        self.assertIn("/runs/r1/map.md", out)

    def test_a_decisions_file_names_its_test_command_as_the_command_to_run(self):
        self.assertIn("- its `test_command:` line: the exact command to run the tests with", self.render("task"))
        args = [a for a in ARGS["task"] if a not in RUN]
        self.assertNotIn("test_command", self.render("task", args))

    def test_a_report_file_is_the_one_file_the_reviewer_may_write(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                args = [a.replace("@FINDINGS@", str(self.findings)) for a in ARGS[mode]]
                got = run(mode, *args, "--report", "reviews/42-abc1234.md", cwd=self.tmp)
                self.assertEqual(got.returncode, 0, got.stderr)
                report = str(Path(self.tmp, "reviews", "42-abc1234.md"))
                self.assertTrue(got.stdout.endswith(
                    f"\nWrite your report to {report}, the only file you may write, then return the block alone, "
                    "as the brief's Report says.\n"), got.stdout)
                self.assertNotIn("the only file you may write", self.render(mode))

    def test_relative_paths_are_made_absolute(self):
        got = run("task", "--pr", "1", "--worktree", "wt", "--head", "h", "--base", "b", cwd=self.tmp)
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertIn(str(Path(self.tmp, "wt")), got.stdout)

    def test_the_diff_command_runs_as_given_whatever_the_worktree_and_refs_hold(self):
        repo = self.tmp / "a repo $(touch pwned)"
        git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        (repo / "f.txt").write_text("one\n")
        subprocess.run(git + ["add", "-A"], check=True)
        subprocess.run(git + ["commit", "-qm", "one"], check=True)
        (repo / "f.txt").write_text("two\n")
        subprocess.run(git + ["commit", "-qam", "two"], check=True)
        got = run("task", "--pr", "1", "--worktree", str(repo), "--head", "HEAD", "--base", "HEAD~1;touch pwned2")
        self.assertEqual(got.returncode, 0, got.stderr)
        command = next(line.strip() for line in got.stdout.splitlines() if line.strip().startswith("git -C"))
        subprocess.run([BASH, "-c", command], cwd=self.tmp, capture_output=True, text=True)
        self.assertFalse((self.tmp / "pwned").exists() or (self.tmp / "pwned2").exists(), command)
        got = run("task", "--pr", "1", "--worktree", str(repo), "--head", "HEAD", "--base", "HEAD~1")
        command = next(line.strip() for line in got.stdout.splitlines() if line.strip().startswith("git -C"))
        res = subprocess.run([BASH, "-c", command], cwd=self.tmp, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("+two", res.stdout)

    def assert_usage(self, got):
        self.assertEqual(got.returncode, 2, got.stdout + got.stderr)
        self.assertEqual(got.stdout, "")
        self.assertTrue(got.stderr.startswith("usage: review_prompt.py "), got.stderr)

    def test_an_unknown_mode_fails_with_usage(self):
        self.assert_usage(run("deep", *COMMON))

    def test_re_review_without_findings_fails_with_usage(self):
        self.assert_usage(run("re-review", *COMMON))

    def test_re_review_with_a_missing_findings_file_fails_with_usage(self):
        self.assert_usage(run("re-review", *COMMON, "--findings", str(self.tmp / "none.md")))

    def test_a_missing_required_option_fails_with_usage(self):
        self.assert_usage(run("task", "--pr", "42"))

    def test_hub_without_its_ticket_and_task_base_fails_with_usage(self):
        self.assert_usage(run("hub", *COMMON, "--task", "/t.md"))


if __name__ == "__main__":
    unittest.main()
