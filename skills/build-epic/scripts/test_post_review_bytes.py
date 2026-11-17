"""Tests that `post_review.py` posts the reviewer's output rendered by `render.py`, never its raw text, against a stub
`gh`."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_ship import POST_REVIEW, REVIEWS, SCRIPTS, Base  # noqa: E402

sys.path.insert(0, str(SCRIPTS.parents[1] / "_shared" / "protocol"))

import render  # noqa: E402

OUTPUT = {
    "status": "done", "mode": "ticket", "checked": "every hunk; the suites ran green", "process": [],
    "followups": [{"severity": "nit", "kind": "follow-up", "path": "README.md", "claim": "document reset"}],
    "findings": [{"severity": "should-fix", "axis": "bugs", "path": "a.py", "line": 3,
                  "claim": "the parser drops keys — café ✓", "scenario": "parse('{\"a\": 1}') returns {}"}],
}
SECURITY = {
    "status": "done", "complete": True, "other_bugs": [], "process": [], "findings": [],
    "coverage": [{"source": "cli.py:10", "sinks": ["open"], "cells": {
        "injection": "clean", "secrets": "clean", "authz": "clean", "privilege": "clean", "supply-chain": "clean",
        "unsafe-handling": "clean"}}],
}
OK = {"status": "done", "mode": "task", "checked": "all of it", "process": [], "followups": [], "findings": []}


class PostReviewBytesTest(Base):
    def setUp(self):
        super().setUp()
        self.kit.answer(REVIEWS.format(n=12), out=json.dumps({"html_url": "https://github.com/o/r/pull/12#r1"}))
        self.reviews = self.kit.tmp / "reviews"
        self.reviews.mkdir()

    def report(self, out=OUTPUT, name="12-abc1234.json"):
        path = self.reviews / name
        path.write_text(out if isinstance(out, str) else json.dumps(out, ensure_ascii=False), encoding="utf-8")
        return path

    def post(self, *args, cwd="/"):
        line = self.one_line(self.kit.step(POST_REVIEW, self.kit.run_dir, *args, cwd=cwd))
        self.assertEqual(line, "OK PR12 review https://github.com/o/r/pull/12#r1")
        call = self.calls("api")[-1]
        payload = json.loads(Path(call[call.index("--input") + 1]).read_text(encoding="utf-8"))
        self.assertEqual(payload["event"], "COMMENT")
        self.assertEqual(payload["commit_id"], self.head())
        self.assertNotIn("comments", payload)
        return payload["body"]

    def split(self, body, role="reviewer", out=OUTPUT):
        """(header, note) around the rendered output in `body`; fails when it is not there whole."""
        header, sep, rest = body.partition("\n\n")
        self.assertEqual(sep, "\n\n", body)
        text = render.render(role, out)
        self.assertTrue(rest.startswith(text), body)
        return header, rest[len(text):]

    def test_the_body_is_a_one_line_header_then_the_rendered_output(self):
        head = self.head()[:7]
        body = self.post("12", self.report(name=f"12-{head}.json"), "--base", "origin/main")
        header, note = self.split(body)
        self.assertEqual(note, "")
        self.assertNotIn("\n", header)
        self.assertIn(f"Review of PR #12 at `{head}`", header)
        self.assertIn(f"`git diff origin/main...{head}`", header)
        self.assertIn("rendered from the reviewer's report", header)
        self.assertNotIn("posted on", header)
        self.assertIn("**should-fix** (bugs) `a.py:3`: the parser drops keys — café ✓", body)
        self.assertNotIn('"severity"', body)

    def test_a_task_report_on_the_ticket_pr_names_the_task_and_the_head_it_reviewed(self):
        header, _ = self.split(self.post("12", self.report(OK, "01-02-abc1234.json"), "--base", "feat/t"), out=OK)
        self.assertIn("Review of task 01-02 at `abc1234`, before it merged, posted on PR #12", header)
        self.assertIn("`git diff feat/t...abc1234`", header)
        self.assertNotIn("PR #01", header)

    def test_a_security_report_is_rendered_as_a_security_output(self):
        body = self.post("12", self.report(SECURITY, "30-abc1234-security.json"))
        header, _ = self.split(body, "security", SECURITY)
        self.assertIn("Review of PR #30 at `abc1234`, posted on PR #12", header)
        self.assertIn("## Coverage", body)

    def test_a_reviewer_output_named_as_a_security_report_is_a_brief_violation(self):
        line = self.one_line(self.kit.step(POST_REVIEW, self.kit.run_dir, "12",
                                           self.report(OK, "12-abc1234-security.json")))
        self.assertRegex(line, r"^FAIL PR12 brief-violation \$: missing coverage")
        self.assertEqual(self.calls("api"), [])

    def test_a_re_review_names_the_diff_from_the_head_reviewed_before(self):
        head = self.head()[:7]
        header, _ = self.split(self.post("12", self.report(name=f"12-{head}.json"), "--base", "1234567"))
        self.assertIn(f"read `git diff 1234567...{head}`", header)
        self.assertNotIn("main", header)

    def test_without_a_base_the_header_names_no_diff(self):
        header, _ = self.split(self.post("12", self.report()))
        self.assertIn("Review of PR #12 at `abc1234`", header)
        self.assertNotIn("git diff", header)

    def test_a_report_name_without_a_head_says_the_reviewed_head_is_not_recorded(self):
        header, _ = self.split(self.post("12", self.report(name="notes.json"), "--base", "origin/main"))
        self.assertIn("Review of PR #12 (reviewed head not recorded)", header)
        self.assertNotIn(self.head()[:7], header)
        self.assertNotIn("git diff", header)

    def test_a_fenced_output_is_rendered_like_a_bare_one(self):
        body = self.post("12", self.report("```json\n" + json.dumps(OUTPUT) + "\n```\n"))
        self.split(body)

    def test_a_non_utf8_report_fails_bad_report_and_calls_no_api(self):
        report = self.reviews / "12-abc1234.json"
        report.write_bytes(b"caf\xe9 \xff\n")
        line = self.one_line(self.kit.step(POST_REVIEW, self.kit.run_dir, "12", report))
        self.assertRegex(line, r"^FAIL PR12 bad-report \(log: ")
        self.assertEqual(self.kit.gh_calls(), [])

    def test_prose_or_an_output_failing_its_schema_is_refused_and_nothing_posts(self):
        for text, problem in (("## Findings\n\n1. should-fix a.py:3: drops keys\n", "json"),
                              (json.dumps(dict(OUTPUT, status="maybe")), r"\$\.status: not one of"),
                              (json.dumps({k: v for k, v in OUTPUT.items() if k != "checked"}), r"\$: missing checked")):
            with self.subTest(problem=problem):
                line = self.one_line(self.kit.step(POST_REVIEW, self.kit.run_dir, "12", self.report(text)))
                self.assertRegex(line, rf"^FAIL PR12 brief-violation {problem}")
        self.assertEqual(self.kit.gh_calls(), [])
        self.assertFalse((self.reviews / "12-abc1234.posted.md").exists())

    def test_the_header_names_the_runs_test_command(self):
        p = subprocess.run([sys.executable, "-I", str(SCRIPTS / "decide.py"), self.kit.run_dir, "set", "test_command",
                            "tests/run.sh"], env=self.kit.env, text=True, capture_output=True, encoding="utf-8")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        header, _ = self.split(self.post("12", self.report()))
        self.assertIn("`tests/run.sh`", header)

    def test_a_note_follows_the_rendered_output(self):
        body = self.post("12", self.report(), "--note", "Posted by the orchestrator: 2 findings kept.")
        self.assertEqual(self.split(body)[1], "\nPosted by the orchestrator: 2 findings kept.\n")
        body = self.post("12", self.report(), "--note=see the run report")
        self.assertEqual(self.split(body)[1], "\nsee the run report\n")

    def test_a_relative_report_path_posts_the_same_body_as_an_absolute_one(self):
        report = self.report()
        absolute = self.post("12", report)
        relative = self.post("12", "reviews/12-abc1234.json", cwd=self.kit.tmp)
        self.assertEqual(relative, absolute)
        self.split(relative)

    def test_a_copy_of_the_posted_body_is_saved_beside_the_report(self):
        report = self.report()
        before = report.read_bytes()
        body = self.post("12", report, "--note", "kept")
        copy = self.reviews / "12-abc1234.posted.md"
        self.assertEqual(copy.read_bytes(), body.encode())
        self.assertEqual(report.read_bytes(), before)

    def test_inline_comments_are_refused(self):
        line = self.one_line(self.kit.step(POST_REVIEW, self.kit.run_dir, "12", self.report(),
                                           "--comments", "[]"))
        self.assertRegex(line, r"^FAIL PR12 usage: ")
        self.assertEqual(self.calls("api"), [])


if __name__ == "__main__":
    unittest.main()
