"""Tests of the agent protocol: every role's schema, check.py, prompt.py and render.py, run as a caller runs them."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import protocol  # noqa: E402

DONE = {
    "planner": {"status": "done", "wrote": ["spec.md", "areas.md"]},
    "explorer": {"status": "done", "note": "notes/store.md", "unsettled": []},
    "reader": {"status": "done", "note": "notes/source-1.md"},
    "mapper": {"status": "done", "map": "map.md", "areas": 3, "flows": 1, "repo_wide": 2, "not_reviewed": 0},
    "implementer": {
        "status": "done", "commit": "abc1234", "files": ["todo/cli.py", "tests/test_cli.py"],
        "tests": [{"name": "test_note_adds_text", "criterion": "todo note 1 x stores x", "broke": "dropped the save"}],
        "suites": [{"command": "capped -- pytest", "baseline_s": 1.5, "final_s": 1.7, "passed": True}],
        "followups": [{"severity": "nit", "kind": "follow-up", "path": "README.md", "claim": "document note"}],
    },
    "reviewer": {
        "status": "done", "mode": "ticket", "checked": "every hunk; pytest green", "process": [], "followups": [],
        "findings": [
            {"severity": "nit", "axis": "tests", "path": "a.py", "line": 3, "claim": "c1", "scenario": "s1"},
            {"severity": "blocker", "axis": "bugs", "path": "b.py", "line": 9, "claim": "c2", "scenario": "s2"},
        ],
    },
    "security": {
        "status": "done", "complete": True, "other_bugs": [], "process": [],
        "coverage": [{"source": "cli.py:10", "sinks": ["open"], "cells": {
            "injection": "clean", "secrets": "clean", "authz": "clean", "privilege": "clean",
            "supply-chain": "clean", "unsafe-handling": 1}}],
        "findings": [{"n": 1, "severity": "should-fix", "axis": "unsafe-handling", "path": "cli.py", "line": 10,
                      "claim": "c", "attacker": "a", "scenario": "s", "gain": "g", "fix": "f"}],
    },
    "tuner": {"status": "done", "commit": "", "files": [], "baseline": [{"suite": "unit", "secs": 9}],
              "time": [], "covered": [], "moved": [], "mutations": [], "left": []},
}


def run(script, *args, stdin=""):
    proc = subprocess.run([sys.executable, "-I", str(HERE / script), *args], input=stdin,
                          capture_output=True, text=True)
    return proc.returncode, proc.stdout


class Schemas(unittest.TestCase):
    def test_every_role_has_a_valid_done_output(self):
        self.assertEqual(sorted(DONE), protocol.roles())
        for role, out in DONE.items():
            self.assertEqual(protocol.check(role, "output", out), [], role)

    def test_every_schema_names_an_agent_and_a_brief_that_exist(self):
        skills = HERE.parent.parent
        for role in protocol.roles():
            doc = protocol.schema(role)
            self.assertTrue((skills / doc["brief"]).is_file(), doc["brief"])

    def test_a_status_other_than_done_needs_a_why(self):
        out = dict(DONE["implementer"], status="blocked")
        self.assertIn("$: missing why", protocol.check("implementer", "output", out))
        out["why"] = ""
        self.assertIn("$.why: empty", protocol.check("implementer", "output", out))
        out["why"] = "red after 3 tries"
        self.assertEqual(protocol.check("implementer", "output", out), [])

    def test_unknown_keys_wrong_enums_and_missing_fields_are_named(self):
        out = dict(DONE["reviewer"], extra=1)
        out["findings"] = [dict(out["findings"][0], severity="major")]
        del out["checked"]
        problems = protocol.check("reviewer", "output", out)
        self.assertIn("$: unknown key extra", problems)
        self.assertIn("$: missing checked", problems)
        self.assertTrue(any(p.startswith("$.findings[0].severity: not one of") for p in problems), problems)

    def test_an_owner_doc_followup_needs_its_item_and_patch(self):
        out = dict(DONE["implementer"])
        out["followups"] = [{"severity": "should-fix", "kind": "owner-doc", "path": "AGENTS.md", "claim": "x"}]
        problems = protocol.check("implementer", "output", out)
        self.assertIn("$.followups[0]: missing patch", problems)
        out["followups"][0].update(item="Checks", patch="--- a/AGENTS.md\n")
        self.assertEqual(protocol.check("implementer", "output", out), [])

    def test_a_followup_about_the_whole_file_has_line_0_or_none(self):
        out = dict(DONE["implementer"])
        out["followups"] = [{"severity": "nit", "kind": "follow-up", "path": "README.md", "line": 0, "claim": "x"},
                            {"severity": "nit", "kind": "follow-up", "path": "README.md", "claim": "y"}]
        self.assertEqual(protocol.check("implementer", "output", out), [])
        out["followups"][0]["line"] = -1
        self.assertTrue(protocol.check("implementer", "output", out))

    def test_a_line_of_prose_before_the_object_is_read_past(self):
        obj = {"status": "done", "note": "a {brace} inside"}
        self.assertEqual(protocol.parse_json("Reissue: write it again.\n\n" + json.dumps(obj)), obj)
        self.assertEqual(protocol.parse_json("Here it is:\n```json\n" + json.dumps(obj) + "\n```"), obj)
        with self.assertRaises(ValueError):
            protocol.parse_json(json.dumps(obj) + "\nDone, and more prose after.")
        with self.assertRaises(ValueError):
            protocol.parse_json("status: done\n")

    def test_a_coverage_cell_is_clean_or_a_finding_number(self):
        out = json.loads(json.dumps(DONE["security"]))
        out["coverage"][0]["cells"]["secrets"] = "dirty"
        self.assertTrue(protocol.check("security", "output", out))


class Check(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def write(self, name, value):
        path = self.dir / name
        path.write_text(value if isinstance(value, str) else json.dumps(value))
        return str(path)

    def test_an_output_and_its_message_pass(self):
        out = self.write("01-01.json", DONE["implementer"])
        code, line = run("check.py", "implementer", "output", out, "--message", "-",
                         stdin='```json\n{"status": "done", "output": "%s"}\n```\n' % out)
        self.assertEqual((code, line), (0, "OK implementer done\n"))

    def test_a_message_that_disagrees_or_runs_long_is_a_violation(self):
        out = self.write("o.json", DONE["planner"])
        code, line = run("check.py", "planner", "output", out, "--message", "-",
                         stdin=json.dumps({"status": "blocked", "output": out}))
        self.assertEqual(code, 1)
        self.assertIn("status-mismatch", line)
        code, line = run("check.py", "planner", "output", out, "--message", "-",
                         stdin=json.dumps({"status": "done", "output": out, "note": "x" * 400}))
        self.assertIn("length=", line)
        self.assertIn("message: unknown key note", line)

    def test_the_old_four_line_block_is_a_violation(self):
        out = self.write("o.json", "status: done\nchanged: x\nwhy:\nfollow-up:\n")
        self.assertEqual(run("check.py", "planner", "output", out), (1, "FAIL planner brief-violation json\n"))

    def test_a_missing_output_and_an_unknown_role(self):
        self.assertEqual(run("check.py", "planner", "output", str(self.dir / "none.json")),
                         (1, "FAIL planner brief-violation no-output\n"))
        self.assertEqual(run("check.py", "nobody", "output", "x")[0], 2)

    def test_a_prompt_is_checked_as_the_role_input(self):
        code, line = run("prompt.py", "explorer", "areas=/r/areas.md", "area=store", "spec=/r/spec.md",
                         "note=/r/notes/store.md", "output=/r/reports/explore-store.json",
                         "--out", str(self.dir / "p.md"))
        self.assertEqual((code, line), (0, f"OK explorer prompt={self.dir / 'p.md'}\n"))
        text = (self.dir / "p.md").read_text()
        self.assertTrue(text.startswith("You are an epic-explorer. Follow ~/.agents/skills/build-epic/explorer-brief.md.\n"))
        self.assertEqual(run("check.py", "explorer", "input", str(self.dir / "p.md")), (0, "OK explorer input\n"))


class Prompt(unittest.TestCase):
    def test_json_values_and_texts_that_look_like_numbers(self):
        code, text = run("prompt.py", "reviewer", "mode=ticket", "pr:=12", "worktree=/w", "head=1234567",
                         "base=origin/main", "diff=git diff a b", "remote:=true")
        self.assertEqual(code, 0)
        inputs = protocol.prompt_inputs(text)
        self.assertEqual((inputs["pr"], inputs["head"], inputs["remote"]), (12, "1234567", True))

    def test_a_bad_or_missing_input_writes_nothing(self):
        code, line = run("prompt.py", "planner", "pass=C", "run=/r", "notes=/n", "output=/o")
        self.assertEqual(code, 1)
        self.assertIn("$.pass: not one of A|B|revise", line)
        code, line = run("prompt.py", "planner", "pass=A", "run=/r")
        self.assertIn("missing notes", line)


class Render(unittest.TestCase):
    def render(self, role, out):
        path = Path(tempfile.mkdtemp()) / "o.json"
        path.write_text(json.dumps(out))
        return run("render.py", role, str(path))

    def test_a_review_lists_findings_most_severe_first(self):
        code, text = self.render("reviewer", DONE["reviewer"])
        self.assertEqual(code, 0)
        self.assertLess(text.index("`b.py:9`"), text.index("`a.py:3`"))
        self.assertIn("1. **blocker** (bugs) `b.py:9`: c2\n   - Scenario: s2", text)

    def test_a_clean_review_says_what_was_checked(self):
        _, text = self.render("reviewer", dict(DONE["reviewer"], findings=[]))
        self.assertIn("No findings. Checked: every hunk; pytest green", text)

    def test_an_owner_doc_patch_is_a_diff_block(self):
        out = dict(DONE["implementer"], followups=[{"severity": "should-fix", "kind": "owner-doc",
                   "path": "AGENTS.md", "item": "Checks", "claim": "name it", "patch": "+x\n"}])
        _, text = self.render("implementer", out)
        self.assertIn("- should-fix owner-doc AGENTS.md#Checks: name it\n```diff\n+x\n```", text)

    def test_every_role_renders(self):
        for role, out in DONE.items():
            code, text = self.render(role, out)
            self.assertEqual(code, 0, role)
            self.assertTrue(text.startswith("Status: done\n"), role)

    def test_an_output_failing_its_schema_is_not_rendered(self):
        code, line = self.render("reviewer", {"status": "done"})
        self.assertEqual(code, 1)
        self.assertTrue(line.startswith("FAIL reviewer brief-violation"))


if __name__ == "__main__":
    unittest.main()
