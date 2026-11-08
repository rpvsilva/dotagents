"""Tests for `followups.py record` and `steplib/followups.py`, the only reader and writer of `<run>/followups.jsonl`."""
import ast
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import followups  # noqa: E402
from testkit import SCRIPTS, Kit  # noqa: E402

RECORD = SCRIPTS / "followups.py"

REPORT = """# Report

Built the thing.

## Follow-ups

- should-fix follow-up src/app.py:12: the flag is not in the README
- blocker interaction src/undo.py: undo must restore the due time

- nit follow-up docs/x.md:3: typo: "teh"

## Notes

- not a row, and not in the section
"""


class FollowupsCase(unittest.TestCase):
    def setUp(self):
        self.kit = Kit()
        self.addCleanup(self.kit.close)
        self.run_dir = self.kit.run_dir

    def report(self, text, name="report.md"):
        path = self.run_dir / "reports" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def record(self, *args, ok=True):
        p = self.kit.step(RECORD, self.run_dir, "record", *args)
        lines = p.stdout.splitlines()
        self.assertEqual(len(lines), 1, p.stdout + p.stderr)
        self.assertEqual(p.returncode == 0, ok, p.stdout + p.stderr)
        return lines[0]

    def rows(self):
        return followups.load(self.run_dir)


class RecordTest(FollowupsCase):
    def test_record_appends_each_row_with_ticket_pr_and_source(self):
        report = self.report(REPORT)
        self.assertEqual(self.record(report, "--ticket", "03", "--pr", "41"), "OK 03 recorded 3 new=3")
        self.assertEqual(self.rows(), [
            {"id": 1, "ticket": "03", "pr": 41, "severity": "should-fix", "kind": "follow-up", "file": "src/app.py",
             "line": 12, "claim": "the flag is not in the README", "source": "reports/report.md"},
            {"id": 2, "ticket": "03", "pr": 41, "severity": "blocker", "kind": "interaction", "file": "src/undo.py",
             "line": None, "claim": "undo must restore the due time", "source": "reports/report.md"},
            {"id": 3, "ticket": "03", "pr": 41, "severity": "nit", "kind": "follow-up", "file": "docs/x.md",
             "line": 3, "claim": 'typo: "teh"', "source": "reports/report.md"},
        ])

    def test_each_line_of_the_file_is_one_json_row(self):
        self.record(self.report(REPORT), "--ticket", "3")
        lines = (self.run_dir / "followups.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(line)["id"] for line in lines], [1, 2, 3])

    def test_without_pr_the_pr_is_null(self):
        self.record(self.report(REPORT), "--ticket", "03")
        self.assertEqual({r["pr"] for r in self.rows()}, {None})

    def test_recording_the_same_report_twice_adds_nothing_the_second_time(self):
        report = self.report(REPORT)
        self.record(report, "--ticket", "03", "--pr", "41")
        before = (self.run_dir / "followups.jsonl").read_bytes()
        self.assertEqual(self.record(report, "--ticket", "03", "--pr", "41"), "OK 03 recorded 3 new=0")
        self.assertEqual((self.run_dir / "followups.jsonl").read_bytes(), before)

    def test_a_row_is_the_same_on_ticket_kind_file_and_claim_whatever_its_severity_line_or_pr(self):
        self.record(self.report(REPORT), "--ticket", "03", "--pr", "41")
        again = self.report("## Follow-ups\n- nit follow-up src/app.py:99: the flag is not in the README\n", "b.md")
        self.assertEqual(self.record(again, "--ticket", "3", "--pr", "42"), "OK 3 recorded 1 new=0")

    def test_the_same_row_in_another_ticket_or_kind_is_new(self):
        self.record(self.report(REPORT), "--ticket", "03")
        other = self.report("## Follow-ups\n- should-fix interaction src/app.py: the flag is not in the README\n",
                            "b.md")
        self.assertEqual(self.record(other, "--ticket", "03"), "OK 03 recorded 1 new=1")
        self.assertEqual(self.record(self.report(REPORT, "c.md"), "--ticket", "04"), "OK 04 recorded 3 new=3")
        self.assertEqual([r["id"] for r in self.rows()], list(range(1, 8)))

    def test_a_report_with_no_section_records_0(self):
        report = self.report("# Report\n\nNothing to follow up.\n")
        self.assertEqual(self.record(report, "--ticket", "03"), "OK 03 recorded 0 new=0")
        self.assertEqual(self.rows(), [])

    def test_a_malformed_row_fails_and_records_nothing_from_that_report(self):
        bad = REPORT.replace("- nit follow-up docs/x.md:3", "- minor follow-up docs/x.md:3")
        line = self.record(self.report(bad), "--ticket", "03", ok=False)
        self.assertRegex(line, r'^FAIL 03 bad-followup - minor follow-up docs/x.md:3: typo: "teh" \(log: .+\)$')
        self.assertEqual(self.rows(), [])
        self.assertFalse((self.run_dir / "followups.jsonl").exists())

    def test_every_kind_of_malformed_line_fails(self):
        for bad in ("- should-fix follow-up src/app.py the claim", "- should-fix todo src/app.py: claim",
                    "- should-fix follow-up src/app.py: ", "* should-fix follow-up src/app.py: claim",
                    "free text"):
            with self.subTest(bad=bad):
                line = self.record(self.report(f"## Follow-ups\n{bad}\n"), "--ticket", "03", ok=False)
                self.assertTrue(line.startswith(f"FAIL 03 bad-followup {bad.strip()} (log: "), line)

    def test_an_implementers_report_with_the_block_unfenced_after_the_section_records_its_rows(self):
        report = self.report("# Report\n\nBuilt it.\n\n## Follow-ups\n\n- nit follow-up a.py:3: typo\n\n"
                             "status: done\nchanged: abc123 a.py\nwhy:\nfollow-up: none\n")
        self.assertEqual(self.record(report, "--ticket", "03"), "OK 03 recorded 1 new=1")
        self.assertEqual([(r["file"], r["line"], r["claim"]) for r in self.rows()], [("a.py", 3, "typo")])

    def test_a_report_with_the_block_fenced_after_the_section_records_its_rows(self):
        report = self.report("## Follow-ups\n\n- nit follow-up a.py:3: typo\n- should-fix interaction b.py: undo\n"
                             "\n```\nstatus: done\nchanged: report.md\nwhy:\nfollow-up: 0 blocker\n```\n")
        self.assertEqual(self.record(report, "--ticket", "03"), "OK 03 recorded 2 new=2")

    def test_a_reviewers_process_lines_after_the_section_are_not_rows(self):
        report = self.report("No findings.\n\n## Follow-ups\n\n- nit follow-up a.py: typo\n\n"
                             "process: the suite took 40 s\nprocess: the diff was large\n")
        self.assertEqual(self.record(report, "--ticket", "03"), "OK 03 recorded 1 new=1")

    def test_a_bullet_that_does_not_parse_after_rows_still_fails(self):
        for bad in ("- minor follow-up a.py: typo", "* nit follow-up a.py: typo"):
            with self.subTest(bad=bad):
                text = f"## Follow-ups\n\n- nit follow-up b.py: one\n\n{bad}\n\nstatus: done\n"
                line = self.record(self.report(text), "--ticket", "03", ok=False)
                self.assertTrue(line.startswith(f"FAIL 03 bad-followup {bad} (log: "), line)
                self.assertEqual(self.rows(), [])

    def test_a_report_outside_the_run_folder_keeps_its_full_path_as_source(self):
        outside = self.kit.tmp / "elsewhere.md"
        outside.write_text(REPORT, encoding="utf-8")
        self.record(outside, "--ticket", "03")
        self.assertEqual({r["source"] for r in self.rows()}, {str(outside)})

    def test_bad_arguments_fail_with_their_reason(self):
        report = self.report(REPORT)
        cases = {
            ("record", report): r"^FAIL run usage: ",
            ("record", "--ticket", "03"): r"^FAIL 03 usage: ",
            ("record", report, "--ticket", "03", "--pr"): r"^FAIL 03 usage: ",
            ("file", report, "--ticket", "03"): r"^FAIL 03 usage: ",
            ("record", report, "--ticket", "x"): r"^FAIL x bad-ticket: x ",
            ("record", report, "--ticket", "03", "--pr", "#4"): r"^FAIL 03 bad-pr: #4 ",
            ("record", self.run_dir / "none.md", "--ticket", "03"): r"^FAIL 03 no-report: ",
        }
        for args, want in cases.items():
            with self.subTest(args=args):
                p = self.kit.step(RECORD, self.run_dir, *args)
                self.assertEqual(p.returncode, 1, p.stdout)
                self.assertRegex(p.stdout, want)
        self.assertEqual(self.rows(), [])

    def test_concurrent_records_lose_no_row(self):
        reports = [self.report("## Follow-ups\n" + "".join(f"- nit follow-up f{i}.py: claim {i}-{j}\n"
                                                           for j in range(20)), f"r{i}.md") for i in range(8)]
        procs = [subprocess.Popen([sys.executable, "-I", str(RECORD), str(self.run_dir), "record", str(r),
                                   "--ticket", "03"], env=self.kit.env, stdout=subprocess.PIPE, text=True)
                 for r in reports]
        for p in procs:
            self.assertEqual(p.communicate()[0], "OK 03 recorded 20 new=20\n")
        rows = self.rows()
        self.assertEqual(len(rows), 160)
        self.assertEqual(sorted(r["id"] for r in rows), list(range(1, 161)))


OWNER = """## Follow-ups

- should-fix owner-doc AGENTS.md#checks: name the new suite
```diff
--- a/AGENTS.md
+++ b/AGENTS.md
@@ -1 +1 @@
-old checks
+new checks
```
- nit follow-up skills/a.py:3: typo
"""

GUARD = "# Rules\n\n```agent-merge-guard\nowner path AGENTS.md\nhooks path hooks/**\n```\n"


def owner_doc(item, claim, patch, severity="should-fix", path="AGENTS.md"):
    return f"- {severity} owner-doc {path}#{item}: {claim}\n```diff\n{patch}```\n"


class OwnerDocTest(FollowupsCase):
    def test_an_owner_doc_row_records_its_item_and_its_patch(self):
        self.assertEqual(self.record(self.report(OWNER), "--ticket", "04"), "OK 04 recorded 2 new=2")
        doc = self.rows()[0]
        self.assertEqual((doc["kind"], doc["file"], doc["item"], doc["line"], doc["claim"]),
                         ("owner-doc", "AGENTS.md", "checks", None, "name the new suite"))
        self.assertEqual(doc["patch"], "--- a/AGENTS.md\n+++ b/AGENTS.md\n@@ -1 +1 @@\n-old checks\n+new checks\n")
        self.assertEqual(self.rows()[1]["file"], "skills/a.py")

    def test_a_later_row_for_the_same_item_replaces_the_earlier_one(self):
        self.record(self.report("## Follow-ups\n" + owner_doc("checks", "first", "-a\n+b\n")), "--ticket", "03")
        later = self.report("## Follow-ups\n" + owner_doc("checks", "second", "-a\n+c\n", "nit"), "b.md")
        self.assertEqual(self.record(later, "--ticket", "04"), "OK 04 recorded 1 new=1")
        self.assertEqual([(r["ticket"], r["claim"], r["patch"]) for r in self.rows()], [("04", "second", "-a\n+c\n")])

    def test_recording_the_same_owner_doc_again_changes_nothing(self):
        report = self.report(OWNER)
        self.record(report, "--ticket", "04")
        before = (self.run_dir / "followups.jsonl").read_bytes()
        self.assertEqual(self.record(report, "--ticket", "04"), "OK 04 recorded 2 new=0")
        self.assertEqual((self.run_dir / "followups.jsonl").read_bytes(), before)

    def test_an_owner_doc_without_its_diff_block_fails(self):
        for text in ("- nit owner-doc AGENTS.md#x: claim\n- nit follow-up a.py: b\n",
                     "- nit owner-doc AGENTS.md#x: claim\nthe patch:\n```diff\n-a\n```\n",
                     "- nit owner-doc AGENTS.md#x: claim\n```diff\n-a\n",
                     "- nit owner-doc AGENTS.md#x: claim\n```diff\n```\n",
                     "- nit owner-doc AGENTS.md: claim\n```diff\n-a\n```\n"):
            with self.subTest(text=text):
                line = self.record(self.report("## Follow-ups\n" + text), "--ticket", "04", ok=False)
                self.assertTrue(line.startswith(f"FAIL 04 bad-followup {text.splitlines()[0]} (log: "), line)
                self.assertEqual(self.rows(), [])


class ReportTest(FollowupsCase):
    def setUp(self):
        super().setUp()
        self.repo = self.kit.make_origin()
        self.kit.init_run()

    def rules(self, agents_md):
        """Puts `agents_md` as `AGENTS.md` on origin's `main`, the run's default branch."""
        (self.repo / "AGENTS.md").write_text(agents_md, encoding="utf-8")
        self.kit.git("add", "AGENTS.md", cwd=self.repo)
        self.kit.git("commit", "-qm", "rules", cwd=self.repo)
        self.kit.git("push", "-q", "origin", "main", cwd=self.repo)

    def printed(self, ok=True):
        p = self.kit.step(RECORD, self.run_dir, "report")
        self.assertEqual(p.returncode == 0, ok, p.stdout + p.stderr)
        return p.stdout

    def test_an_empty_run_prints_no_follow_ups(self):
        self.assertEqual(self.printed(), "follow-ups: 0\n")

    def test_counts_and_groups_rows_by_area_then_severity(self):
        self.record(self.report(REPORT), "--ticket", "03", "--pr", "41")
        self.record(self.report("## Follow-ups\n- nit follow-up src/b.py: two\n- blocker follow-up README.md: three\n",
                                "b.md"), "--ticket", "04")
        self.assertEqual(self.printed(), "\n".join([
            "follow-ups: 5",
            "README.md: 1 blocker",
            "docs: 1 nit",
            "src: 1 blocker, 1 should-fix, 1 nit",
            "",
            "## README.md",
            "### blocker",
            "- follow-up README.md: three (ticket 04)",
            "## docs",
            "### nit",
            '- follow-up docs/x.md:3: typo: "teh" (ticket 03, PR #41)',
            "## src",
            "### blocker",
            "- interaction src/undo.py: undo must restore the due time (ticket 03, PR #41)",
            "### should-fix",
            "- follow-up src/app.py:12: the flag is not in the README (ticket 03, PR #41)",
            "### nit",
            "- follow-up src/b.py: two (ticket 04)",
            "",
        ]))

    def test_the_counts_match_the_rows(self):
        self.record(self.report(REPORT), "--ticket", "03")
        out = self.printed().splitlines()
        self.assertEqual(out[0], f"follow-ups: {len(self.rows())}")
        counted = sum(int(n) for line in out[1:out.index("")] for n in re.findall(r"(\d+) ", line))
        self.assertEqual(counted, len([line for line in out if line.startswith("- ")]))

    def test_owner_only_files_get_one_combined_patch_with_the_latest_per_item(self):
        self.rules(GUARD)
        self.record(self.report("## Follow-ups\n" + owner_doc("checks", "first", "-a\n+b\n")
                                + owner_doc("usage", "usage", "-u\n+v\n")), "--ticket", "03")
        self.record(self.report("## Follow-ups\n" + owner_doc("checks", "second", "-a\n+c\n"), "b.md"), "--ticket",
                    "04")
        out = self.printed()
        owner = out[out.index("## Owner-only files"):]
        self.assertEqual(owner, "\n".join([
            "## Owner-only files",
            "### AGENTS.md",
            "- should-fix owner-doc AGENTS.md#usage: usage (ticket 03)",
            "- should-fix owner-doc AGENTS.md#checks: second (ticket 04)",
            "```diff",
            "-u",
            "+v",
            "-a",
            "+c",
            "```",
            "",
        ]))
        self.assertEqual(out.count("```diff"), 1)
        self.assertNotIn("first", out)

    def test_a_follow_up_on_a_guarded_path_is_listed_under_its_owner_only_file(self):
        self.rules(GUARD)
        self.record(self.report("## Follow-ups\n- should-fix follow-up hooks/pre.sh:4: quote the path\n"
                                "- nit follow-up src/a.py: not guarded\n"), "--ticket", "03")
        out = self.printed()
        owner = out[out.index("## Owner-only files"):]
        self.assertEqual(owner, "## Owner-only files\n### hooks/pre.sh\n"
                                "- should-fix follow-up hooks/pre.sh:4: quote the path (ticket 03)\n")

    def test_without_guarded_rows_there_is_no_owner_only_section(self):
        self.rules(GUARD)
        self.record(self.report(REPORT), "--ticket", "03")
        self.assertNotIn("Owner-only", self.printed())

    def test_unreadable_guard_rules_fail(self):
        self.rules("```agent-merge-guard\nowner AGENTS.md\n```\n")
        self.record(self.report(REPORT), "--ticket", "03")
        self.assertEqual(self.printed(ok=False), "FAIL run bad-rule: line 2: owner AGENTS.md\n")

    def test_a_missing_run_folder_fails_with_no_run_folder(self):
        gone = self.run_dir.parent / "no-such-run"
        p = self.kit.step(RECORD, gone, "report")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertRegex(p.stdout, r"^FAIL run no-run-folder: ")

    def test_report_takes_no_other_argument(self):
        p = self.kit.step(RECORD, self.run_dir, "report", "extra")
        self.assertEqual(p.returncode, 2, p.stdout)
        self.assertRegex(p.stdout, r"^FAIL run usage: ")


class LibraryTest(FollowupsCase):
    def add(self, ticket, kind, claim, **kw):
        return followups.add(self.run_dir, [{"ticket": ticket, "pr": None, "severity": "should-fix", "kind": kind,
                                             "file": "a.py", "line": None, "claim": claim, "source": "r.md", **kw}])

    def test_uncarried_lists_the_tickets_rows_of_that_kind_not_yet_carried(self):
        self.add("03", "interaction", "one")
        self.add("03", "follow-up", "two")
        self.add("04", "interaction", "three")
        self.add("3", "interaction", "four")
        self.assertEqual([r["claim"] for r in followups.uncarried(self.run_dir, "3", "interaction")], ["one", "four"])
        followups.mark(self.run_dir, [1], "carried", "03-02")
        self.assertEqual([r["claim"] for r in followups.uncarried(self.run_dir, "03", "interaction")], ["four"])

    def test_mark_sets_a_key_on_the_given_rows_only(self):
        for claim in ("one", "two", "three"):
            self.add("03", "follow-up", claim)
        self.assertEqual(followups.mark(self.run_dir, [1, 3], "issue", 77), 2)
        self.assertEqual([r.get("issue") for r in followups.load(self.run_dir)], [77, None, 77])
        self.assertEqual(followups.mark(self.run_dir, [9], "issue", 78), 0)

    def test_mark_never_changes_an_id(self):
        self.add("03", "follow-up", "one")
        with self.assertRaises(ValueError):
            followups.mark(self.run_dir, [1], "id", 5)

    def test_add_returns_only_the_new_rows(self):
        self.assertEqual([r["id"] for r in self.add("03", "follow-up", "one")], [1])
        self.assertEqual(self.add("03", "follow-up", "one"), [])

    def test_load_of_a_run_with_no_file_is_empty(self):
        self.assertEqual(followups.load(self.run_dir), [])


class DocstringTest(unittest.TestCase):
    def test_the_docstring_lists_the_grammar_and_every_fail_reason(self):
        source = RECORD.read_text(encoding="utf-8")
        doc = ast.get_docstring(ast.parse(source))
        self.assertIn("- <severity> <kind> <path[:line]>: <claim>", doc)
        for word in followups.SEVERITIES + followups.KINDS:
            self.assertIn(f"`{word}`", doc)
        self.assertIn("owner-doc", followups.KINDS)
        self.assertIn("- <severity> owner-doc <path>#<item>: <claim>", doc)
        self.assertIn("followups.py <run> report", doc)
        self.assertIn("follow-ups: 0", doc)
        reasons = set(re.findall(r'core\.Fail\(f?"([a-z-]+)', source)) | {"usage"}
        self.assertEqual(reasons, {"usage", "bad-ticket", "bad-pr", "no-report", "bad-followup"})
        for reason in reasons:
            self.assertIn(f"`{reason}", doc)


if __name__ == "__main__":
    unittest.main()
