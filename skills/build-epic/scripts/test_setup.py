"""Tests for `setup.py`: the run's test environment, test command, CI fact, identity and skipped tools, once."""
import io
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import setup as setup_step  # noqa: E402
from steplib import checks, core, decisions, identity  # noqa: E402
from testkit import SCRIPTS, Kit, env_tool_setup  # noqa: E402

SETUP = SCRIPTS / "setup.py"
REPO = SCRIPTS.parents[2]
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

# What every test command starts with: `capped` on PATH, then the caps it runs under, from the machine-level cap_mb
# each case sets and cap_secs' default.
CAPS = 'export PATH="$PATH:$HOME/.agents/bin" && export CAPPED_MB="${CAPPED_MB:-1500}" CAPPED_SECS="${CAPPED_SECS:-300}" && '

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
        self.kit.env.pop("XDG_CONFIG_HOME", None)
        (self.kit.home / ".agents" / "bin").mkdir(parents=True)
        (self.kit.home / ".agents" / "bin" / "settings").symlink_to(REPO / "scripts" / "settings.sh")
        self.write_map("echo suite ok")
        self.setting("max_parallel", "4")  # a Level sets it: no `parallel=`, but in the tests of the default
        self.setting("cap_mb", "1500")  # likewise for the memory cap: not the machine's share, but in its tests

    def write_map(self, checks_block, setup="Setup: none."):
        (self.run_dir / "map.md").write_text(MAP.format(checks=checks_block, setup=setup), encoding="utf-8")

    def workflows(self, files):
        """Commits `.github/workflows/<name>` files on `main` and pushes them."""
        clone = self.kit.clone
        (clone / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            (clone / ".github" / "workflows" / name).write_text(text, encoding="utf-8")
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

    def setting(self, key, value):
        p = self.kit.tool(self.kit.home / ".agents/bin/settings", "set", "build-epic", key, value, "--machine")
        self.assertEqual(p.returncode, 0, p.stderr)

    def suite_printing(self, out, only_with_all=False):
        """A Checks command printing `out` (with `--all` only, when `only_with_all`)."""
        script = self.kit.tmp / "suite.sh"
        guard = '[ "$1" = --all ] || exit 0\n' if only_with_all else ""
        script.write_text(f"{guard}cat <<'EOF'\n{out}EOF\n", encoding="utf-8")
        return f"bash {script.as_posix()}"  # a Windows path's backslashes would be escapes in bash


class ParallelFollowsTheMachine(SetupCase):
    def default_level(self):
        p = self.kit.tool(self.kit.home / ".agents/bin/settings", "unset", "build-epic", "max_parallel", "--machine")
        self.assertEqual(p.returncode, 0, p.stderr)

    def parallel_for(self, machine):
        self.kit.env["BUILD_EPIC_MACHINE"] = machine
        return self.setup()

    def test_ten_cores_and_16_gb_give_four(self):
        self.default_level()
        self.assertTrue(self.parallel_for("10,16").endswith(" parallel=4 (10 cores, 16 GB)"))

    def test_plenty_of_memory_leaves_the_cores_minus_two(self):
        self.default_level()
        self.assertIn(" parallel=2 (4 cores, 64 GB)", self.parallel_for("4,64"))

    def test_a_small_machine_still_gets_one(self):
        self.default_level()
        self.assertIn(" parallel=1 (2 cores, 4 GB)", self.parallel_for("2,4"))

    def test_a_big_machine_is_capped_at_six(self):
        self.default_level()
        self.assertIn(" parallel=6 (64 cores, 512 GB)", self.parallel_for("64,512"))

    def test_the_real_machine_is_probed_without_an_override(self):
        self.default_level()
        m = re.search(r" parallel=([1-6]) \((\d+) cores, (\d+) GB\)$", self.setup())
        self.assertTrue(m)
        self.assertEqual(int(m.group(2)), os.cpu_count())
        self.assertGreater(int(m.group(3)), 0)  # the memory was read, not left at 0

    def test_a_malformed_override_fails_bad_machine(self):
        self.default_level()
        self.kit.env["BUILD_EPIC_MACHINE"] = "lots"
        self.assertIn("bad-machine", self.setup(ok=False))

    def test_a_machine_level_max_parallel_prints_nothing(self):
        self.assertNotIn("parallel=", self.parallel_for("10,16"))

    def test_a_repo_level_max_parallel_prints_nothing(self):
        self.default_level()
        p = self.kit.tool(self.kit.home / ".agents/bin/settings", "--dir", str(self.kit.clone), "set", "build-epic",
                          "max_parallel", "3", "--repo")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("parallel=", self.parallel_for("10,16"))


class PrintsOneLine(SetupCase):
    def test_prints_ci_env_and_skips(self):
        self.workflows({"ci.yml": PR_INTO_MAIN})
        line = self.setup()
        self.assertEqual(line, f"OK run set up ci=on env={self.run_dir / 'env'} skips=0 caps=1500MB,300s")
        self.assertTrue((self.run_dir / "env").is_dir())

    def test_runs_from_a_linked_run_folder(self):
        linked = self.kit.link(self.run_dir, "linked-run")
        p = self.kit.step(SETUP, linked)
        self.assertEqual(p.stdout, f"OK run set up ci=off env={self.run_dir / 'env'} skips=0 caps=1500MB,300s\n",
                         p.stderr)

    def test_without_run_md_fails_with_its_log(self):
        (self.run_dir / "run.md").unlink()
        self.assertTrue(self.setup(ok=False).startswith("FAIL run no-run-md"))

    def test_without_checks_fails(self):
        (self.run_dir / "map.md").write_text("# Map\n\n## Checks\n\nSetup: none\n", encoding="utf-8")
        self.assertTrue(self.setup(ok=False).startswith("FAIL run no-checks"))

    def test_without_map_md_fails(self):
        (self.run_dir / "map.md").unlink()
        self.assertTrue(self.setup(ok=False).startswith("FAIL run no-map: "))

    def test_an_extra_argument_is_a_usage_failure(self):
        p = self.kit.step(SETUP, self.run_dir, "extra")
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertTrue(p.stdout.startswith("FAIL run usage: setup.py <run>"), p.stdout)
        self.assertFalse((self.run_dir / "env").exists())

    def test_a_setup_line_that_is_no_command_fails_unclear(self):
        self.write_map("echo hi", setup="Setup: make a venv first.")
        self.assertTrue(self.setup(ok=False).startswith("FAIL run unclear-setup: "))

    def test_a_setup_heading_with_a_note_and_no_command_fails_unclear(self):
        self.write_map("echo hi", setup="Setup (fresh worktree): python3 -m venv .venv")
        self.assertTrue(self.setup(ok=False).startswith("FAIL run unclear-setup: "))
        self.assertFalse((self.run_dir / "env" / ".setup-done").exists())

    def test_a_setup_line_in_another_form_fails_unclear_instead_of_meaning_none(self):
        for line in ("Setup - `touch x`", "Setup is a virtualenv: `touch x`"):
            with self.subTest(line):
                self.write_map("echo hi", setup=line)
                self.assertTrue(self.setup(ok=False).startswith("FAIL run unclear-setup: "))


class CapsFollowTheMachine(SetupCase):
    """Each test command's caps: cap_mb, else the machine's memory share per slot; cap_secs. Recorded and exported
    as defaults, so a sandbox's own caps win."""

    def unset(self, key):
        p = self.kit.tool(self.kit.home / ".agents/bin/settings", "unset", "build-epic", key, "--machine")
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_16_gb_over_the_four_slots_of_the_machine_default(self):
        self.unset("cap_mb")
        self.unset("max_parallel")
        self.kit.env["BUILD_EPIC_MACHINE"] = "10,16"
        # (16384 - 2048) / 4 - 1024
        self.assertIn(" caps=2560MB,300s ", self.setup())
        self.assertEqual(self.decided("caps"), "2560 300")

    def test_fewer_slots_a_level_sets_give_each_a_bigger_share(self):
        self.unset("cap_mb")
        self.setting("max_parallel", "2")
        self.kit.env["BUILD_EPIC_MACHINE"] = "10,16"
        self.assertTrue(self.setup().endswith(" caps=6144MB,300s"))

    def test_a_small_machine_still_gets_512_mb(self):
        self.unset("cap_mb")
        self.kit.env["BUILD_EPIC_MACHINE"] = "4,4"
        self.assertTrue(self.setup().endswith(" caps=512MB,300s"))

    def test_cap_secs_sets_the_time_cap(self):
        self.setting("cap_secs", "900")
        self.setup()
        self.assertEqual(self.decided("caps"), "1500 900")
        self.assertTrue(self.decided("test_command").startswith('export PATH="$PATH:$HOME/.agents/bin" && '
                                                               'export CAPPED_MB="${CAPPED_MB:-1500}" '
                                                               'CAPPED_SECS="${CAPPED_SECS:-900}" && '))

    def test_the_test_command_exports_the_caps_unless_already_set(self):
        self.write_map('echo "$CAPPED_MB $CAPPED_SECS"')
        self.setup()
        command = self.decided("test_command")
        env = {k: v for k, v in os.environ.items() if not k.startswith("CAPPED_")}
        mine = core.run(["bash", "-c", command], io.StringIO(), env=env)
        self.assertEqual(mine.out.strip(), "1500 300")
        sandbox = core.run(["bash", "-c", command], io.StringIO(), env={**env, "CAPPED_MB": "7000", "CAPPED_SECS": "60"})
        self.assertEqual(sandbox.out.strip(), "7000 60")

    def test_the_test_command_finds_capped_with_agents_bin_off_path(self):
        capped = self.kit.home / ".agents" / "bin" / "capped"
        capped.write_text('#!/bin/sh\n[ "$1" = -- ] && shift\nexec "$@"\n')
        capped.chmod(0o755)
        self.write_map("capped -- echo capped ran")
        self.setup()
        bare = {"PATH": "/usr/bin:/bin", "HOME": str(self.kit.home)}  # no ~/.agents/bin, as in the #16 baseline run
        for command in (self.decided("test_command"), checks.in_env(self.run_dir, "capped -- echo capped ran")):
            res = core.run(["bash", "-c", command], io.StringIO(), env=bare)
            self.assertEqual((res.rc, res.out.strip()), (0, "capped ran"), res.err)
            # `$HOME` stays unexpanded, so a remote sandbox finds its own ~/.agents/bin/capped.
            self.assertIn("$HOME/.agents/bin", command)
            self.assertNotIn(str(self.kit.home), command)

    def test_a_merge_runs_its_checks_under_the_recorded_caps(self):
        self.setup()
        self.assertEqual(checks.in_env(self.run_dir, "true"), CAPS + "true")

    def test_caps_must_be_two_whole_numbers(self):
        for bad in ("1500", "1500 0", "x 300", "1500 300 1"):
            with self.assertRaises(core.Fail):
                decisions.check_value("caps", bad)
        decisions.check_value("caps", "1500 300")


class RecordsTheRunsFacts(SetupCase):
    def test_records_test_command_human_and_identity(self):
        self.write_map("echo one\necho two")
        self.setup()
        self.assertEqual(self.decided("test_command"), CAPS + "echo one && echo two")
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
        self.assertEqual(self.decided("test_command"), CAPS + f"export BUILD_EPIC_ENV={env.as_posix()} && echo hi")

    def test_the_remote_test_command_makes_the_sandbox_env_once_then_runs_the_suites(self):
        # A remote sandbox has no <run>/env/: replays of #402 saw implementers build their own venvs in /tmp.
        make = 'mkdir -p "$BUILD_EPIC_ENV/bin" && echo made >> "$BUILD_EPIC_ENV/count" && touch "$BUILD_EPIC_ENV/bin/activate"'
        self.write_map('echo "suite in $BUILD_EPIC_ENV"', setup=f"Setup:\n\n```\n{make}\n```")
        self.setup()
        remote = self.decided("remote_test_command")
        self.assertNotIn(str(self.run_dir), remote)
        home = self.run_dir / "sandbox-home"
        home.mkdir()
        env = {"HOME": str(home), "PATH": os.environ["PATH"]}
        for _ in range(2):
            p = subprocess.run(["bash", "-c", remote], capture_output=True, text=True, env=env, cwd=home)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(p.stdout, f"suite in {home}/.build-epic-env\n")
        self.assertEqual((home / ".build-epic-env" / "count").read_text(), "made\n")

    def test_the_remote_test_command_without_setup_is_the_suites_under_the_caps(self):
        self.write_map("echo one\necho two")
        self.setup()
        self.assertEqual(self.decided("remote_test_command"), CAPS + "echo one && echo two")

    def test_test_command_activates_a_virtualenv_setup_made(self):
        activate = 'mkdir -p "$BUILD_EPIC_ENV/bin" && touch "$BUILD_EPIC_ENV/bin/activate"'
        self.write_map("echo hi", setup=f"Setup: `{activate}`")
        self.setup()
        env = self.run_dir / "env"
        self.assertEqual(self.decided("test_command"),
                         CAPS + f"export BUILD_EPIC_ENV={env.as_posix()} && . {env.as_posix()}/bin/activate && echo hi")

    def test_test_command_activates_a_windows_virtualenv_setup_made(self):
        activate = 'mkdir -p "$BUILD_EPIC_ENV/Scripts" && touch "$BUILD_EPIC_ENV/Scripts/activate"'
        self.write_map("echo hi", setup=f"Setup: `{activate}`")
        self.setup()
        env = self.run_dir / "env"
        self.assertEqual(self.decided("test_command"),
                         CAPS + f"export BUILD_EPIC_ENV={env.as_posix()} && . {env.as_posix()}/Scripts/activate && echo hi")

    def test_a_setup_heading_with_a_note_runs_its_code_spans(self):
        made = 'mkdir -p "$BUILD_EPIC_ENV" && echo ok > "$BUILD_EPIC_ENV/made"'
        self.write_map("echo hi", setup=f"Setup (fresh worktree): `{made}`")
        self.setup()
        env = self.run_dir / "env"
        self.assertTrue((env / "made").exists())
        self.assertEqual(self.decided("test_command"), CAPS + f"export BUILD_EPIC_ENV={env.as_posix()} && echo hi")

    def test_a_setup_heading_with_a_note_runs_its_block_and_the_block_is_no_suite(self):
        made = 'echo ok > "$BUILD_EPIC_ENV/made"'
        self.write_map("echo hi", setup=f"Setup (once, in the repo):\n\n```\n{made}\n```")
        self.setup()
        env = self.run_dir / "env"
        self.assertTrue((env / "made").exists())
        self.assertEqual(self.decided("test_command"), CAPS + f"export BUILD_EPIC_ENV={env.as_posix()} && echo hi")
        self.assertEqual(checks.commands(self.run_dir / "map.md"), ["echo hi"])

    def test_setup_none_puts_no_env_in_the_test_command(self):
        self.write_map("echo hi", setup="Setup (fresh worktree): none.")
        self.setup()
        self.assertEqual(self.decided("test_command"), CAPS + "echo hi")

    def test_failing_setup_fails_and_is_run_again_next_time(self):
        count = self.kit.tmp / "count"
        self.write_map("echo hi", setup=f"Setup: `echo x >> {count.as_posix()} && false`")
        self.assertTrue(self.setup(ok=False).startswith("FAIL run setup-failed"))
        self.setup(ok=False)
        self.assertEqual(count.read_text(encoding="utf-8"), "x\nx\n")


class DetectsCI(SetupCase):
    def test_pull_request_into_main_is_on_for_main(self):
        self.workflows({"ci.yml": PR_INTO_MAIN})
        self.assertIn(" ci=on ", self.setup())
        self.assertEqual(self.decided("ci"), "on")

    def test_pull_request_into_main_is_off_for_another_base(self):
        self.workflows({"ci.yml": PR_INTO_MAIN})
        self.kit.branch_from_main("int")
        (self.run_dir / "decisions.md").write_text("# Decisions\nbase: int\n", encoding="utf-8")
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

    def test_a_yaml_workflow_counts_and_other_files_do_not(self):
        self.workflows({"ci.yaml": PR_INTO_MAIN, "notes.md": PR_INTO_MAIN})
        self.assertIn(" ci=on ", self.setup())
        self.kit.git("rm", "-q", ".github/workflows/ci.yaml", cwd=self.kit.clone)
        self.workflows({})
        self.assertIn(" ci=off ", self.setup())

    def test_an_unreadable_actions_setting_lets_the_workflows_decide(self):
        self.kit.answer(PERMISSIONS, err="HTTP 403\n", rc=1)
        self.workflows({"ci.yml": PR_INTO_MAIN})
        self.assertIn(" ci=on ", self.setup())

    def test_a_base_origin_lacks_fails_recording_no_ci(self):
        (self.run_dir / "decisions.md").write_text("# Decisions\nbase: nowhere\n", encoding="utf-8")
        self.setup(ok=False)
        self.assertIsNone(self.decided("ci"))


class TriggerForms(unittest.TestCase):
    """The workflow parser alone: `DetectsCI` drives it through `setup.py` and git; these are its YAML forms."""

    def test_trigger_forms(self):
        for name, text, want in [
            ("bare", "on: pull_request\n", True),
            ("list", "on: [push, pull_request]\n", True),
            ("quoted key", '"on":\n  pull_request:\n', True),
            ("types only", "on:\n  pull_request:\n    types: [opened]\n", True),
            ("block list", "on:\n  pull_request:\n    branches:\n      - 'main'\n", True),
            ("other branch", "on:\n  pull_request:\n    branches:\n      - dev\n", False),
            ("glob", "on:\n  pull_request:\n    branches: ['ma*']\n", True),
            ("ignored", "on:\n  pull_request:\n    branches-ignore: [main]\n", False),
            ("negated", "on:\n  pull_request:\n    branches: ['*', '!main']\n", False),
            ("commented", "# on: pull_request\non:\n  push: # pull_request\n", False),
            ("target", "on:\n  pull_request_target:\n    branches: [main]\n", True),
            ("flow mapping", "on: {pull_request: {branches: [dev]}}\n", True),
            ("flow event", "on:\n  pull_request: {branches: [dev]}\n", True),
            ("double star", "on:\n  pull_request:\n    branches: ['**']\n", True),
            ("question mark", "on:\n  pull_request:\n    branches: ['mai?']\n", True),
            ("no on", "name: ci\njobs: {}\n", False),
        ]:
            with self.subTest(name):
                self.assertIs(setup_step.triggers_on_pr(text, "main"), want)

    def test_a_star_stays_within_a_path_segment(self):
        text = "on:\n  pull_request:\n    branches: ['release/*']\n"
        self.assertTrue(setup_step.triggers_on_pr(text, "release/1"))
        self.assertFalse(setup_step.triggers_on_pr(text, "release/1/x"))
        self.assertTrue(setup_step.triggers_on_pr(text.replace("/*", "/**"), "release/1/x"))


class ListsSkippedTools(SetupCase):
    def test_parsed_skip_reasons_land_in_skips_md_naming_the_tool(self):
        out = ("mod.test.sh: skipped (node not installed)\n"
               "== shellcheck: not installed, skipped (install shellcheck)\n"
               "SKIPPED [1] tests/test_x.py:3: requires docker\n")
        self.write_map(self.suite_printing(out))
        line = self.setup()
        self.assertTrue(line.endswith(" skips=3 caps=1500MB,300s"), line)
        skips = (self.run_dir / "skips.md").read_text(encoding="utf-8")
        for reason in checks.skip_reasons(out):
            self.assertIn(reason.reason, skips)
        for tool in ("node", "shellcheck", "docker"):
            self.assertRegex(skips, rf"(?m)^\| {tool} \|")

    def test_a_red_suite_still_lists_its_skips(self):
        self.write_map("echo '== tests/zsh.test.sh (0s)'; echo 'zsh.test.sh: skipped (zsh is not installed)'; exit 1")
        self.assertTrue(self.setup().endswith(" skips=1 caps=1500MB,300s"))
        self.assertRegex((self.run_dir / "skips.md").read_text(encoding="utf-8"), r"(?m)^\| zsh \|")

    def test_suites_run_in_a_throwaway_worktree_of_the_base_not_the_checkout(self):
        where = self.kit.tmp / "where"
        # Git Bash's `pwd -W` prints the folder as Windows names it; elsewhere `-W` is refused and `pwd` prints it.
        self.write_map(f"(pwd -W 2>/dev/null || pwd) > {where.as_posix()}; git rev-parse HEAD >> {where.as_posix()}")
        self.kit.git("commit", "-q", "--allow-empty", "-m", "local only", cwd=self.kit.clone)
        self.setup()
        ran_in, head = where.read_text(encoding="utf-8").split()
        self.assertNotEqual(Path(ran_in).resolve(), self.kit.clone.resolve())
        self.assertFalse(Path(ran_in).exists())
        self.assertEqual(head, self.kit.git("rev-parse", "origin/main", cwd=self.kit.clone))
        self.assertEqual(len(self.kit.git("worktree", "list", "--porcelain", cwd=self.kit.clone).split("\nworktree ")), 1)

    def test_a_checks_command_the_map_names_with_all_lists_every_suites_skips(self):
        suite = self.suite_printing("== tests/zsh.test.sh (0s)\nzsh.test.sh: skipped (zsh is not installed)\n",
                                    only_with_all=True)
        self.write_map(suite, setup=f"Setup: none. Before a ticket ships, run `{suite} --all`.")
        self.assertTrue(self.setup().endswith(" skips=1 caps=1500MB,300s"))
        self.assertEqual(self.decided("test_command"), CAPS + suite)

    def test_suites_run_in_the_runs_environment(self):
        self.write_map("envtool", setup=env_tool_setup("envtool", "SKIPPED [1] t.py:1: needs docker"))
        self.assertTrue(self.setup().endswith(" skips=1 caps=1500MB,300s"))
        self.assertRegex((self.run_dir / "skips.md").read_text(encoding="utf-8"), r"(?m)^\| docker \|")


class Reruns(SetupCase):
    def test_rerun_reuses_env_and_does_not_run_setup_again(self):
        count = self.kit.tmp / "count"
        self.write_map("echo hi", setup=f"Setup:\n\n```\necho x >> {count.as_posix()}\n```")
        first = self.setup()
        second = self.setup()
        self.assertEqual(first, second)
        self.assertEqual(count.read_text(encoding="utf-8"), "x\n")

    def test_rerun_keeps_recorded_identity_and_human_so_the_merge_still_refuses(self):
        self.setup()
        self.assertEqual((self.decided("merge_identity"), self.decided("human")), ("app", "rui"))
        self.setting("merge_identity", "human")
        self.kit.answer("api-user", out="someone-else\n")
        self.setup()
        self.assertEqual((self.decided("merge_identity"), self.decided("human")), ("app", "rui"))
        with self.assertRaises(core.Fail) as caught:
            identity.prove(self.run_dir, "human", self.kit.clone, io.StringIO())
        self.assertEqual(str(caught.exception), "wrong-identity")

    def test_rerun_records_a_human_left_unset_before(self):
        self.kit.answer("api-user", err="HTTP 401\n", rc=1)
        self.setup()
        self.assertIsNone(self.decided("human"))
        self.kit.answer("api-user", out="rui\n")
        self.setup()
        self.assertEqual(self.decided("human"), "rui")

    def test_rerun_keeps_skips_md_until_it_is_deleted(self):
        self.write_map("echo '== tests/zsh.test.sh (0s)'; echo 'zsh.test.sh: skipped (zsh is not installed)'")
        self.assertTrue(self.setup().endswith(" skips=1 caps=1500MB,300s"))
        self.write_map("echo all ran")
        self.assertTrue(self.setup().endswith(" skips=1 caps=1500MB,300s"))
        (self.run_dir / "skips.md").unlink()
        self.assertTrue(self.setup().endswith(" skips=0 caps=1500MB,300s"))


class SkipReasons(unittest.TestCase):
    def tools(self, out):
        return [(s.tool, s.reason) for s in checks.skip_reasons(out)]

    def test_reads_pytest_unittest_and_suite_lines(self):
        self.assertEqual(self.tools(
            "== tests/mod.test.sh (1s)\n"
            "SKIPPED [2] tests/test_a.py:10: docker is not installed\n"
            "test_z (test_s.Zsh) ... skipped 'zsh is not installed'\n"
            "mod.test.sh: skipped (opt in with REMOTE_SMOKE=1)\n"
            "WARN: node not installed, mod unit tests skipped (install node 22.6+)\n"
            "== shellcheck: 0.11.0 not installed and the docker daemon is not running, skipped (start docker)\n"
            "Ran 3 tests in 0.1s\nOK (skipped=1)\n"
            "all good\n"), [
            ("docker", "docker is not installed"),
            ("zsh", "zsh is not installed"),
            (None, "mod.test.sh: opt in with REMOTE_SMOKE=1"),
            ("node", "WARN: node not installed, mod unit tests (install node 22.6+)"),
            ("shellcheck", "shellcheck: 0.11.0 not installed and the docker daemon is not running (start docker)"),
        ])

    def test_they_are_the_reasons_counts_reads_each_once(self):
        out = ("== tests/a.test.sh (1s)\nzsh.test.sh: skipped (zsh is not installed)\n"
               "SKIPPED [1] t.py:1: no docker\nSKIPPED [1] t.py:2: no docker\n"
               "test_z (m.Z) ... skipped 'no git'\n")
        reasons = checks.counts(out)[3]
        self.assertEqual(reasons, ["zsh.test.sh: zsh is not installed", "no docker", "no docker", "no git"])
        self.assertEqual([s.reason for s in checks.skip_reasons(out)], list(dict.fromkeys(reasons)))

    def test_a_skip_named_twice_counts_once(self):
        out = "SKIPPED [1] a.py:1: requires docker\nSKIPPED [3] b.py:9: requires docker\n"
        self.assertEqual(self.tools(out), [("docker", "requires docker")])

    def test_count_summaries_are_not_skip_rows(self):
        for out in ("============ 3 passed, 2 skipped in 0.12s ============\n",
                    "3 passed, 2 skipped in 0.12s\n",
                    "Tests:       1 failed, 2 skipped, 40 passed, 43 total\n",
                    "== tests/a.test.sh (1s)\nTests:       2 skipped, 40 passed, 42 total\n",
                    "== tests/a.test.sh (1s)\n===== 3 passed, 2 skipped in 0.1s =====\n",
                    "Ran 3 tests in 0.1s\n\nOK (skipped=2)\n"):
            with self.subTest(out):
                self.assertEqual(self.tools(out), [])

    def test_a_framed_jest_summary_still_counts_its_skips(self):
        out = "== tests/a.test.sh (1s)\nTests:       2 skipped, 40 passed, 42 total\n"
        self.assertEqual(checks.counts(out), (40, 0, 2, []))

    def test_words_that_name_no_tool_are_never_the_tool(self):
        for reason, want in [
            ("no such file or directory", None),
            ("install a newer git first", None),
            ("requires network access", None),
            ("unit tests not found", None),
            ("needs such a thing", None),
            ("node 22.6+ is missing", "node"),
            ("requires docker", "docker"),
            ("no zsh", "zsh"),
        ]:
            with self.subTest(reason):
                self.assertEqual(self.tools(f"SKIPPED [1] t.py:1: {reason}\n"), [(want, reason)])


if __name__ == "__main__":
    unittest.main()
