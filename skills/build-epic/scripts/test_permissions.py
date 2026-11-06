"""Tests for `permissions.py`: the permission rules a run needs, listed for the approval message."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from testkit import SCRIPTS, Kit  # noqa: E402

PERMISSIONS = SCRIPTS / "permissions.py"
DECIDE = SCRIPTS / "decide.py"
INSTALLED = "~/.agents/skills/build-epic/scripts"
FORCE_PUSH = "Bash(git push --force-with-lease *)"


class PermissionsTest(unittest.TestCase):
    def setUp(self):
        self.kit = Kit()
        self.addCleanup(self.kit.close)
        self.kit.make_origin()
        self.kit.init_run()
        self.run_dir = self.kit.run_dir

    def decide(self, key, value):
        p = self.kit.step(DECIDE, self.run_dir, "set", key, value)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

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

    def test_the_default_base_needs_no_force_push_rule(self):
        self.assertNotIn(FORCE_PUSH, self.permissions())
        self.decide("base", "main")
        self.assertNotIn(FORCE_PUSH, self.permissions())

    def test_a_non_default_base_adds_the_force_push_rule(self):
        self.decide("base", "feat/integration")
        self.assertEqual(self.permissions().count(FORCE_PUSH), 1)

    def test_merging_as_the_human_adds_a_line_saying_so(self):
        self.decide("merge_identity", "human")
        self.decide("human", "rpvsilva")
        notes = [line for line in self.permissions() if not line.startswith("Bash(")]
        self.assertEqual(len(notes), 1, notes)
        self.assertIn("human's own login", notes[0])
        self.assertIn("rpvsilva", notes[0])

    def test_merging_as_the_app_adds_no_identity_line(self):
        self.decide("merge_identity", "app")
        self.assertTrue(all(line.startswith("Bash(") for line in self.permissions()))

    def test_a_missing_run_folder_fails(self):
        p = self.kit.step(PERMISSIONS, self.kit.tmp / "nope")
        self.assertEqual(p.returncode, 1)
        self.assertTrue(p.stdout.startswith("FAIL run no-run-folder"), p.stdout)


if __name__ == "__main__":
    unittest.main()
