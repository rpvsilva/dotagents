"""Tests for `permissions.py`: the permission rules a run needs, listed for the approval message."""
import fnmatch
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import decisions  # noqa: E402
from testkit import SCRIPTS, Kit  # noqa: E402

PERMISSIONS = SCRIPTS / "permissions.py"
INSTALLED = "~/.agents/skills/build-epic/scripts"
FORCE_PUSH = "git push --force-with-lease"


class PermissionsTest(unittest.TestCase):
    def setUp(self):
        self.kit = Kit()
        self.addCleanup(self.kit.close)
        self.kit.make_origin()
        self.kit.init_run()
        self.run_dir = self.kit.run_dir

    def decide(self, key, value):
        """What `decide.py <run> set <key> <value>` records (tested in test_decisions.py), in-process."""
        decisions.check_value(key, value)
        d = decisions.load(self.run_dir) or decisions.Decisions()
        d.headers[key] = value
        decisions.save(self.run_dir, d)

    def permissions(self, ok=True):
        p = self.kit.step(PERMISSIONS, self.run_dir)
        self.assertEqual(p.returncode == 0, ok, p.stdout + p.stderr)
        return p.stdout.splitlines()

    def test_each_merging_or_posting_step_script_gets_one_rule_by_its_installed_path(self):
        lines = self.permissions()
        for script in ("merge_task.py", "post_review.py", "ship_ticket.py"):
            rule = f"Bash(python3 -I {INSTALLED}/{script} *)"
            self.assertEqual(lines.count(rule), 1, lines)
        self.assertEqual(lines.count("Bash(capped stop *)"), 1, lines)

    def force_push_rules(self):
        return [line for line in self.permissions() if FORCE_PUSH in line]

    def test_the_default_base_needs_no_force_push_rule(self):
        self.assertEqual(self.force_push_rules(), [])
        self.decide("base", "main")
        self.assertEqual(self.force_push_rules(), [])

    def test_a_non_default_base_adds_a_force_push_rule_naming_only_that_base(self):
        self.decide("base", "feat/integration")
        self.assertEqual(self.force_push_rules(), [f"Bash({FORCE_PUSH} origin feat/integration)"])

    def test_no_rule_matches_a_push_to_the_default_branch(self):
        self.decide("base", "feat/integration")
        pushes = [f"git push {flags}origin {ref}" for flags in ("", "--force ", "--force-with-lease ", "-f ")
                  for ref in ("main", "HEAD:main", "feat/integration:main", "+HEAD:refs/heads/main")]
        for line in self.permissions():
            m = re.fullmatch(r"Bash\((.*)\)", line)
            if not m:
                continue
            for push in pushes:
                self.assertFalse(fnmatch.fnmatchcase(push, m.group(1)), f"{line} allows {push}")

    def test_a_base_with_a_wildcard_or_space_fails(self):
        for base in ("feat/*", "feat/x main"):
            self.decide("base", base)
            self.assertTrue(self.permissions(ok=False)[0].startswith("FAIL run bad-value"), base)

    def test_merging_as_the_human_adds_a_line_saying_so(self):
        self.decide("merge_identity", "human")
        self.decide("human", "rpvsilva")
        notes = [line for line in self.permissions() if not line.startswith("Bash(")]
        self.assertEqual(len(notes), 1, notes)
        self.assertIn("human's own login", notes[0])
        self.assertIn("rpvsilva", notes[0])

    def test_merging_as_the_human_with_no_recorded_human_names_no_login(self):
        self.decide("merge_identity", "human")
        self.assertEqual(self.permissions()[-1], "Merges run as the human's own login, not an App's.")

    def test_merging_as_the_app_adds_no_identity_line(self):
        self.decide("merge_identity", "app")
        self.assertTrue(all(line.startswith("Bash(") for line in self.permissions()))

    def test_a_missing_run_folder_fails(self):
        p = self.kit.step(PERMISSIONS, self.kit.tmp / "nope")
        self.assertEqual(p.returncode, 1)
        self.assertTrue(p.stdout.startswith("FAIL run no-run-folder"), p.stdout)

    def test_any_argument_count_but_one_is_a_usage_failure(self):
        for args in ((), (self.run_dir, "extra")):
            p = self.kit.step(PERMISSIONS, *args)
            self.assertEqual(p.returncode, 2, args)
            self.assertEqual(p.stdout, "FAIL run usage: permissions.py <run>\n", args)

    def test_a_run_folder_without_run_md_fails_when_the_base_needs_the_default(self):
        self.decide("base", "feat/integration")
        (self.run_dir / "run.md").unlink()
        self.assertEqual(self.permissions(ok=False), ["FAIL run no-run-md: run init_run.py first"])


if __name__ == "__main__":
    unittest.main()
