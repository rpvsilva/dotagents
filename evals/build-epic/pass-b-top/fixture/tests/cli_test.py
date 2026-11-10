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

    def test_unknown_command_fails(self):
        out = self.tally("rename")
        self.assertEqual((out.returncode, out.stderr), (1, "tally: usage: tally <add|list> ...\n"))


if __name__ == "__main__":
    unittest.main()
