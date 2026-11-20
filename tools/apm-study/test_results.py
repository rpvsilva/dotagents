"""Checks results.json: every public Group x tool has a cell, with a known status, and no home path in the file."""
import json
import re
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
TOOLS = ("claude", "codex", "cursor")
STATUSES = ("works", "partial", "fails")


def public_groups():
    return [g for g in json.loads((ROOT / "groups.json").read_text()) if g not in ("core", "personal")]


class Results(unittest.TestCase):
    def setUp(self):
        self.text = (HERE / "results.json").read_text()
        self.data = json.loads(self.text)

    def test_every_public_group_and_tool_has_a_cell(self):
        for group in public_groups():
            for tool in TOOLS:
                with self.subTest(group=group, tool=tool):
                    self.assertIn(tool, self.data["cells"].get(group, {}), f"missing cell {group} x {tool}")

    def test_every_cell_has_a_known_status_and_described_breakages(self):
        for group, tools in self.data["cells"].items():
            for tool, cell in tools.items():
                with self.subTest(group=group, tool=tool):
                    self.assertIn(cell["status"], STATUSES)
                    for breakage in cell["breakages"]:
                        self.assertRegex(breakage["id"], r"^[a-z0-9-]+$")
                        self.assertTrue(breakage["description"].strip())

    def test_apm_version_and_date_are_recorded(self):
        self.assertRegex(self.data["apm_version"], r"^\d+\.\d+\.\d+$")
        self.assertRegex(self.data["date"], r"^\d{4}-\d{2}-\d{2}$")

    def test_no_absolute_home_path_or_user_name(self):
        self.assertIsNone(re.search(r"/(Users|home)/", self.text))


if __name__ == "__main__":
    unittest.main()
