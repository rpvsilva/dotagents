"""Tests for `pick.py`: the eval cases a change affects, run in a throwaway repo holding a copy of `pick.py` and
`tests/affected.sh`, with skills that have cases (`a`, `b`), one without (`c`) and a shared Settings file."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
A = ["a/one", "a/two"]
B = ["b/one"]
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
        self.write("evals/_runner/cases.py", "")
        self.write("skills/_shared/conventions.md", "# Conventions\n")
        self.write("skills/_shared/settings.json", json.dumps(SHARED))
        for skill, opt_in in (("a", "max_parallel"), ("b", "tracker"), ("c", "max_parallel")):
            self.write(f"skills/{skill}/SKILL.md", f"---\nname: {skill}\n---\n")
            self.write(f"skills/{skill}/settings.json", json.dumps({opt_in: {"shared": True}}))
        for case in EVERY:
            self.write(f"evals/{case}/case.json", "{}")
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


class EveryCaseTest(PickTest):
    def test_shared_conventions_pick_every_case(self):
        self.assertEqual(self.pick("--", "skills/_shared/conventions.md"), EVERY)

    def test_the_runner_or_a_top_level_eval_script_picks_every_case(self):
        self.assertEqual(self.pick("--", "evals/_runner/cases.py"), EVERY)
        self.assertEqual(self.pick("--", "evals/_runner/fixtures/cases/x.md"), EVERY)
        self.assertEqual(self.pick("--", "evals/run.py"), EVERY)

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
