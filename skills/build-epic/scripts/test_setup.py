"""Tests for `setup.py`: the run's test environment, test command, CI fact, identity and skipped tools, once."""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import checks, decisions  # noqa: E402
from testkit import SCRIPTS, Kit  # noqa: E402

SETUP = SCRIPTS / "setup.py"
PERMISSIONS = "api-repos__owner___repo__actions_permissions"

PR_INTO_MAIN = """name: ci
on:
  pull_request:
    branches: [main]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
"""

MAP = """# Map

## Checks

```
{checks}
```

{setup}

## Gaps

- none
"""


class SetupCase(unittest.TestCase):
    def setUp(self):
        self.kit = Kit()
        self.addCleanup(self.kit.close)
        self.kit.make_origin()
        self.kit.init_run()
        self.run_dir = self.kit.run_dir
        self.kit.answer("api-user", out="rui\n")
        self.kit.answer(PERMISSIONS, out="true\n")
        self.write_map("echo suite ok")

    def write_map(self, checks_block, setup="Setup: none."):
        (self.run_dir / "map.md").write_text(MAP.format(checks=checks_block, setup=setup))

    def workflows(self, files):
        """Commits `.github/workflows/<name>` files on `main` and pushes them."""
        clone = self.kit.clone
        (clone / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            (clone / ".github" / "workflows" / name).write_text(text)
        self.kit.git("add", ".github", cwd=clone)
        self.kit.git("commit", "-qm", "ci", cwd=clone)
        self.kit.git("push", "-q", "origin", "main", cwd=clone)

    def setup(self, ok=True):
        p = self.kit.step(SETUP, self.run_dir)
        lines = p.stdout.splitlines()
        self.assertEqual(len(lines), 1, p.stdout + p.stderr)
        self.assertEqual(p.returncode == 0, ok, p.stdout + p.stderr)
        if not ok:
            m = re.search(r"\(log: (.+)\)$", lines[0])
            self.assertTrue(m and Path(m.group(1)).exists(), lines[0])
        return lines[0]

    def decided(self, key):
        return decisions.get(self.run_dir, key)


class PrintsOneLine(SetupCase):
    def test_prints_ci_env_and_skips(self):
        self.workflows({"ci.yml": PR_INTO_MAIN})
        line = self.setup()
        self.assertEqual(line, f"OK run set up ci=on env={self.run_dir / 'env'} skips=0")
        self.assertTrue((self.run_dir / "env").is_dir())

    def test_runs_from_a_linked_run_folder(self):
        linked = self.kit.link(self.run_dir, "linked-run")
        p = self.kit.step(SETUP, linked)
        self.assertEqual(p.stdout, f"OK run set up ci=off env={self.run_dir / 'env'} skips=0\n", p.stderr)

    def test_without_run_md_fails_with_its_log(self):
        (self.run_dir / "run.md").unlink()
        self.assertTrue(self.setup(ok=False).startswith("FAIL run no-run-md"))

    def test_without_checks_fails(self):
        (self.run_dir / "map.md").write_text("# Map\n\n## Checks\n\nSetup: none\n")
        self.assertTrue(self.setup(ok=False).startswith("FAIL run no-checks"))


class RecordsTheRunsFacts(SetupCase):
    def test_records_test_command_human_and_identity(self):
        self.write_map("echo one\necho two")
        self.setup()
        self.assertEqual(self.decided("test_command"), "echo one && echo two")
        self.assertEqual(self.decided("human"), "rui")
        self.assertEqual(self.decided("merge_identity"), "app")
        self.assertEqual(self.decided("ci"), "off")

    def test_human_unknown_is_left_unrecorded(self):
        self.kit.answer("api-user", err="HTTP 401: Bad credentials\n", rc=1)
        self.setup()
        self.assertIsNone(self.decided("human"))

    def test_test_command_carries_the_env(self):
        make = 'mkdir -p "$BUILD_EPIC_ENV/bin" && echo ok > "$BUILD_EPIC_ENV/made"'
        self.write_map("echo hi", setup=f"Setup:\n\n```\n{make}\n```")
        self.setup()
        env = self.run_dir / "env"
        self.assertTrue((env / "made").exists())
        self.assertEqual(self.decided("test_command"), f"export BUILD_EPIC_ENV={env} && echo hi")

    def test_test_command_activates_a_virtualenv_setup_made(self):
        activate = 'mkdir -p "$BUILD_EPIC_ENV/bin" && touch "$BUILD_EPIC_ENV/bin/activate"'
        self.write_map("echo hi", setup=f"Setup: `{activate}`")
        self.setup()
        env = self.run_dir / "env"
        self.assertEqual(self.decided("test_command"),
                         f"export BUILD_EPIC_ENV={env} && . {env}/bin/activate && echo hi")

    def test_failing_setup_fails_and_is_run_again_next_time(self):
        count = self.kit.tmp / "count"
        self.write_map("echo hi", setup=f"Setup: `echo x >> {count} && false`")
        self.assertTrue(self.setup(ok=False).startswith("FAIL run setup-failed"))
        self.setup(ok=False)
        self.assertEqual(count.read_text(), "x\nx\n")


class DetectsCI(SetupCase):
    def test_pull_request_into_main_is_on_for_main(self):
        self.workflows({"ci.yml": PR_INTO_MAIN})
        self.assertIn(" ci=on ", self.setup())
        self.assertEqual(self.decided("ci"), "on")

    def test_pull_request_into_main_is_off_for_another_base(self):
        self.workflows({"ci.yml": PR_INTO_MAIN})
        self.kit.branch_from_main("int")
        (self.run_dir / "decisions.md").write_text("# Decisions\nbase: int\n")
        self.assertIn(" ci=off ", self.setup())
        self.assertEqual(self.decided("ci"), "off")
        self.assertEqual(self.decided("base"), "int")

    def test_actions_disabled_is_off(self):
        self.workflows({"ci.yml": PR_INTO_MAIN})
        self.kit.answer(PERMISSIONS, out="false\n")
        self.assertIn(" ci=off ", self.setup())

    def test_no_workflow_is_off(self):
        self.assertIn(" ci=off ", self.setup())

    def test_push_only_is_off(self):
        self.workflows({"ci.yml": "on:\n  push:\n    branches: [main]\njobs: {}\n"})
        self.assertIn(" ci=off ", self.setup())

    def test_trigger_forms(self):
        for name, text, want in [
            ("bare", "on: pull_request\n", "on"),
            ("list", "on: [push, pull_request]\n", "on"),
            ("quoted key", '"on":\n  pull_request:\n', "on"),
            ("types only", "on:\n  pull_request:\n    types: [opened]\n", "on"),
            ("block list", "on:\n  pull_request:\n    branches:\n      - 'main'\n", "on"),
            ("other branch", "on:\n  pull_request:\n    branches:\n      - dev\n", "off"),
            ("glob", "on:\n  pull_request:\n    branches: ['ma*']\n", "on"),
            ("ignored", "on:\n  pull_request:\n    branches-ignore: [main]\n", "off"),
            ("negated", "on:\n  pull_request:\n    branches: ['*', '!main']\n", "off"),
            ("commented", "# on: pull_request\non:\n  push: # pull_request\n", "off"),
        ]:
            with self.subTest(name):
                self.workflows({"ci.yml": text})
                self.assertIn(f" ci={want} ", self.setup())


class ListsSkippedTools(SetupCase):
    def test_parsed_skip_reasons_land_in_skips_md_naming_the_tool(self):
        out = ("mod.test.sh: skipped (node not installed)\n"
               "== shellcheck: not installed, skipped (install shellcheck)\n"
               "SKIPPED [1] tests/test_x.py:3: requires docker\n")
        script = self.kit.tmp / "suite.sh"
        script.write_text(f"cat <<'EOF'\n{out}EOF\n")
        self.write_map(f"bash {script}")
        line = self.setup()
        self.assertTrue(line.endswith(" skips=3"), line)
        skips = (self.run_dir / "skips.md").read_text()
        for reason in checks.skip_reasons(out):
            self.assertIn(reason.reason, skips)
        for tool in ("node", "shellcheck", "docker"):
            self.assertRegex(skips, rf"(?m)^\| {tool} \|")

    def test_a_red_suite_still_lists_its_skips(self):
        self.write_map("echo 'zsh.test.sh: skipped (zsh is not installed)'; exit 1")
        self.assertTrue(self.setup().endswith(" skips=1"))
        self.assertRegex((self.run_dir / "skips.md").read_text(), r"(?m)^\| zsh \|")


class Reruns(SetupCase):
    def test_rerun_reuses_env_and_does_not_run_setup_again(self):
        count = self.kit.tmp / "count"
        self.write_map("echo hi", setup=f"Setup:\n\n```\necho x >> {count}\n```")
        first = self.setup()
        second = self.setup()
        self.assertEqual(first, second)
        self.assertEqual(count.read_text(), "x\n")


class SkipReasons(unittest.TestCase):
    def test_reads_pytest_unittest_and_suite_lines(self):
        got = checks.skip_reasons(
            "SKIPPED [2] tests/test_a.py:10: docker is not installed\n"
            "test_z (test_s.Zsh) ... skipped 'zsh is not installed'\n"
            "mod.test.sh: skipped (opt in with REMOTE_SMOKE=1)\n"
            "WARN: node not installed, mod unit tests skipped (install node 22.6+)\n"
            "Ran 3 tests in 0.1s\nOK (skipped=1)\n"
            "all good\n")
        self.assertEqual([(s.tool, s.reason) for s in got], [
            ("docker", "docker is not installed"),
            ("zsh", "zsh is not installed"),
            (None, "mod.test.sh: skipped (opt in with REMOTE_SMOKE=1)"),
            ("node", "WARN: node not installed, mod unit tests skipped (install node 22.6+)"),
        ])


if __name__ == "__main__":
    unittest.main()
