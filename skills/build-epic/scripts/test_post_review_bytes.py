"""Tests that `post_review.py` posts the reviewer's own report byte for byte, against a stub `gh`."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_ship import POST_REVIEW, REVIEWS, SCRIPTS, Base  # noqa: E402

REPORT = "## Findings\n\n1. should-fix `a.py:3`: the parser drops keys — café ✓\r\nlast line, no newline"


class PostReviewBytesTest(Base):
    def setUp(self):
        super().setUp()
        self.kit.answer(REVIEWS.format(n=12), out=json.dumps({"html_url": "https://github.com/o/r/pull/12#r1"}))
        self.reviews = self.kit.tmp / "reviews"
        self.reviews.mkdir()

    def report(self, data, name="12-abc1234.md"):
        path = self.reviews / name
        path.write_bytes(data.encode())
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

    def split(self, body, data):
        """(header, note) around the report's exact bytes in `body`; fails when the bytes are not there whole."""
        header, sep, rest = body.partition("\n\n")
        self.assertEqual(sep, "\n\n", body)
        self.assertTrue(rest.startswith(data), body)
        return header, rest[len(data):]

    def test_the_body_is_a_one_line_header_then_the_reports_exact_bytes(self):
        head = self.head()[:7]
        body = self.post("12", self.report(REPORT, f"12-{head}.md"), "--base", "origin/main")
        header, note = self.split(body, REPORT)
        self.assertEqual(note, "")
        self.assertNotIn("\n", header)
        self.assertIn(f"Review of PR #12 at `{head}`", header)
        self.assertIn(f"`git diff origin/main...{head}`", header)
        self.assertNotIn("posted on", header)

    def test_a_hub_report_on_the_ticket_pr_names_the_hub_pr_and_head_it_reviewed(self):
        header, _ = self.split(self.post("12", self.report("ok\n", "30-abc1234.md")), "ok\n")
        self.assertIn("Review of PR #30 at `abc1234`, posted on PR #12", header)
        self.assertNotIn(self.head()[:7], header)
        self.assertNotIn("git diff", header)

    def test_a_security_report_names_the_head_in_its_name(self):
        header, _ = self.split(self.post("12", self.report("ok\n", "30-abc1234-security.md")), "ok\n")
        self.assertIn("Review of PR #30 at `abc1234`, posted on PR #12", header)

    def test_a_re_review_names_the_diff_from_the_head_reviewed_before(self):
        head = self.head()[:7]
        header, _ = self.split(self.post("12", self.report("ok\n", f"12-{head}.md"), "--base", "1234567"), "ok\n")
        self.assertIn(f"read `git diff 1234567...{head}`", header)
        self.assertNotIn("main", header)

    def test_without_a_base_the_header_names_no_diff(self):
        header, _ = self.split(self.post("12", self.report("ok\n")), "ok\n")
        self.assertIn("Review of PR #12 at `abc1234`", header)
        self.assertNotIn("git diff", header)

    def test_a_report_name_without_a_head_says_the_reviewed_head_is_not_recorded(self):
        header, _ = self.split(self.post("12", self.report("ok\n", "notes.md"), "--base", "origin/main"), "ok\n")
        self.assertIn("Review of PR #12 (reviewed head not recorded)", header)
        self.assertNotIn(self.head()[:7], header)
        self.assertNotIn("git diff", header)

    def test_a_non_utf8_report_fails_bad_report_and_calls_no_api(self):
        report = self.reviews / "12-abc1234.md"
        report.write_bytes(b"caf\xe9 \xff\n")
        line = self.one_line(self.kit.step(POST_REVIEW, self.kit.run_dir, "12", report))
        self.assertRegex(line, r"^FAIL PR12 bad-report \(log: ")
        self.assertEqual(self.kit.gh_calls(), [])

    def test_a_trailing_newline_is_kept(self):
        body = self.post("12", self.report("Finding: drops keys.\n"))
        self.assertEqual(self.split(body, "Finding: drops keys.\n")[1], "")

    def test_the_header_names_the_runs_test_command(self):
        p = subprocess.run([sys.executable, "-I", str(SCRIPTS / "decide.py"), self.kit.run_dir, "set", "test_command",
                            "tests/run.sh"], env=self.kit.env, text=True, capture_output=True, encoding="utf-8")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        header, _ = self.split(self.post("12", self.report("ok\n")), "ok\n")
        self.assertIn("`tests/run.sh`", header)

    def test_a_note_follows_the_reports_bytes(self):
        body = self.post("12", self.report("ok\n"), "--note", "Posted by the orchestrator: 2 findings kept.")
        self.assertEqual(self.split(body, "ok\n")[1], "\nPosted by the orchestrator: 2 findings kept.\n")
        body = self.post("12", self.report("no newline"), "--note=see the run report")
        self.assertEqual(self.split(body, "no newline")[1], "\n\nsee the run report\n")

    def test_a_relative_report_path_posts_the_same_body_as_an_absolute_one(self):
        report = self.report(REPORT)
        absolute = self.post("12", report)
        relative = self.post("12", "reviews/12-abc1234.md", cwd=self.kit.tmp)
        self.assertEqual(relative, absolute)
        self.split(relative, REPORT)

    def test_a_copy_of_the_posted_body_is_saved_beside_the_report(self):
        report = self.report(REPORT)
        body = self.post("12", report, "--note", "kept")
        copy = self.reviews / "12-abc1234.posted.md"
        self.assertEqual(copy.read_bytes(), body.encode())
        self.assertEqual(report.read_bytes(), REPORT.encode())

    def test_inline_comments_are_refused(self):
        line = self.one_line(self.kit.step(POST_REVIEW, self.kit.run_dir, "12", self.report("ok\n"),
                                           "--comments", "[]"))
        self.assertRegex(line, r"^FAIL PR12 usage: ")
        self.assertEqual(self.calls("api"), [])


if __name__ == "__main__":
    unittest.main()
