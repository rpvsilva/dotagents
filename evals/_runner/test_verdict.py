"""Tests for `verdict.py`: per-case aggregates of saved runs and the better, worse or noise verdict of two refs."""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import verdict  # noqa: E402


def run(case="c", passed=True, cost=1.0, turns=10, secs=60.0, scores=None):
    rubric = None if scores is None else {"scores": {k: {"score": v, "reason": "r"} for k, v in scores.items()},
                                          "judge_model": "claude-haiku-5-5"}
    return {"case": case, "passed": passed, "cost_usd": cost, "turns": turns, "duration_s": secs, "rubric": rubric}


def runs(n, case="c", passes=None, **fields):
    """`n` runs of one case; `passes` of them pass (all by default); list fields give one value per run, repeated
    from the start when shorter than `n`."""
    passes = n if passes is None else passes
    out = []
    for i in range(n):
        kw = {k: v[i % len(v)] if isinstance(v, list) else v for k, v in fields.items()}
        out.append(run(case, passed=i < passes, **kw))
    return out


N = verdict.MIN_RUNS  # enough runs for cost, turns, time and rubric to decide


def scored(*scores):
    """`N` runs scoring `spec`, `scores` repeated from the start."""
    return [run(scores={"spec": scores[i % len(scores)]}) for i in range(N)]


def compare(base, head):
    return verdict.verdict(verdict.summarise(base), verdict.summarise(head))


class Summarise(unittest.TestCase):
    def test_gives_pass_rate_median_and_range_per_case(self):
        s = verdict.summarise(runs(3, passes=2, cost=[1.0, 3.0, 2.0], turns=[5, 9, 7], secs=[10.0, 30.0, 20.0])
                              + runs(1, case="d", passes=0))
        c = s["c"]
        self.assertEqual((c["runs"], c["passed"]), (3, 2))
        self.assertAlmostEqual(c["pass_rate"], 2 / 3)
        self.assertEqual(c["cost_usd"], {"median": 2.0, "min": 1.0, "max": 3.0})
        self.assertEqual(c["turns"], {"median": 7, "min": 5, "max": 9})
        self.assertEqual(c["duration_s"], {"median": 20.0, "min": 10.0, "max": 30.0})
        self.assertEqual((s["d"]["runs"], s["d"]["pass_rate"]), (1, 0.0))

    def test_rubric_is_the_median_score_per_criterion(self):
        s = verdict.summarise([run(scores={"spec": 2, "tasks": 5}), run(scores={"spec": 4, "tasks": 3}),
                               run(scores={"spec": 3}), run(scores=None)])
        self.assertEqual(s["c"]["rubric"], {"spec": {"median": 3, "min": 2, "max": 4},
                                            "tasks": {"median": 4.0, "min": 3, "max": 5}})

    def test_null_values_are_left_out_of_medians_and_ranges(self):
        s = verdict.summarise(runs(3, cost=[None, 1.0, 5.0], turns=[None, None, 4]))
        self.assertEqual(s["c"]["cost_usd"], {"median": 3.0, "min": 1.0, "max": 5.0})
        self.assertEqual(s["c"]["turns"], {"median": 4, "min": 4, "max": 4})

    def test_metric_with_no_values_is_unknown_not_zero(self):
        s = verdict.summarise(runs(3, cost=None))
        self.assertIsNone(s["c"]["cost_usd"])
        v = compare(runs(3, cost=None), runs(3, cost=0.5))
        self.assertEqual(v["cases"]["c"]["metrics"]["cost_usd"]["verdict"], "unknown")
        self.assertIsNone(v["cases"]["c"]["metrics"]["cost_usd"]["diff"])
        self.assertEqual(v["cases"]["c"]["verdict"], "noise")

    def test_error_rubric_and_null_pass_are_left_out(self):
        s = verdict.summarise([run(passed=None), {**run(), "rubric": {"error": "judge", "judge_model": "m"}}])
        self.assertEqual((s["c"]["runs"], s["c"]["passed"], s["c"]["pass_rate"]), (2, 1, 1.0))
        self.assertEqual(s["c"]["rubric"], {})

    def test_null_or_non_numeric_rubric_score_is_left_out(self):
        s = verdict.summarise([run(scores={"spec": None}), run(scores={"spec": "n/a"}), run(scores={"spec": 4})])
        self.assertEqual(s["c"]["rubric"], {"spec": {"median": 4, "min": 4, "max": 4}})


class RangeRule(unittest.TestCase):
    def test_overlapping_ranges_are_noise(self):
        v = compare(runs(N, cost=[1.0, 2.0, 3.0]), runs(N, cost=[2.5, 3.5, 4.0]))
        self.assertEqual(v["cases"]["c"]["metrics"]["cost_usd"]["verdict"], "noise")
        self.assertEqual(v["cases"]["c"]["verdict"], "noise")

    def test_lower_cost_without_overlap_is_better(self):
        v = compare(runs(N, cost=[2.0, 2.5, 3.0]), runs(N, cost=[1.0, 1.2, 1.9]))
        self.assertEqual(v["cases"]["c"]["verdict"], "better")
        self.assertAlmostEqual(v["cases"]["c"]["metrics"]["cost_usd"]["diff"], 1.2 - 2.5)

    def test_more_turns_or_time_without_overlap_is_worse(self):
        self.assertEqual(compare(runs(N, turns=[5, 6, 7]), runs(N, turns=[8, 9, 9]))["cases"]["c"]["verdict"], "worse")
        self.assertEqual(compare(runs(N, secs=[5.0, 6.0, 7.0]), runs(N, secs=[50.0, 60.0, 70.0]))["cases"]["c"]["verdict"],
                         "worse")

    def test_a_change_under_the_floor_is_noise_even_without_overlap(self):
        # The pilot's same-skills compare: a $0.00001 gap between two tight ranges came out `better`.
        v = compare(runs(N, cost=[0.044364, 0.044449, 0.044479]), runs(N, cost=[0.044073, 0.044242, 0.04435]))
        self.assertEqual(v["cases"]["c"]["metrics"]["cost_usd"]["verdict"], "noise")
        self.assertEqual(v["cases"]["c"]["verdict"], "noise")
        self.assertEqual(v["more_runs"], [])

    def test_a_change_at_the_floor_without_overlap_is_real(self):
        v = compare(runs(N, cost=[0.99, 1.0, 1.01]), runs(N, cost=[1.09, 1.1, 1.11]))
        self.assertEqual(v["cases"]["c"]["verdict"], "worse")
        v = compare(runs(N, secs=[59.0, 60.0, 61.0]), runs(N, secs=[54.5, 55.0, 55.5]))
        self.assertEqual(v["cases"]["c"]["verdict"], "noise")

    def test_the_floor_leaves_rubric_and_a_zero_median_alone(self):
        v = compare(scored(4), scored(5))
        self.assertEqual(v["cases"]["c"]["verdict"], "better")
        v = compare(runs(N, cost=0.0), runs(N, cost=0.01))
        self.assertEqual(v["cases"]["c"]["verdict"], "worse")

    def test_higher_rubric_without_overlap_is_better(self):
        base = scored(2, 2, 3)
        head = scored(4, 5, 4)
        v = compare(base, head)["cases"]["c"]
        self.assertEqual(v["metrics"]["rubric"]["spec"]["verdict"], "better")
        self.assertEqual(v["verdict"], "better")


class PassRateRule(unittest.TestCase):
    def test_three_of_three_against_one_of_three_is_real(self):
        self.assertEqual(compare(runs(3, passes=1), runs(3, passes=3))["cases"]["c"]["verdict"], "better")
        self.assertEqual(compare(runs(3, passes=3), runs(3, passes=1))["cases"]["c"]["verdict"], "worse")

    def test_two_of_three_against_one_of_three_is_noise(self):
        v = compare(runs(3, passes=1), runs(3, passes=2))
        self.assertEqual(v["cases"]["c"]["metrics"]["pass_rate"]["verdict"], "noise")
        self.assertEqual(v["cases"]["c"]["verdict"], "noise")

    def test_threshold_scales_with_runs(self):
        # runs 5: ceil(2/3 * 5) = 4 runs must move
        self.assertEqual(compare(runs(5, passes=2), runs(5, passes=5))["cases"]["c"]["verdict"], "noise")
        self.assertEqual(compare(runs(5, passes=1), runs(5, passes=5))["cases"]["c"]["verdict"], "better")
        self.assertEqual(compare(runs(5, passes=5), runs(5, passes=1))["cases"]["c"]["verdict"], "worse")

    def test_pass_rate_change_outranks_cost_of_the_other_sign(self):
        cheaper_but_failing = compare(runs(N, passes=N, cost=5.0), runs(N, passes=0, cost=1.0))
        self.assertEqual(cheaper_but_failing["cases"]["c"]["metrics"]["cost_usd"]["verdict"], "better")
        self.assertEqual(cheaper_but_failing["cases"]["c"]["verdict"], "worse")
        dearer_but_passing = compare(runs(N, passes=0, cost=1.0), runs(N, passes=N, cost=5.0))
        self.assertEqual(dearer_but_passing["cases"]["c"]["verdict"], "better")


class PassRateOnly(unittest.TestCase):
    def test_under_min_runs_cost_turns_time_and_rubric_do_not_decide(self):
        # The pilot's second same-skills compare: 3 runs a side, cost 25% apart and rubric 4,4,4 against 3,3,3.
        v = compare(runs(3, cost=[0.066, 0.0726, 0.0707], secs=[5.0, 6.0, 7.0])
                    + [run("r", scores={"spec": 4})] * 3,
                    runs(3, cost=[0.0543, 0.046, 0.0592], secs=[50.0, 60.0, 70.0])
                    + [run("r", scores={"spec": 3})] * 3)
        c, r = v["cases"]["c"], v["cases"]["r"]
        self.assertEqual((c["verdict"], r["verdict"]), ("noise", "noise"))
        self.assertEqual(c["metrics"]["cost_usd"]["verdict"], "noise")
        self.assertEqual(r["metrics"]["rubric"]["spec"]["verdict"], "noise")
        self.assertEqual(v["pass_rate_only"], ["c", "r"])
        self.assertEqual(v["more_runs"], ["c", "r"])

    def test_under_min_runs_the_pass_rate_still_decides(self):
        v = compare(runs(3, passes=3), runs(3, passes=1))
        self.assertEqual(v["cases"]["c"]["verdict"], "worse")
        self.assertEqual(v["pass_rate_only"], ["c"])

    def test_the_side_with_fewer_runs_counts(self):
        v = compare(runs(N, cost=2.0), runs(N - 1, cost=1.0))
        self.assertEqual(v["cases"]["c"]["verdict"], "noise")
        self.assertEqual(v["pass_rate_only"], ["c"])
        v = compare(runs(N, cost=2.0), runs(N, cost=1.0))
        self.assertEqual(v["cases"]["c"]["verdict"], "better")
        self.assertEqual(v["pass_rate_only"], [])


class MoreRuns(unittest.TestCase):
    def test_noise_with_different_medians_is_worth_more_runs(self):
        v = compare(runs(3, cost=[1.0, 2.0, 3.0], case="moved") + runs(3, case="same"),
                    runs(3, cost=[2.5, 2.8, 4.0], case="moved") + runs(3, case="same"))
        self.assertEqual(v["more_runs"], ["moved"])
        self.assertTrue(v["cases"]["moved"]["more_runs"])
        self.assertFalse(v["cases"]["same"]["more_runs"])

    def test_a_cost_or_time_move_under_the_floor_is_not_worth_more_runs(self):
        v = compare(runs(3, cost=[1.0, 2.0, 3.0], secs=[60.0, 70.0, 80.0]),
                    runs(3, cost=[1.1, 2.1, 3.0], secs=[62.0, 72.0, 81.0]))
        self.assertEqual(v["more_runs"], [])

    def test_noise_with_a_rubric_move_is_worth_more_runs(self):
        v = compare([run(scores={"spec": s}) for s in (3, 3, 4)], [run(scores={"spec": s}) for s in (3, 4, 4)])
        self.assertEqual(v["more_runs"], ["c"])

    def test_noise_with_a_pass_rate_move_is_worth_more_runs(self):
        self.assertEqual(compare(runs(3, passes=1), runs(3, passes=2))["more_runs"], ["c"])

    def test_a_real_verdict_is_not_worth_more_runs(self):
        self.assertEqual(compare(runs(3, passes=1), runs(3, passes=3))["more_runs"], [])

    def test_case_on_one_side_only_is_unknown(self):
        v = compare(runs(3, case="old"), runs(3, case="new"))
        self.assertEqual({c: v["cases"][c]["verdict"] for c in v["cases"]}, {"old": "unknown", "new": "unknown"})
        self.assertEqual(v["more_runs"], [])


if __name__ == "__main__":
    unittest.main()
