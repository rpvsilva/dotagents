"""Tests for `review_prompt.py`: one golden prompt per mode, each a valid protocol prompt, and the usage failures."""
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
SCRIPT = HERE / "review_prompt.py"
GOLDEN = HERE / "fixtures" / "review_prompt"
CHECK = HERE.parents[1] / "_shared" / "protocol" / "check.py"
MODES = ("task", "ticket", "re-review", "security")
ROLE = {"task": "reviewer", "ticket": "reviewer", "re-review": "reviewer", "security": "security"}

COMMON = ["--pr", "42", "--worktree", "/wt/pr-42", "--head", "abc1234", "--base", "origin/main"]
RUN = ["--run", "/runs/r1"]
ARGS = {
    "task": COMMON + RUN + ["--task", "/runs/r1/tasks/03-02-review-prompt.md"],
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


def inputs(prompt):
    """The input object after the prompt's first line."""
    return json.loads(prompt.partition("\n")[2])


class ReviewPromptTest(unittest.TestCase):
    def setUp(self):
        # Resolved, as the child's cwd is: macOS tmp is under a /var symlink, Windows tmp under an 8.3 short name.
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        self.addCleanup(shutil.rmtree, self.tmp, True)
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

    def test_each_prompt_names_its_agent_and_brief_and_passes_its_roles_input_check(self):
        first = {"reviewer": "You are a pr-reviewer. Follow ~/.agents/skills/review-pr/reviewer-brief.md.",
                 "security": "You are a security-reviewer. Follow ~/.agents/skills/security-audit/security-brief.md."}
        for mode in MODES:
            with self.subTest(mode=mode):
                out = self.render(mode)
                self.assertEqual(out.splitlines()[0], first[ROLE[mode]])
                prompt = self.tmp / f"{mode}.txt"
                prompt.write_text(out, encoding="utf-8")
                got = subprocess.run([sys.executable, "-I", str(CHECK), ROLE[mode], "input", str(prompt)],
                                     capture_output=True, text=True, encoding="utf-8")
                self.assertEqual(got.stdout, f"OK {ROLE[mode]} input\n")

    def test_every_mode_diffs_against_the_merge_base(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                got = inputs(self.render(mode))
                base = ARGS[mode][ARGS[mode].index("--base") + 1]
                self.assertEqual(got["base"], base)
                self.assertIn(f"merge-base {base} abc1234", got["diff"])

    def test_the_mode_is_the_reviewers_and_security_is_the_pr_scope(self):
        for mode in ("task", "ticket", "re-review"):
            self.assertEqual(inputs(self.render(mode))["mode"], mode)
        got = inputs(self.render("security"))
        self.assertEqual(got["scope"], "pr")
        self.assertNotIn("mode", got)

    def test_re_review_names_the_numbered_findings_and_the_head_last_reviewed(self):
        got = inputs(self.render("re-review"))
        self.assertEqual(got["findings"], "@FINDINGS@")
        self.assertEqual(got["base"], "def5678")
        self.assertNotIn("task", got)

    def test_a_reviewer_reads_the_runs_map_and_a_security_reviewer_its_checks(self):
        self.assertEqual(inputs(self.render("ticket"))["map"], "/runs/r1/map.md")
        got = inputs(self.render("security"))
        self.assertEqual(got["checks"], "/runs/r1/map.md")
        self.assertNotIn("map", got)

    def test_without_a_run_the_run_files_are_left_out(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                args = [a for a in ARGS[mode] if a not in RUN]
                got = inputs(self.render(mode, args))
                for key in ("decisions", "ci", "test_command", "map", "checks", "spec"):
                    self.assertNotIn(key, got)

    def test_a_decisions_file_is_named_with_or_without_a_run(self):
        args = [a for a in ARGS["task"] if a not in RUN]
        got = inputs(self.render("task", args + ["--decisions", "/d/decisions.md"]))
        self.assertEqual(got["decisions"], "/d/decisions.md")
        self.assertNotIn("map", got)
        got = inputs(self.render("task", ARGS["task"] + ["--decisions", "/d/decisions.md"]))
        self.assertEqual(got["decisions"], "/d/decisions.md")
        self.assertEqual(got["map"], "/runs/r1/map.md")

    def test_the_decisions_files_ci_and_test_command_lines_become_inputs(self):
        decisions = self.tmp / "decisions.md"
        decisions.write_text("# Decisions\nbase: main\nci: off\ntest_command: capped -- tests/run.sh\n\n"
                             "## Decisions\n\n- 2026-11-06: ci: on\n", encoding="utf-8")
        args = [a for a in ARGS["task"] if a not in RUN] + ["--decisions", str(decisions)]
        got = inputs(run("task", *args).stdout)
        self.assertEqual((got["ci"], got["test_command"]), ("off", "capped -- tests/run.sh"))
        got = inputs(run("security", *[a for a in ARGS["security"] if a not in RUN], "--decisions",
                         str(decisions)).stdout)
        self.assertEqual(got["test_command"], "capped -- tests/run.sh")
        self.assertNotIn("ci", got)
        decisions.write_text("# Decisions\nci: maybe\ntest_command:\n", encoding="utf-8")
        got = inputs(run("task", *args).stdout)
        self.assertNotIn("ci", got)
        self.assertNotIn("test_command", got)

    def test_the_runs_spec_is_named_when_it_exists(self):
        run_dir = self.tmp / "run"
        run_dir.mkdir()
        args = ["--pr", "1", "--worktree", "/w", "--head", "h", "--base", "b", "--run", str(run_dir)]
        self.assertNotIn("spec", inputs(run("ticket", *args).stdout))
        (run_dir / "spec.md").write_text("spec\n", encoding="utf-8")
        self.assertEqual(inputs(run("ticket", *args).stdout)["spec"], str(run_dir / "spec.md"))
        self.assertNotIn("spec", inputs(run("security", *args).stdout))

    def test_an_output_file_is_named_as_the_output_input(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                args = [a.replace("@FINDINGS@", str(self.findings)) for a in ARGS[mode]]
                for option in ("--output", "--report"):
                    got = run(mode, *args, option, "reviews/42-abc1234.json", cwd=self.tmp)
                    self.assertEqual(got.returncode, 0, got.stderr)
                    self.assertEqual(inputs(got.stdout)["output"], str(self.tmp / "reviews" / "42-abc1234.json"))
                self.assertNotIn("output", inputs(self.render(mode)))

    def test_relative_paths_are_made_absolute(self):
        got = run("task", "--pr", "1", "--worktree", "wt", "--head", "h", "--base", "b", cwd=self.tmp)
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertEqual(inputs(got.stdout)["worktree"], str(Path(self.tmp, "wt")))

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
        subprocess.run([BASH, "-c", inputs(got.stdout)["diff"]], cwd=self.tmp, capture_output=True, text=True,
                       encoding="utf-8")
        self.assertFalse((self.tmp / "pwned").exists() or (self.tmp / "pwned2").exists())
        got = run("task", "--pr", "1", "--worktree", str(repo), "--head", "HEAD", "--base", "HEAD~1")
        res = subprocess.run([BASH, "-c", inputs(got.stdout)["diff"]], cwd=self.tmp, capture_output=True, text=True,
                             encoding="utf-8")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("+two", res.stdout)

    def assert_usage(self, got):
        self.assertEqual(got.returncode, 2, got.stdout + got.stderr)
        self.assertEqual(got.stdout, "")
        self.assertTrue(got.stderr.startswith("usage: review_prompt.py "), got.stderr)

    def test_an_unknown_mode_fails_with_usage(self):
        self.assert_usage(run("deep", *COMMON))

    def test_hub_mode_is_gone(self):
        self.assert_usage(run("hub", *COMMON, "--task", "/t.md"))
        self.assert_usage(run("ticket", *COMMON, "--ticket", "/t.md", "--task-base", "origin/feat/x"))

    def test_re_review_without_findings_fails_with_usage(self):
        self.assert_usage(run("re-review", *COMMON))

    def test_re_review_with_a_missing_findings_file_fails_with_usage(self):
        self.assert_usage(run("re-review", *COMMON, "--findings", str(self.tmp / "none.md")))

    def test_a_missing_required_option_fails_with_usage(self):
        self.assert_usage(run("task", "--pr", "42"))

    def test_an_input_the_schema_refuses_fails_with_usage(self):
        self.assert_usage(run("task", "--pr", "-3", "--worktree", "/w", "--head", "h", "--base", "b"))
        self.assert_usage(run("task", "--pr", "3", "--worktree", "/w", "--head", "", "--base", "b"))

    # A task's commits before they merge, with no PR (build-epic's high-tier tasks)

    NO_PR = ["--worktree", "/runs/r1/worktrees/03-02", "--head", "abc1234", "--base", "feat/03-review", *RUN,
             "--task", "/runs/r1/tasks/03-02-review-prompt.md", "--output", "/runs/r1/reviews/03-02-abc1234.json"]

    def test_a_task_with_no_pr_is_reviewed_from_its_task_file_and_local_commits(self):
        got = inputs(self.render("task", self.NO_PR))
        self.assertNotIn("pr", got)
        self.assertEqual(got["task"], "/runs/r1/tasks/03-02-review-prompt.md")
        self.assertEqual(got["diff"], "git -C /runs/r1/worktrees/03-02 diff $(git -C /runs/r1/worktrees/03-02 "
                                      "merge-base feat/03-review abc1234) abc1234")
        self.assertEqual(got["output"], "/runs/r1/reviews/03-02-abc1234.json")

    def test_with_no_pr_only_a_local_task_review_runs(self):
        no_task = [a for a in self.NO_PR if a != "/runs/r1/tasks/03-02-review-prompt.md" and a != "--task"]
        self.assert_usage(run("task", *no_task))
        for mode in ("ticket", "re-review", "security"):
            self.assert_usage(run(mode, *self.NO_PR, "--findings", str(self.findings)))
        self.assert_usage(run("task", *self.NO_PR[:-2], "--remote", str(self.tmp / "remote"),
                              "--allow-author", "rui"))


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
        self.last = git(self.repo, "rev-parse", "HEAD")
        self.head = self.commit("f.txt", "two\n")
        self.run_dir = self.tmp / "run"
        for name in ("decisions.md", "map.md", "spec.md", "tasks/03-02-review-prompt.md", "tickets/03-review.md",
                     "findings.md"):
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

    def test_a_remote_prompt_names_no_local_path_no_gh_no_git_history_and_no_output(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                out, _ = self.render(self.args(mode))
                got = inputs(out)
                self.assertEqual(got["worktree"], "~/work")
                self.assertIs(got["remote"], True)
                self.assertNotIn("output", got)
                self.assertNotIn(str(self.tmp), out)
                values = [v for k, v in got.items() if isinstance(v, str) and k != "head" and k != "base"]
                for value in values:
                    self.assertIsNone(re.search(r"(^|[\s(`'\"=:])[/\\]", value), value)
                    self.assertIsNone(re.search(r"\b[A-Za-z]:[/\\]", value), value)
                    self.assertIsNone(re.search(r"\bgh\b|\bgit\b|merge-base|\$\(", value), value)

    def test_a_remote_review_gets_the_remote_test_command(self):
        # A sandbox has no <run>/env/, so the local test_command would point at nothing there.
        (self.run_dir / "decisions.md").write_text(
            "# Decisions\ntest_command: local-env-test\nremote_test_command: build-env-then-test\n", encoding="utf-8")
        out, _ = self.render(self.args("ticket"))
        self.assertEqual(inputs(out)["test_command"], "build-env-then-test")

    def test_a_remote_review_names_the_agent_whose_model_it_runs_on(self):
        # Without --agent, remote-agent runs the sandbox's default model: Opus for every reviewer in step D's replay 4.
        for mode in MODES:
            with self.subTest(mode=mode):
                self.render(self.args(mode))
                want = "security-reviewer" if mode == "security" else "pr-reviewer"
                self.assertEqual((self.out / "agent.txt").read_text(encoding="utf-8"), want + "\n")

    def test_a_remote_prompt_names_each_attachment_by_its_basename(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                out, attach = self.render(self.args(mode))
                names = [Path(path).name for path in attach]
                self.assertEqual(sorted(set(re.findall(r"~/attached/([^\s\"`,:;)]+)", out))), sorted(names))
                self.assertTrue(all(Path(path).is_absolute() and Path(path).is_file() for path in attach), attach)

    def test_the_attach_list_names_every_input_the_local_prompt_names_with_distinct_basenames(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                args = self.args(mode)
                local = run(*args)
                self.assertEqual(local.returncode, 0, local.stderr)
                named = {v for v in inputs(local.stdout).values()
                         if isinstance(v, str) and v.startswith(str(self.run_dir))}
                self.assertTrue(named)
                _, attach = self.render(args)
                self.assertTrue(named <= set(attach), (named, attach))
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
        got = inputs(out)
        self.assertEqual(got["diff"], "cat ~/attached/pr-42.diff")
        self.assertEqual(got["pr_facts"], "~/attached/pr-42.json")

    def test_inputs_sharing_a_basename_are_attached_under_distinct_names(self):
        for name in ("a/notes.md", "b/notes.md", "c/decisions.md"):
            (self.tmp / name).parent.mkdir(parents=True, exist_ok=True)
            (self.tmp / name).write_text(f"{name}\n", encoding="utf-8")
        args = self.args("re-review")
        args[args.index("--findings") + 1] = str(self.tmp / "b/notes.md")
        out, attach = self.render(args + ["--task", str(self.tmp / "a/notes.md"),
                                          "--decisions", str(self.tmp / "c/decisions.md")])
        names = [Path(path).name for path in attach]
        self.assertEqual(len(names), len(set(names)), names)
        by_name = {Path(path).name: Path(path).read_text(encoding="utf-8") for path in attach}
        got = {k: v.removeprefix("~/attached/") for k, v in inputs(out).items() if isinstance(v, str)}
        self.assertEqual(by_name[got["task"]], "a/notes.md\n")
        self.assertEqual(by_name[got["findings"]], "b/notes.md\n")
        self.assertEqual(by_name[got["decisions"]], "c/decisions.md\n")

    def test_without_a_run_only_the_diff_and_the_pr_facts_are_attached(self):
        args = self.args("task")
        i = args.index("--run")
        out, attach = self.render(args[:i] + args[i + 2:])
        self.assertEqual(sorted(Path(p).name for p in attach), ["03-02-review-prompt.md", "pr-42.diff", "pr-42.json"])
        self.assertNotIn("decisions", inputs(out))

    def test_remote_with_an_output_file_fails_with_usage(self):
        got = self.remote(self.args("task") + ["--output", str(self.tmp / "r.json")])
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
        self.render(self.args("task"))
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
