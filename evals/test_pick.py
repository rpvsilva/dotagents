"""Tests for `pick.py`: the eval cases a change affects, run in a throwaway repo holding a copy of `pick.py` and
`tests/affected.sh` and `scripts/parts.sh`, with skills that have cases (`a`, `b`), one without (`c`), parts they
need or install (`lib`, `tool`, the core skill `configure`) and a shared Settings file."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
A = ["a/one", "a/two"]
B = ["b/one", "b/two"]
EVERY = A + B
SHARED = {"max_parallel": {"kind": "int", "default": 4, "meaning": "m"},
          "tracker": {"kind": "string", "default": None, "meaning": "t"}}


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                    *args], check=True, capture_output=True, stdin=subprocess.DEVNULL)


class PickTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        r = self.repo = self.tmp / "repo"
        (r / "tests").mkdir(parents=True)
        shutil.copy(REPO / "tests" / "affected.sh", r / "tests")
        (r / "tests" / "suites.txt").write_text("repo.test.sh *\n")
        (r / "evals" / "_runner").mkdir(parents=True)
        shutil.copy(REPO / "evals" / "pick.py", r / "evals")
        (r / "scripts").mkdir()
        shutil.copy(REPO / "scripts" / "parts.sh", r / "scripts")
        self.write("groups.json", json.dumps({"core": {"about": "c"}, "g": {"about": "g"}}))
        self.write("evals/_runner/cases.py", "")
        self.write("skills/_shared/conventions.md", "# Conventions\n")
        self.write("skills/_shared/settings.json", json.dumps(SHARED))
        for skill, opt_in in (("a", "max_parallel"), ("b", "tracker"), ("c", "max_parallel")):
            self.write(f"skills/{skill}/SKILL.md", f"---\nname: {skill}\n---\n")
            self.write(f"skills/{skill}/settings.json", json.dumps({opt_in: {"shared": True}}))
        needs = {"a": {"lib": "optional", "configure": "required"}, "b": {}, "c": {"lib": "required"}, "lib": {},
                 "tool": {}}
        for skill, need in needs.items():
            self.write(f"skills/{skill}/needs.json", json.dumps({"group": "g", "needs": need}))
        for skill in ("lib", "tool", "configure"):
            self.write(f"skills/{skill}/SKILL.md", f"---\nname: {skill}\n---\n")
        self.write("skills/configure/needs.json", json.dumps({"group": "core"}))
        self.write("skills/lib/brief.md", "brief\n")
        self.write("adapters/claude/a/helper.py", "")
        for case in EVERY:
            self.write(f"evals/{case}/case.json", "{}")
        self.write("evals/b/one/case.json", json.dumps({"install": ["b", "tool"]}))
        self.write("docs/x.md", "x\n")
        git(r, "init", "-q")
        git(r, "add", ".")
        git(r, "commit", "-qm", "base")
        git(r, "update-ref", "refs/remotes/origin/main", "HEAD")

    def write(self, rel, text):
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def pick(self, *args):
        got = subprocess.run([sys.executable, "-I", str(self.repo / "evals" / "pick.py"), *args], cwd=self.tmp,
                             capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(got.returncode, 0, got.stderr)
        return got.stdout.splitlines()

    def change_shared(self, **defaults):
        data = json.loads(json.dumps(SHARED))
        for key, value in defaults.items():
            data.setdefault(key, {"kind": "int", "meaning": "n"})["default"] = value
        self.write("skills/_shared/settings.json", json.dumps(data, indent=2))


class SkillFileTest(PickTest):
    def test_a_file_under_a_skill_picks_that_skills_cases(self):
        self.assertEqual(self.pick("--", "skills/a/SKILL.md"), A)
        self.assertEqual(self.pick("--", "skills/b/planner-brief.md"), B)

    def test_a_test_file_or_fixture_under_a_skill_picks_nothing(self):
        self.assertEqual(self.pick("--", "skills/a/test_plan.py", "skills/a/fixtures/x.md",
                                   "skills/a/sub/fixtures/y.md", "skills/a/lib/test_y.py"), [])

    def test_a_file_under_a_skills_evals_picks_that_skills_cases(self):
        self.assertEqual(self.pick("--", "evals/a/one/prompt.md"), A)

    def test_changed_files_come_from_the_working_tree_since_origin_main(self):
        self.write("skills/b/SKILL.md", "---\nname: b\ndescription: new\n---\n")
        self.assertEqual(self.pick(), B)

    def test_base_compares_against_the_given_ref(self):
        self.write("skills/a/SKILL.md", "changed\n")
        git(self.repo, "commit", "-qam", "a")
        self.write("skills/b/SKILL.md", "changed\n")
        git(self.repo, "commit", "-qam", "b")
        self.assertEqual(self.pick("--base", "HEAD~1"), B)
        self.assertEqual(self.pick(), EVERY)


class PartTest(PickTest):
    def test_a_part_a_skill_needs_picks_that_skills_cases(self):
        self.assertEqual(self.pick("--", "skills/lib/brief.md"), A)

    def test_a_skills_adapter_helper_picks_that_skills_cases(self):
        self.assertEqual(self.pick("--", "adapters/claude/a/helper.py"), A)

    def test_a_part_a_case_installs_picks_only_that_case(self):
        self.assertEqual(self.pick("--", "skills/tool/SKILL.md"), ["b/one"])

    def test_a_test_file_or_fixture_under_a_needed_part_picks_nothing(self):
        self.write("skills/lib/test_x.py", "")
        self.write("skills/lib/fixtures/x.md", "x\n")
        self.assertEqual(self.pick("--", "skills/lib/test_x.py", "skills/lib/fixtures/x.md"), [])

    def test_a_core_part_picks_no_case_of_a_skill_needing_it(self):
        self.assertEqual(self.pick("--", "skills/configure/SKILL.md"), [])

    def test_a_core_file_a_skill_names_by_its_installed_path_picks_that_skills_cases(self):
        self.write("scripts/settings.sh", "")
        self.write("scripts/hooks/lib.sh", "")
        self.write("skills/configure/SKILL.md", "---\nname: configure\n---\nRun `~/.agents/bin/settings list`.\n")
        self.write("evals/configure/one/case.json", "{}")
        self.write("skills/b/SKILL.md", "---\nname: b\n---\nSource `~/.agents/hooks/lib.sh`.\n")
        self.assertEqual(self.pick("--", "scripts/settings.sh"), ["configure/one"])
        self.assertEqual(self.pick("--", "scripts/hooks/lib.sh"), B)

    def test_a_core_file_a_skill_names_by_its_repo_path_picks_that_skills_cases(self):
        self.write("scripts/capped", "")
        self.write("skills/b/brief.md", "Run `scripts/capped` first.\n")
        self.assertEqual(self.pick("--", "scripts/capped"), B)

    def test_a_core_file_no_skill_names_picks_no_case(self):
        self.write("scripts/settings.sh", "")
        self.write("scripts/capped", "")
        self.write("evals/configure/one/case.json", "{}")
        self.assertEqual(self.pick("--", "scripts/settings.sh", "scripts/capped"), [])

    def test_a_case_whose_parts_cannot_be_read_is_picked_by_any_change(self):
        self.write("evals/b/two/case.json", json.dumps({"install": ["nope"]}))
        self.assertEqual(self.pick("--", "docs/x.md"), ["b/two"])


class EveryCaseTest(PickTest):
    def test_shared_conventions_pick_every_case(self):
        self.assertEqual(self.pick("--", "skills/_shared/conventions.md"), EVERY)

    def test_the_runner_or_a_top_level_eval_script_picks_every_case(self):
        self.assertEqual(self.pick("--", "evals/_runner/cases.py"), EVERY)
        self.assertEqual(self.pick("--", "evals/_runner/fixtures/cases/x.md"), EVERY)
        self.assertEqual(self.pick("--", "evals/run.py"), EVERY)

    def test_an_eval_test_file_picks_no_case(self):
        self.assertEqual(self.pick("--", "evals/test_pick.py", "evals/_runner/test_cases.py"), [])

    def test_all_prints_every_case(self):
        self.assertEqual(self.pick("--all"), EVERY)

    def test_no_merge_base_prints_every_case(self):
        git(self.repo, "update-ref", "-d", "refs/remotes/origin/main")
        self.assertEqual(self.pick(), EVERY)


class SharedSettingsTest(PickTest):
    def test_a_changed_shared_key_picks_the_skills_opting_in_to_it(self):
        self.change_shared(max_parallel=2)
        self.assertEqual(self.pick(), A)
        self.change_shared(tracker="github")
        self.assertEqual(self.pick(), B)
        self.change_shared(max_parallel=2, tracker="github")
        self.assertEqual(self.pick(), EVERY)

    def test_a_changed_shared_key_picks_every_configure_case(self):
        self.write("evals/configure/one/case.json", "{}")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "configure case")
        git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.change_shared(max_parallel=2)
        self.assertEqual(self.pick(), A + ["configure/one"])
        self.change_shared(new_key=1)
        self.assertEqual(self.pick(), ["configure/one"])

    def test_a_skill_naming_the_shared_settings_file_is_picked_only_by_a_key_it_opts_in_to(self):
        self.write("skills/b/SKILL.md", "---\nname: b\n---\nSee `skills/_shared/settings.json`.\n")
        git(self.repo, "commit", "-qam", "b names it")
        git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.change_shared(max_parallel=2)
        self.assertEqual(self.pick(), A)

    def test_a_shared_key_change_given_as_a_path_reads_the_change_since_origin_main(self):
        self.change_shared(tracker="github")
        self.assertEqual(self.pick("--", "skills/_shared/settings.json"), B)

    def test_base_reads_the_shared_keys_changed_since_the_given_ref(self):
        self.change_shared(tracker="github")
        git(self.repo, "commit", "-qam", "tracker")
        self.change_shared(tracker="github", max_parallel=2)
        git(self.repo, "commit", "-qam", "max_parallel")
        self.assertEqual(self.pick("--base", "HEAD~1"), A)
        self.assertEqual(self.pick(), EVERY)

    def test_a_shared_settings_change_with_no_changed_key_known_picks_every_case(self):
        self.write("skills/_shared/settings.json", "{ not json")
        self.assertEqual(self.pick(), EVERY)
        (self.repo / "skills" / "_shared" / "settings.json").unlink()
        self.assertEqual(self.pick(), EVERY)


class NothingTest(PickTest):
    def test_a_change_touching_no_skill_with_cases_prints_nothing(self):
        self.assertEqual(self.pick("--", "skills/c/SKILL.md", "docs/x.md", "evals/c/README.md"), [])
        self.assertEqual(self.pick(), [])


if __name__ == "__main__":
    unittest.main()
