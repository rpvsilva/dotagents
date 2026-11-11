"""The `tally` commands, run as `python3 -m tally` against a temp store."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TallyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {**os.environ, "TALLY_FILE": str(Path(self.tmp.name) / "tally.json")}

    def tally(self, *args):
        return subprocess.run([sys.executable, "-m", "tally", *args], cwd=ROOT, env=self.env,
                              capture_output=True, text=True)

    def test_add_starts_a_count_at_one(self):
        self.assertEqual(self.tally("add", "tea").stdout, "tea 1\n")

    def test_add_n_adds_to_the_count(self):
        self.tally("add", "tea")
        self.assertEqual(self.tally("add", "tea", "3").stdout, "tea 4\n")

    def test_add_refuses_a_negative_n(self):
        out = self.tally("add", "tea", "-1")
        self.assertEqual((out.returncode, out.stderr), (1, "tally: n must be at least 0\n"))

    def test_list_prints_highest_first(self):
        self.tally("add", "tea")
        self.tally("add", "coffee", "2")
        self.assertEqual(self.tally("list").stdout, "coffee 2\ntea 1\n")

    def test_add_refuses_a_bad_name(self):
        out = self.tally("add", "Tea!")
        self.assertNotEqual(out.stdout, "tea 1\n")

    def test_remove_drops_the_count(self):
        self.tally("add", "tea")
        self.tally("add", "coffee", "2")
        out = self.tally("remove", "tea")
        self.assertEqual((out.returncode, out.stdout), (0, ""))
        self.assertEqual(self.tally("list").stdout, "coffee 2\n")

    def test_rename_moves_the_count(self):
        self.tally("add", "tea", "3")
        self.assertEqual(self.tally("rename", "tea", "green-tea").stdout, "green-tea 3\n")
        self.assertEqual(self.tally("list").stdout, "green-tea 3\n")

    def test_rename_of_an_unknown_count_fails(self):
        out = self.tally("rename", "tea", "green-tea")
        self.assertEqual(out.returncode, 1)

    def test_unknown_command_fails(self):
        out = self.tally("frobnicate")
        self.assertEqual((out.returncode, out.stderr), (1, "tally: usage: tally <add|list|remove|rename> ...\n"))


if __name__ == "__main__":
    unittest.main()
