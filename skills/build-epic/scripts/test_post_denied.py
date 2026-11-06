"""Tests for `post_review.py` when GitHub refuses the review (HTTP 403), against a stub `gh`."""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_ship import POST_REVIEW, REVIEWS, Base  # noqa: E402

DENIED = "gh: Resource not accessible by integration (HTTP 403)\n"


class PostDeniedTest(Base):
    def post(self, *args):
        return self.one_line(self.kit.step(POST_REVIEW, self.kit.run_dir, *args))

    def deny(self):
        report = self.kit.tmp / "reviews" / "12-abc1234.md"
        report.parent.mkdir()
        report.write_text("Finding: the JSON parser drops keys.\n")
        self.kit.answer(REVIEWS.format(n=12), err=DENIED, rc=1, n=1)
        return report

    def test_a_denied_post_names_where_the_report_is_and_leaves_it_in_place(self):
        report = self.deny()
        line = self.post("12", report)
        self.assertRegex(line, rf"^FAIL PR12 post-denied {re.escape(str(report))} \(log: .+\)$")
        self.assertEqual(report.read_text(), "Finding: the JSON parser drops keys.\n")

    def test_a_denied_post_is_not_retried(self):
        report = self.deny()
        self.kit.answer(REVIEWS.format(n=12), out='{"html_url": "https://github.com/o/r/pull/12#r1"}', n=2)
        self.assertRegex(self.post("12", report), r"^FAIL PR12 post-denied ")
        self.assertEqual(len(self.calls("api")), 1)

    def test_any_other_failed_post_is_still_a_plain_post_failure(self):
        report = self.deny()
        self.kit.answer(REVIEWS.format(n=12), err="HTTP 422: Unprocessable", rc=1, n=1)
        self.assertRegex(self.post("12", report), r"^FAIL PR12 post \(log: ")


if __name__ == "__main__":
    unittest.main()
