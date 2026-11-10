"""Tests for the pilot `configure` cases in `evals/configure/`: each case's checks pass on the end state a good run
leaves, built with this checkout's `scripts/settings.sh set` in a run folder, and fail on the untouched fixture or on
an end state that breaks the case's rule."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
CASES = HERE / "configure"
sys.path.insert(0, str(HERE / "_runner"))

import cases  # noqa: E402
import checks  # noqa: E402
import home  # noqa: E402
from testlib import EvalSandbox  # noqa: E402

SETTINGS = REPO / "scripts" / "settings.sh"
NAMES = ("build-epic-only", "machine-three-agents", "repo-two-agents", "unknown-setting")


class PilotConfigure(EvalSandbox):
    def end_state(self, name, *sets, commit=False, edit=None, text="changed\n", then=()):
        """The run folder a run of case `name` leaves: its fixture as the git repo `work/`, then each `sets` entry
        (`<section> <key> <value> --repo|--machine`) applied by `settings.sh set` from `work/` with the run's home,
        then `text` appended to `edit` (a file of `work/`, made when missing), with `commit` everything committed,
        and then each `then` entry applied as `sets` are. Returns the case's checks' results."""
        case = cases.load(CASES / name)
        run = Path(tempfile.mkdtemp(prefix=name + "-", dir=self.tmp))
        paths = {"work": run / "work", "scratch": run / "scratch", "input": run / "input", "home": run / "home",
                 "transcript": run / "transcript.jsonl", "gh_calls": run / "gh-calls", "checkout": REPO}
        for key in ("scratch", "input", "home"):
            paths[key].mkdir(parents=True)
        paths["transcript"].write_text("")
        paths["gh_calls"].write_text("")
        home.make_work(case["fixture"], paths["work"], case.get("settings"))
        env = home.tool_env(paths["home"], ())
        def apply(entries):
            for args in entries:
                subprocess.run(["bash", str(SETTINGS), "set", *args.split()], cwd=paths["work"], env=env,
                               check=True, stdin=subprocess.DEVNULL, capture_output=True)

        apply(sets)
        if edit:
            with open(paths["work"] / edit, "a") as f:
                f.write(text)
        if commit:
            git = ["git", "-C", str(paths["work"]), "-c", "user.name=t", "-c", "user.email=t@localhost",
                   "-c", "commit.gpgsign=false"]
            subprocess.run(git + ["add", "-A"], check=True, env=env)
            subprocess.run(git + ["commit", "-qm", "x", "--no-verify"], check=True, env=env)
        apply(then)
        return checks.run(case["checks"], paths)

    @staticmethod
    def failed(results):
        return [n for n, ok in results.items() if not ok]

    def assertPasses(self, results):
        self.assertEqual(self.failed(results), [])

    def assertFails(self, results, *names):
        """Every check in `names` failed (at least one given)."""
        failed = [n for n, ok in results.items() if not ok]
        for n in names:
            self.assertIn(n, failed)
        self.assertTrue(failed)

    def test_the_four_cases_exist_and_are_well_formed(self):
        self.assertEqual(sorted(p.name for p in CASES.iterdir() if p.is_dir()), list(NAMES))
        self.assertEqual(cases.check(HERE), [])
        for name in NAMES:
            case = cases.load(CASES / name)
            self.assertEqual(case["model"], "claude-sonnet-5-5", name)
            self.assertEqual(case["runs"], 3, name)

    def test_every_prompt_names_its_scope_approves_and_says_leave_it_uncommitted(self):
        for name in NAMES:
            prompt = (CASES / name / "prompt.md").read_text().lower()
            self.assertIn("i approve", prompt, name)
            self.assertIn("uncommitted", prompt, name)
            self.assertTrue("this repo" in prompt or "this machine" in prompt, name)

    def test_every_check_is_an_executable_bash_script(self):
        for name in NAMES:
            for path in sorted((CASES / name / "checks").iterdir()):
                self.assertEqual(path.suffix, ".sh", path)
                self.assertTrue(os.access(path, os.X_OK), path)
                self.assertTrue(path.read_text().startswith("#!/usr/bin/env bash\n"), path)

    def test_repo_two_agents_passes_on_an_uncommitted_repo_write_only(self):
        self.assertPasses(self.end_state("repo-two-agents", "shared max_parallel 2 --repo"))
        self.assertFails(self.end_state("repo-two-agents"), "repo-max-parallel")
        self.assertFails(self.end_state("repo-two-agents", "shared max_parallel 3 --repo"), "repo-max-parallel")
        self.assertFails(self.end_state("repo-two-agents", "shared max_parallel 2 --repo", commit=True),
                         "uncommitted")
        self.assertFails(self.end_state("repo-two-agents", "shared max_parallel 2 --repo",
                                        "shared max_parallel 2 --machine"), "no-machine-file")
        self.assertEqual(self.failed(self.end_state("repo-two-agents", "shared max_parallel 3 --repo", commit=True,
                                                    then=("shared max_parallel 2 --repo",))), ["uncommitted"])

    def test_machine_three_agents_passes_on_a_machine_write_only(self):
        self.assertPasses(self.end_state("machine-three-agents", "shared max_parallel 3 --machine"))
        self.assertFails(self.end_state("machine-three-agents"), "machine-max-parallel")
        self.assertFails(self.end_state("machine-three-agents", "shared max_parallel 2 --machine"),
                         "machine-max-parallel")
        self.assertFails(self.end_state("machine-three-agents", "shared max_parallel 3 --machine",
                                        "shared max_parallel 3 --repo"), "no-repo-file")
        self.assertEqual(self.failed(self.end_state("machine-three-agents", "shared max_parallel 3 --machine",
                                                    edit="README.md")), ["no-repo-file"])
        self.assertEqual(self.failed(self.end_state("machine-three-agents", "shared max_parallel 3 --machine",
                                                    edit="README.md", commit=True)), ["no-repo-file"])

    def test_build_epic_only_passes_when_only_build_epic_changes_at_repo_level(self):
        self.assertPasses(self.end_state("build-epic-only", "build-epic max_parallel 1 --repo"))
        self.assertFails(self.end_state("build-epic-only"), "build-epic-max-parallel")
        self.assertFails(self.end_state("build-epic-only", "shared max_parallel 1 --repo"),
                         "build-epic-max-parallel", "shared-untouched")
        self.assertFails(self.end_state("build-epic-only", "build-epic max_parallel 1 --machine"),
                         "build-epic-max-parallel", "no-machine-file")
        self.assertFails(self.end_state("build-epic-only", "build-epic max_parallel 1 --repo", commit=True),
                         "uncommitted")
        self.assertEqual(self.failed(self.end_state("build-epic-only", "build-epic max_parallel 2 --repo", commit=True,
                                                    then=("build-epic max_parallel 1 --repo",))), ["uncommitted"])

    def test_unknown_setting_passes_only_when_nothing_is_written(self):
        self.assertPasses(self.end_state("unknown-setting"))
        self.assertFails(self.end_state("unknown-setting", "shared max_parallel 5 --repo"), "no-settings-file")
        self.assertFails(self.end_state("unknown-setting", "shared max_parallel 5 --machine"), "no-settings-file")
        self.assertFails(self.end_state("unknown-setting", edit="README.md"), "work-unchanged")
        self.assertFails(self.end_state("unknown-setting", edit="README.md", commit=True), "work-unchanged")

    def test_unknown_setting_fails_when_a_file_names_the_made_up_key(self):
        self.assertFails(self.end_state("unknown-setting", edit="NOTES.md", text="coffee_breaks: 2\n"),
                         "no-settings-file")
        self.assertFails(self.end_state("unknown-setting", edit="README.md", text="coffee_breaks: 2\n", commit=True),
                         "no-settings-file")
