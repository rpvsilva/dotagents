"""Per-case aggregates of saved runs, and the verdict of a head ref against a base ref.

`summarise(runs)` takes one ref's `result.json` dicts and gives, per case: the runs, how many passed and the pass
rate, the median and min-max of cost, turns and time, and the median and min-max rubric score per criterion. A `null`
value is left out; a metric with no values is `None` (unknown), never 0.

`verdict(base, head)` takes two summaries and gives each case `better`, `worse`, `noise` or `unknown` (a case one
side lacks). A metric's change is real when its min-max ranges do not overlap, and the pass rate's when it moves by at
least `ceil(2/3 * runs)` runs (spec open question 10); otherwise it is noise. Higher pass rate and rubric are better;
lower cost, turns and time are better. The first metric in `ORDER` with a real change decides the case, so a
pass-rate change outranks a cost change of the other sign; rubric criteria real in both directions decide nothing. A `noise` case whose medians or pass rate differ is worth more runs.
"""
import statistics

METRICS = ("cost_usd", "turns", "duration_s")  # lower is better
ORDER = ("pass_rate", "rubric") + METRICS


def spread(values):
    """`{median, min, max}` of the non-null values, or None when there are none."""
    v = sorted(x for x in values if x is not None)
    if not v:
        return None
    return {"median": statistics.median(v), "min": v[0], "max": v[-1]}


def _scores(result):
    scores = (result.get("rubric") or {}).get("scores") or {}
    for criterion, entry in scores.items():
        score = entry.get("score") if isinstance(entry, dict) else None
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            yield criterion, score


def summarise(runs):
    """Per case: `runs`, `passed`, `pass_rate`, a `spread()` per metric of `METRICS` and `rubric` per criterion."""
    by_case = {}
    for result in runs:
        by_case.setdefault(result["case"], []).append(result)
    out = {}
    for case, rs in sorted(by_case.items()):
        judged = [r["passed"] for r in rs if r.get("passed") is not None]
        criteria = {}
        for r in rs:
            for criterion, score in _scores(r):
                criteria.setdefault(criterion, []).append(score)
        out[case] = {"runs": len(rs), "passed": sum(1 for p in judged if p),
                     "pass_rate": sum(1 for p in judged if p) / len(judged) if judged else None,
                     **{m: spread(r.get(m) for r in rs) for m in METRICS},
                     "rubric": {c: spread(s) for c, s in sorted(criteria.items())}}
    return out


def needed_moves(runs):
    """How many of `runs` the pass count must move by to be real: two-thirds, rounded up."""
    return -(-2 * runs // 3)


def _range(base, head, sign):
    """Compare two spreads; `sign` is 1 when higher is better, -1 when lower is."""
    if base is None or head is None:
        return {"base": base, "head": head, "diff": None, "verdict": "unknown"}
    diff = head["median"] - base["median"]
    if head["min"] > base["max"]:
        word = "better" if sign > 0 else "worse"
    elif head["max"] < base["min"]:
        word = "worse" if sign > 0 else "better"
    else:
        word = "noise"
    return {"base": base, "head": head, "diff": diff, "verdict": word}


def _pass_rate(base, head):
    b, h = base["pass_rate"], head["pass_rate"]
    if b is None or h is None:
        return {"base": b, "head": h, "diff": None, "verdict": "unknown"}
    runs = max(base["runs"], head["runs"])
    diff = h - b
    real = abs(diff) * runs >= needed_moves(runs) - 1e-9
    return {"base": b, "head": h, "diff": diff, "verdict": ("better" if diff > 0 else "worse") if real else "noise"}


def compare_case(base, head):
    """The verdict of one case present on both sides, with each metric's own verdict and whether to run it more."""
    metrics = {"pass_rate": _pass_rate(base, head)}
    metrics.update({m: _range(base[m], head[m], -1) for m in METRICS})
    metrics["rubric"] = {c: _range(base["rubric"].get(c), head["rubric"].get(c), 1)
                         for c in sorted(set(base["rubric"]) | set(head["rubric"]))}
    word = "noise"
    for name in ORDER:
        found = [m["verdict"] for m in (metrics[name].values() if name == "rubric" else [metrics[name]])]
        real = {v for v in found if v in ("better", "worse")}
        if len(real) == 1:
            word = real.pop()
            break
    flat = [metrics[m] for m in ("pass_rate",) + METRICS] + list(metrics["rubric"].values())
    more = word == "noise" and any(m["diff"] for m in flat)
    return {"verdict": word, "metrics": metrics, "more_runs": more}


def verdict(base, head):
    """`{"cases": {case: {verdict, metrics, more_runs}}, "more_runs": [case, ...]}` for two `summarise()` outputs."""
    cases = {}
    for case in sorted(set(base) | set(head)):
        if case in base and case in head:
            cases[case] = compare_case(base[case], head[case])
        else:
            cases[case] = {"verdict": "unknown", "metrics": {}, "more_runs": False,
                           "base": base.get(case), "head": head.get(case)}
    return {"cases": cases, "more_runs": [c for c, v in cases.items() if v["more_runs"]]}
