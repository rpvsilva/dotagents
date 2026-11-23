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


class DraftIssues(unittest.TestCase):
    def drafts(self):
        text = (ROOT / "docs" / "apm.md").read_text().split("## Draft upstream issues", 1)[1]
        sections = re.split(r"(?m)^### `([a-z0-9-]+)`$", text)
        return {sections[i]: sections[i + 1] for i in range(1, len(sections), 2)}

    def test_each_draft_says_what_happens_with_its_own_breakage(self):
        data = json.loads((HERE / "results.json").read_text())
        described = {b["id"]: b["description"] for t in data["cells"].values() for c in t.values() for b in c["breakages"]}
        seen = {}
        for name, body in self.drafts().items():
            with self.subTest(draft=name):
                match = re.search(r"(?m)^> What happens: (.+)$", body)
                self.assertIsNotNone(match, "no What happens line")
                self.assertEqual(match.group(1), described[name])
                self.assertNotIn(match.group(1), seen, f"same sentence as {seen.get(match.group(1))}")
                seen[match.group(1)] = name

    def test_each_draft_expects_behaviour_not_the_breakage_again(self):
        data = json.loads((HERE / "results.json").read_text())
        described = {b["id"]: b["description"] for t in data["cells"].values() for c in t.values() for b in c["breakages"]}
        seen = {}
        for name, body in self.drafts().items():
            with self.subTest(draft=name):
                match = re.search(r"(?m)^> What is expected: (.+)$", body)
                self.assertIsNotNone(match, "no What is expected line")
                expected = match.group(1)
                self.assertNotEqual(expected, described[name], "restates its breakage")
                self.assertNotIn(expected, seen, f"same sentence as {seen.get(expected)}")
                seen[expected] = name


if __name__ == "__main__":
    unittest.main()
