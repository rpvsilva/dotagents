"""Tests for `review_prompt.py`: one golden prompt per mode, the shared rules, and the usage failures."""
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
    return subprocess.run([sys.executable, "-I", str(SCRIPT), *args], capture_output=True, text=True, encoding="utf-8",
                          cwd=cwd)


class ReviewPromptTest(unittest.TestCase):
    def setUp(self):
        # Resolved, as the child's cwd is: macOS tmp is under a /var symlink, Windows tmp under an 8.3 short name.
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        self.findings = self.tmp / "findings.md"
        self.findings.write_text("1. blocker: x.py:3 crashes on empty input\n", encoding="utf-8")

    def render(self, mode, args=None):
        args = [a.replace("@FINDINGS@", str(self.findings)) for a in (args or ARGS[mode])]
        got = run(mode, *args)
        self.assertEqual(got.returncode, 0, got.stderr)
        return posix(got.stdout.replace(str(self.findings), "@FINDINGS@"), args)

    def test_each_mode_matches_its_golden_prompt(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                self.assertEqual(self.render(mode), (GOLDEN / f"{mode}.txt").read_text(encoding="utf-8"))

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
                report = str(self.tmp / "reviews" / "42-abc1234.md")
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
        (repo / "f.txt").write_text("one\n", encoding="utf-8")
        subprocess.run(git + ["add", "-A"], check=True)
        subprocess.run(git + ["commit", "-qm", "one"], check=True)
        (repo / "f.txt").write_text("two\n", encoding="utf-8")
        subprocess.run(git + ["commit", "-qam", "two"], check=True)
        got = run("task", "--pr", "1", "--worktree", str(repo), "--head", "HEAD", "--base", "HEAD~1;touch pwned2")
        self.assertEqual(got.returncode, 0, got.stderr)
        command = next(line.strip() for line in got.stdout.splitlines() if line.strip().startswith("git -C"))
        subprocess.run([BASH, "-c", command], cwd=self.tmp, capture_output=True, text=True, encoding="utf-8")
        self.assertFalse((self.tmp / "pwned").exists() or (self.tmp / "pwned2").exists(), command)
        got = run("task", "--pr", "1", "--worktree", str(repo), "--head", "HEAD", "--base", "HEAD~1")
        command = next(line.strip() for line in got.stdout.splitlines() if line.strip().startswith("git -C"))
        res = subprocess.run([BASH, "-c", command], cwd=self.tmp, capture_output=True, text=True, encoding="utf-8")
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

    # A task's commits before they merge, with no PR (build-epic's high-tier tasks)

    NO_PR = ["--worktree", "/runs/r1/worktrees/03-02", "--head", "abc1234", "--base", "feat/03-review", *RUN,
             "--task", "/runs/r1/tasks/03-02-review-prompt.md", "--report", "/runs/r1/reviews/03-02-abc1234.md"]

    def test_a_task_with_no_pr_is_reviewed_from_its_task_file_and_local_commits(self):
        out = self.render("task", self.NO_PR)
        self.assertTrue(out.startswith("Review the commits of task /runs/r1/tasks/03-02-review-prompt.md, before "
                                       "they merge into feat/03-review. There is no PR"), out)
        self.assertIn("not `gh pr view`", out)
        self.assertIn("/runs/r1/tasks/03-02-review-prompt.md: what the change was asked to do", out)
        self.assertIn("git -C /runs/r1/worktrees/03-02 diff $(git -C /runs/r1/worktrees/03-02 merge-base "
                      "feat/03-review abc1234) abc1234", out)
        self.assertNotIn("PR #", out)

    def test_with_no_pr_only_a_local_task_review_runs(self):
        no_task = [a for a in self.NO_PR if a != "/runs/r1/tasks/03-02-review-prompt.md" and a != "--task"]
        self.assert_usage(run("task", *no_task))
        for mode in ("ticket", "re-review", "security"):
            self.assert_usage(run(mode, *self.NO_PR, "--findings", str(self.findings)))
        self.assert_usage(run("task", *self.NO_PR[:-2], "--remote", str(self.tmp / "remote"),
                              "--allow-author", "rui"))

    def test_the_pr_task_prompt_is_unchanged(self):
        self.assertEqual(self.render("task"), (GOLDEN / "task.txt").read_text(encoding="utf-8"))


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True,
                          capture_output=True, text=True, encoding="utf-8").stdout.strip()


PR_FACTS = ('{"author": {"login": "Rui"}, "title": "Add the review prompt", "body": "Closes #3", "headRefOid": "x", '
            '"baseRefName": "main"}\n')


class RemotePromptTest(unittest.TestCase):
    """`--remote <dir>`: the inputs written or listed for attaching, and a prompt naming only `~/work` and
    `~/attached/<name>`."""

    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / "wt"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.commit("f.txt", "one\n")
        git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.commit("g.txt", "ticket\n")
        git(self.repo, "update-ref", "refs/remotes/origin/feat/03-review", "HEAD")
        self.last = git(self.repo, "rev-parse", "HEAD")
        self.head = self.commit("f.txt", "two\n")
        self.run_dir = self.tmp / "run"
        for name in ("decisions.md", "map.md", "spec.md", "tasks/03-02-review-prompt.md", "tasks/03-09-hub.md",
                     "tickets/03-review.md", "findings.md"):
            (self.run_dir / name).parent.mkdir(parents=True, exist_ok=True)
            (self.run_dir / name).write_text(f"{name}\n", encoding="utf-8")
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.gh_log = self.tmp / "gh.log"
        self.stub_gh(f"printf '%s\\n' \"$*\" >> '{self.gh_log}'\ncat <<'JSON'\n{PR_FACTS}JSON\n")
        self.out = self.tmp / "out"

    def commit(self, name, text):
        (self.repo / name).write_text(text, encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", name)
        return git(self.repo, "rev-parse", "HEAD")

    def stub_gh(self, body):
        gh = self.bin / "gh"
        gh.write_text(f"#!/bin/sh\n{body}", encoding="utf-8")
        gh.chmod(0o755)

    def args(self, mode):
        run_dir = str(self.run_dir)
        common = ["--pr", "42", "--worktree", str(self.repo), "--head", self.head, "--run", run_dir]
        return [mode] + common + {
            "task": ["--base", "origin/main", "--task", f"{run_dir}/tasks/03-02-review-prompt.md"],
            "hub": ["--base", "origin/main", "--task", f"{run_dir}/tasks/03-09-hub.md",
                    "--ticket", f"{run_dir}/tickets/03-review.md", "--task-base", "origin/feat/03-review"],
            "ticket": ["--base", "origin/main", "--task", f"{run_dir}/tickets/03-review.md"],
            "re-review": ["--base", self.last, "--findings", f"{run_dir}/findings.md"],
            "security": ["--base", "origin/main", "--task", f"{run_dir}/tickets/03-review.md"],
        }[mode]

    def remote(self, args, allow=("rui",)):
        env = dict(os.environ, PATH=f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}")
        allowed = [a for login in allow for a in ("--allow-author", login)]
        return subprocess.run([sys.executable, "-I", str(SCRIPT), *args, *allowed, "--remote", str(self.out)],
                              capture_output=True, text=True, encoding="utf-8", env=env, cwd=self.tmp)

    def render(self, args):
        got = self.remote(args)
        self.assertEqual(got.returncode, 0, got.stderr)
        attach = (self.out / "attach.txt").read_text(encoding="utf-8").splitlines()
        return got.stdout, attach

    def test_a_remote_prompt_names_no_local_path_no_gh_no_git_history_and_no_report_line(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                out, _ = self.render(self.args(mode))
                self.assertIn("~/work", out)
                self.assertNotIn(str(self.tmp), out)
                self.assertIsNone(re.search(r"(^|[\s(`'\"=:])[/\\]", out), out)
                self.assertIsNone(re.search(r"\b[A-Za-z]:[/\\]", out), out)
                self.assertIsNone(re.search(r"\bgh\b", out), out)
                self.assertIsNone(re.search(r"\bgit\b|merge-base|\$\(", out), out)
                self.assertNotIn("report:", out)
                self.assertNotIn("the only file you may write", out)
                self.assertIn("Run tests only after you have read the whole diff", out)

    def test_a_remote_prompt_names_each_attachment_by_its_basename(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                out, attach = self.render(self.args(mode))
                names = [Path(path).name for path in attach]
                self.assertEqual(sorted(set(re.findall(r"~/attached/([^\s`,:;)]+)", out))), sorted(names))
                self.assertTrue(all(Path(path).is_absolute() and Path(path).is_file() for path in attach), attach)

    def test_the_attach_list_names_every_input_the_local_prompt_names_with_distinct_basenames(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                args = self.args(mode)
                local = run(*args)
                self.assertEqual(local.returncode, 0, local.stderr)
                named = {p for p in re.findall(r"(?:^|\s)(/\S+?)[,:]?(?=\s|$)", local.stdout, re.M)
                         if p.startswith(str(self.run_dir))}
                self.assertTrue(named)
                _, attach = self.render(args)
                self.assertTrue(named <= set(attach), (named, attach))
                self.assertIn(str(self.run_dir / "spec.md"), attach)
                names = [Path(path).name for path in attach]
                self.assertEqual(len(names), len(set(names)), names)

    def test_the_diff_and_the_pr_facts_are_written_for_attaching(self):
        out, attach = self.render(self.args("task"))
        diff = (self.out / "pr-42.diff").read_text(encoding="utf-8")
        self.assertIn("+two", diff)
        self.assertEqual(diff, git(self.repo, "diff", git(self.repo, "merge-base", "origin/main", self.head),
                                   self.head) + "\n")
        self.assertEqual((self.out / "pr-42.json").read_text(encoding="utf-8"), PR_FACTS)
        self.assertEqual(self.gh_log.read_text(encoding="utf-8"),
                         "pr view 42 --json author,title,body,headRefOid,baseRefName,files\n")
        self.assertIn(str(self.out / "pr-42.diff"), attach)
        self.assertIn(str(self.out / "pr-42.json"), attach)
        self.assertIn("cat ~/attached/pr-42.diff", out)
        self.assertIn("~/attached/pr-42.json", out)

    def test_hub_writes_the_task_diff_and_the_whole_ticket_diff(self):
        out, attach = self.render(self.args("hub"))
        a, b = out.index("## A."), out.index("## B.")
        self.assertIn("cat ~/attached/pr-42-task.diff", out[a:b])
        self.assertIn("~/attached/03-09-hub.md", out[a:b])
        self.assertIn("cat ~/attached/pr-42.diff", out[b:])
        self.assertIn("~/attached/03-review.md", out[b:])
        task = (self.out / "pr-42-task.diff").read_text(encoding="utf-8")
        whole = (self.out / "pr-42.diff").read_text(encoding="utf-8")
        self.assertNotIn("+ticket", task)
        self.assertIn("+ticket", whole)
        self.assertIn("+two", task)
        self.assertIn(str(self.out / "pr-42-task.diff"), attach)

    def test_hub_without_a_task_file_points_at_the_attached_pr_facts(self):
        args = self.args("hub")
        i = args.index("--task")
        out, _ = self.render(args[:i] + args[i + 2:])
        self.assertIn("the PR description, in ~/attached/pr-42.json", out)

    def test_inputs_sharing_a_basename_are_attached_under_distinct_names(self):
        for name in ("a/notes.md", "b/notes.md", "c/decisions.md"):
            (self.tmp / name).parent.mkdir(parents=True, exist_ok=True)
            (self.tmp / name).write_text(f"{name}\n", encoding="utf-8")
        args = self.args("hub")
        args[args.index("--task") + 1] = str(self.tmp / "a/notes.md")
        args[args.index("--ticket") + 1] = str(self.tmp / "b/notes.md")
        out, attach = self.render(args + ["--decisions", str(self.tmp / "c/decisions.md")])
        names = [Path(path).name for path in attach]
        self.assertEqual(len(names), len(set(names)), names)
        by_name = {Path(path).name: Path(path).read_text(encoding="utf-8") for path in attach}
        a, b = out.index("## A."), out.index("## B.")
        task = re.search(r"~/attached/(\S+)", out[a:]).group(1)
        ticket = re.search(r"~/attached/(\S+)", out[b:]).group(1)
        self.assertEqual(by_name[task], "a/notes.md\n")
        self.assertEqual(by_name[ticket], "b/notes.md\n")
        decisions = re.search(r"~/attached/(\S+): a rule it waives", out).group(1)
        self.assertEqual(by_name[decisions], "c/decisions.md\n")

    def test_without_a_run_only_the_diff_and_the_pr_facts_are_attached(self):
        args = self.args("task")
        i = args.index("--run")
        out, attach = self.render(args[:i] + args[i + 2:])
        self.assertEqual(sorted(Path(p).name for p in attach), ["03-02-review-prompt.md", "pr-42.diff", "pr-42.json"])
        self.assertNotIn("decisions", out)
        self.assertIn("the test commands the repo's rule files name", out)

    def test_remote_with_a_report_file_fails_with_usage(self):
        got = self.remote(self.args("task") + ["--report", str(self.tmp / "r.md")])
        self.assertEqual(got.returncode, 2, got.stdout + got.stderr)
        self.assertTrue(got.stderr.startswith("usage: review_prompt.py "), got.stderr)

    def test_a_pr_by_an_author_not_allowed_is_refused_and_prints_no_prompt(self):
        got = self.remote(self.args("task"), allow=("app/rpvsilva-agent", "someone"))
        self.assertEqual(got.returncode, 1, got.stdout + got.stderr)
        self.assertEqual(got.stdout, "")
        self.assertIn("author Rui", got.stderr)
        self.assertIn("not in --allow-author", got.stderr)
        self.assertFalse((self.out / "attach.txt").exists())
        self.assertFalse((self.out / "pr-42.diff").exists())

    def test_a_pr_by_any_allowed_author_renders_whatever_the_login_case(self):
        out, _ = self.render(self.args("task"))
        self.assertIn("~/work", out)
        got = self.remote(self.args("task"), allow=("app/rpvsilva-agent", "RUI"))
        self.assertEqual(got.returncode, 0, got.stderr)

    def test_a_pr_with_no_author_in_its_facts_is_refused(self):
        self.stub_gh("echo '{\"title\": \"t\"}'\n")
        got = self.remote(self.args("task"))
        self.assertEqual(got.returncode, 1, got.stdout + got.stderr)
        self.assertEqual(got.stderr, "review_prompt.py: author: pr-view names no author; review this PR locally\n")

    def test_remote_without_an_allowed_author_fails_with_usage(self):
        got = self.remote(self.args("task"), allow=())
        self.assertEqual(got.returncode, 2, got.stdout + got.stderr)
        self.assertTrue(got.stderr.startswith("usage: review_prompt.py "), got.stderr)

    def test_a_failing_gh_fails_and_prints_no_prompt(self):
        self.stub_gh("echo 'HTTP 404: no such PR' >&2\nexit 1\n")
        got = self.remote(self.args("task"))
        self.assertEqual(got.returncode, 1, got.stdout + got.stderr)
        self.assertEqual(got.stdout, "")
        self.assertIn("pr-view", got.stderr)
        self.assertIn("no such PR", got.stderr)

    def test_a_missing_input_to_attach_fails_and_prints_no_prompt(self):
        (self.run_dir / "map.md").unlink()
        got = self.remote(self.args("task"))
        self.assertEqual(got.returncode, 1, got.stdout + got.stderr)
        self.assertEqual(got.stdout, "")
        self.assertIn("map.md", got.stderr)

    def test_a_base_with_no_merge_base_fails_and_prints_no_prompt(self):
        got = self.remote(["task", "--pr", "42", "--worktree", str(self.repo), "--head", self.head,
                           "--base", "origin/none"])
        self.assertEqual(got.returncode, 1, got.stdout + got.stderr)
        self.assertEqual(got.stdout, "")
        self.assertIn("diff", got.stderr)


if __name__ == "__main__":
    unittest.main()
