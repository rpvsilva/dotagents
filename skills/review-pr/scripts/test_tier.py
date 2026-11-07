"""Tests for `tier.py`: the risk tier of a diff, on the trimmed gh-35 fixtures and on hand-written diffs."""
import os
import re
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
                              capture_output=True, text=True)

    def tier(self, text):
        path = self.tmp / "change.diff"
        path.write_text(text)
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
                got, signals = self.tier(path.read_text())
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
        got, signals = self.tier(diff([("app/mod.py", ["    # then save( it with open(path)", "// f.write(x)"])]))
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
        res = subprocess.run([str(tool), "set", "review-pr", "tier_max_lines", "5", "--machine"],
                             cwd=self.cwd, env=self.env(), capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        got, signals = self.tier(diff([("app/mod.py", ["x = 1"] * 6)]))
        self.assertEqual(got, "high")
        self.assertIn("size: 6 added source lines > 5", signals)

    def test_git_mode_tiers_the_branch_since_its_merge_base(self):
        repo = self.tmp / "repo"
        git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(["git", "init", "-q", str(repo)], check=True, env=self.env())
        (repo / "app.py").write_text("x = 1\n")
        subprocess.run(git + ["add", "-A"], check=True, env=self.env())
        subprocess.run(git + ["commit", "-qm", "base"], check=True, env=self.env())
        base = subprocess.run(git + ["rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        (repo / "app.py").write_text("x = 1\nopen('f').write('y')\n")
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
        path.write_text(full)
        res = self.run_tier("--trim", str(path))
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertNotIn("context", res.stdout)
        self.assertNotIn("old = 1", res.stdout)
        self.assertNotIn("@@", res.stdout)
        self.assertIn("gone.py", res.stdout)
        self.assertEqual(self.tier(full), self.tier(res.stdout))

    def test_fixtures_hold_no_home_paths(self):
        for path in FIXTURES.rglob("*.diff"):  # the trimmed PR diffs; review_prompt/ fixtures quote ~/.agents brief paths on purpose
            if path.is_file():
                with self.subTest(str(path.relative_to(HERE))):
                    self.assertIsNone(re.search(r"/Users/|/home/|~/", path.read_text()))


if __name__ == "__main__":
    unittest.main()
