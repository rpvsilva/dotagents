"""Tests for `render.py`: the Markdown and HTML comparison report of two refs."""
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import render  # noqa: E402
import verdict  # noqa: E402


def run(case="c", passed=True, cost=1.0, turns=10, secs=60.0, scores=None):
    rubric = None if scores is None else {"scores": {k: {"score": v, "reason": "r"} for k, v in scores.items()},
                                          "judge_model": "claude-haiku-5-5"}
    return {"case": case, "passed": passed, "cost_usd": cost, "turns": turns, "duration_s": secs, "rubric": rubric}


def comparison(base_runs, head_runs, **fields):
    out = {"tool": "claude", "model": "claude-sonnet-5-5", "runs": 3, "pricing_as_of": "2026-10-01",
           "base": {"ref": "origin/main", "sha": "aaaa111"}, "head": {"ref": "HEAD", "sha": "bbbb222"},
           "verdict": verdict.verdict(verdict.summarise(base_runs), verdict.summarise(head_runs))}
    out.update(fields)
    return out


def table_rows(md):
    """The Markdown table's body rows, each split into cells on unescaped `|`."""
    rows = [line for line in md.splitlines() if line.startswith("|")][2:]
    return [[c.strip() for c in re.split(r"(?<!\\)\|", row)[1:-1]] for row in rows]


class Markdown(unittest.TestCase):
    def test_header_names_tool_model_refs_shas_runs_and_price_date(self):
        md = render.markdown(comparison([run()], [run()]))
        for word in ("claude", "claude-sonnet-5-5", "origin/main", "aaaa111", "HEAD", "bbbb222", "3",
                     "2026-10-01"):
            self.assertIn(word, md.split("|")[0])

    def test_one_row_per_case_with_both_refs_the_difference_and_the_verdict(self):
        n = verdict.MIN_RUNS
        md = render.markdown(comparison([run(cost=2.0)] * n + [run("d")],
                                        [run(cost=1.0)] * n + [run("d")]))
        rows = table_rows(md)
        self.assertEqual([r[0] for r in rows], ["c", "d"])
        cost = rows[0][2]
        self.assertIn("$2.00", cost)
        self.assertIn("$1.00", cost)
        self.assertIn("-$1.00", cost)
        self.assertEqual(rows[0][-1], "better")
        self.assertEqual(rows[1][-1], "noise")

    def test_pipe_and_newline_in_a_case_name_are_escaped(self):
        md = render.markdown(comparison([run("a|b\nc")], [run("a|b\nc")]))
        rows = table_rows(md)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "a\\|b<br>c")
        self.assertEqual(len(rows[0]), len(table_rows(md)[0]))

    def test_unknown_cost_prints_unknown(self):
        md = render.markdown(comparison([run(cost=None)], [run(cost=None)]))
        cost = table_rows(md)[0][2]
        self.assertIn("unknown", cost)
        self.assertNotIn("$0", cost)
        self.assertNotIn("None", md)

    def test_null_header_field_prints_unknown(self):
        md = render.markdown(comparison([run()], [run()], pricing_as_of=None))
        self.assertIn("unknown", md.split("|")[0])
        self.assertNotIn("None", md)

    def test_case_on_one_side_only_prints_unknown_for_the_missing_side(self):
        md = render.markdown(comparison([run("c"), run("gone")], [run("c")]))
        gone = next(r for r in table_rows(md) if r[0] == "gone")
        self.assertIn("$1.00", gone[2])
        self.assertIn("unknown", gone[2])
        self.assertEqual(gone[-1], "unknown")

    def test_rubric_criteria_are_sub_rows_by_median(self):
        md = render.markdown(comparison([run(scores={"spec": s}) for s in (2, 4, 3, 3, 3)],
                                        [run(scores={"spec": 5})] * verdict.MIN_RUNS))
        rows = table_rows(md)
        self.assertEqual(len(rows), 2)
        sub = rows[1]
        self.assertIn("spec", sub[0])
        rubric = sub[render.COLUMNS.index("rubric")]
        self.assertTrue(rubric.startswith("3 (2–4) → 5"), rubric)
        self.assertIn("+2", rubric)
        self.assertEqual(sub[-1], "better")

    def test_names_the_cases_judged_on_pass_rate_alone(self):
        n = verdict.MIN_RUNS
        md = render.markdown(comparison([run("few", cost=2.0)] * 3 + [run("many", cost=2.0)] * n,
                                        [run("few", cost=1.0)] * 3 + [run("many", cost=1.0)] * n))
        note = next(line for line in md.splitlines() if "only the pass rate decides" in line)
        self.assertTrue(note.startswith(f"Under {n} runs a side"), note)
        self.assertTrue(note.endswith(": few"), note)
        self.assertEqual({r[0]: r[-1] for r in table_rows(md)}, {"few": "noise", "many": "better"})
        self.assertNotIn("only the pass rate decides",
                         render.markdown(comparison([run(cost=2.0)] * n, [run(cost=1.0)] * n)))

    def test_cases_worth_more_runs_are_listed(self):
        md = render.markdown(comparison([run("noisy", cost=c) for c in (1.0, 2.0, 3.0)] + [run("same")] * 3,
                                        [run("noisy", cost=c) for c in (1.5, 2.5, 3.5)] + [run("same")] * 3))
        section = md.split("## Worth more runs", 1)[1]
        self.assertIn("- noisy", section)
        self.assertNotIn("same", section)


class Html(unittest.TestCase):
    def test_is_one_self_contained_page(self):
        page = render.html(comparison([run()], [run()]))
        self.assertTrue(page.lstrip().lower().startswith("<!doctype html>"))
        for external in ("<script", "<link", "src=", "@import", "url("):
            self.assertNotIn(external, page.lower())

    def test_holds_the_same_content_html_escaped(self):
        n = verdict.MIN_RUNS
        c = comparison([run("<b>x</b>&y", cost=2.0)] * n, [run("<b>x</b>&y", cost=1.0)] * n)
        page = render.html(c)
        self.assertNotIn("<b>x</b>", page)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;&amp;y", page)
        for word in ("claude-sonnet-5-5", "aaaa111", "bbbb222", "2026-10-01", "$2.00", "-$1.00", "<td>better</td>",
                     "Worth more runs"):
            self.assertIn(word, page)
        self.assertNotIn("only the pass rate decides", page)

    def test_names_the_cases_judged_on_pass_rate_alone(self):
        page = render.html(comparison([run("few", cost=2.0)] * 3, [run("few", cost=1.0)] * 3))
        self.assertIn(f"Under {verdict.MIN_RUNS} runs a side, only the pass rate decides", page)
        self.assertIn("cannot make a case better or worse: few</p>", page)
        self.assertIn("<td>noise</td>", page)

    def test_newline_in_a_cell_is_a_line_break(self):
        page = render.html(comparison([run("a\nb")], [run("a\nb")]))
        self.assertIn("a<br>b", page)


if __name__ == "__main__":
    unittest.main()
