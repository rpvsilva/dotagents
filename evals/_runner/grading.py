"""Re-grading one saved run with its case's current checks and rubric, never calling the case's tool: used by
`grade.py` on every saved run and by `run.py` on a reused one whose checks or rubric changed since.

`regrade()` re-runs the checks on the run's kept state and rewrites `checks`, `checks_hash` and `passed`; it judges
the run again (`judge.py`, through the run's own tool) only when `needs_judging()`, and gives a case with no rubric
`rubric: null`. Every other field of `result.json` is kept.
"""
import tempfile
from pathlib import Path

import adapters
import checks
import judge
import results


def needs_judging(res, case, rejudge=False):
    """True when the run's saved rubric no longer stands for the case's rubric."""
    if not case.get("rubric"):
        return False
    old = res["rubric"] or {}
    return rejudge or old.get("rubric_hash") != judge.rubric_hash(case) or old.get("judge_model") != case["judge_model"]


def stale(res, case):
    """True when the saved run's checks, rubric or `judge_model` differ from the case's current ones."""
    return (res["checks_hash"] != checks.checks_hash(case["dir"]) or needs_judging(res, case)
            or (res["rubric"] is not None and not case.get("rubric")))


def regrade(run_dir, res, case, checkout_dir, rejudge=False):
    """The run kept in `run_dir` (its saved result `res`) checked again and, when `needs_judging`, judged again;
    saved and returned with whether the judge ran."""
    run_dir = Path(run_dir)
    with tempfile.TemporaryDirectory(prefix="evals-grade-input-") as empty:
        paths = {"work": run_dir / "work", "scratch": run_dir / "scratch", "home": run_dir / "home",
                 "input": case.get("input") or Path(empty), "transcript": run_dir / (res["transcript"] or
                                                                                    adapters.TRANSCRIPT),
                 "gh_calls": run_dir / "gh-calls", "checkout": checkout_dir}
        found = checks.run(case["checks"], paths)
    new = {**res, "checks": found, "checks_hash": checks.checks_hash(case["dir"]),
           "passed": all(found.values()) and not res["capped"] and not res["error"]}
    judged = needs_judging(res, case, rejudge)
    if not case.get("rubric"):
        new["rubric"] = None
    results.write(run_dir, new)
    if judged:
        new["rubric"] = judge.judge(run_dir, case, checkout_dir, res["tool"])
        results.write(run_dir, new)
    return results.read(run_dir), judged
