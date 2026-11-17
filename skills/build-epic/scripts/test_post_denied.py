"""Tests for `post_review.py` when GitHub refuses the review (HTTP 403), against a stub `gh`."""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_ship import POST_REVIEW, REVIEW_OUTPUT, REVIEWS, Base  # noqa: E402

DENIED = "gh: Resource not accessible by integration (HTTP 403)\n"


class PostDeniedTest(Base):
    def post(self, *args):
        return self.one_line(self.kit.step(POST_REVIEW, self.kit.run_dir, *args))

    def report(self):
        report = self.kit.tmp / "reviews" / "12-abc1234.json"
        report.parent.mkdir(exist_ok=True)
        report.write_text(REVIEW_OUTPUT, encoding="utf-8")
        return report

    def deny(self):
        self.kit.answer(REVIEWS.format(n=12), err=DENIED, rc=1, n=1)
        return self.report()

    def test_a_denied_post_names_where_the_report_is_and_leaves_it_in_place(self):
        report = self.deny()
        line = self.post("12", report)
        self.assertRegex(line, rf"^FAIL PR12 post-denied {re.escape(str(report))} \(log: .+\)$")
        self.assertEqual(report.read_text(encoding="utf-8"), REVIEW_OUTPUT)

    def test_a_denied_post_is_not_retried(self):
        report = self.deny()
        self.kit.answer(REVIEWS.format(n=12), out='{"html_url": "https://github.com/o/r/pull/12#r1"}', n=2)
        self.assertRegex(self.post("12", report), r"^FAIL PR12 post-denied ")
        self.assertEqual(len(self.calls("api")), 1)

    def test_any_other_failed_post_is_still_a_plain_post_failure(self):
        report = self.deny()
        self.kit.answer(REVIEWS.format(n=12), err="HTTP 422: Unprocessable", rc=1, n=1)
        self.assertRegex(self.post("12", report), r"^FAIL PR12 post \(log: ")

    def test_a_rate_limit_403_is_a_plain_post_failure_not_a_denial(self):
        report = self.report()
        for err in ("gh: You have exceeded a secondary rate limit. (HTTP 403)\n",
                    "gh: API rate limit exceeded for installation ID 1. (HTTP 403)\n"):
            self.kit.answer(REVIEWS.format(n=12), err=err, rc=1)
            self.assertRegex(self.post("12", report), r"^FAIL PR12 post \(log: ", err)


if __name__ == "__main__":
    unittest.main()
