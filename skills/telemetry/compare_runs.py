#!/usr/bin/env python3
"""Compare two build-epic runs of the same epic, as JSON (`schema: build-epic-run-comparison/v1`).

Usage: python3 -I compare_runs.py --a <run.json> --a-metrics <metrics.json> [--a-branch <branch.json>] [--a-name A]
                                  --b <run.json> --b-metrics <metrics.json> [--b-branch <branch.json>] [--b-name B]
                                  --pricing <pricing.json>

Each run is its saved telemetry (the extractor's output), its outcome metrics (`run_metrics.py`'s output, or the
gh-35 runs' older shape) and optionally its final branch's measurements (`sourceLines`, `testLines`, `suite`, ...).
Prints, per run (`runs.<name>`), its time, cost (by model, token kind, the orchestrator and the subagents, plus
remote runs: measured from the metrics' `remote.cost_usd`, and estimated for runs without cost from the other run's
implementers), tokens, orchestrator figures, subagents by type, role and model, steps and outcomes; then
`comparison`: each figure side by side, with `<b>Minus<A>`, `<b>Vs<A>Pct` and which run did `better`. Roles are a
subagent's saved `role`, else `report.py`'s rule on its type and description (spec Q14 names). A cost that needs a
model missing from the pricing is null. Nothing reads git or writes prose.
"""
import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from report import role_of as role  # noqa: E402

SCHEMA = "build-epic-run-comparison/v1"
TOKEN_KINDS = ["input", "output", "cacheRead", "cacheWrite5m", "cacheWrite1h"]
ROLES = ("implement", "review", "re-review", "ticket-review", "security", "fix", "resolve", "tune", "plan", "explore",
         "other")
REVIEW_ROLES = ("review", "ticket-review", "re-review", "security")


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def dig(value, *keys):
    """value[k1][k2]..., or None when a level is missing."""
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def plus(*xs):
    """The sum, or None when any term is None (a cost with an unpriced model)."""
    return None if any(x is None for x in xs) else sum(xs)


def r(x, n=2):
    return None if x is None else round(x, n)


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def ratio(a, b, n=2):
    return r(a / b, n) if a is not None and b else None


def stats(values, n=2):
    v = sorted(x for x in values if x is not None)
    if not v:
        return None
    q = lambda p: v[min(len(v) - 1, int(round(p * (len(v) - 1))))]
    return {"n": len(v), "sum": r(sum(v), n), "min": r(v[0], n), "p25": r(q(.25), n),
            "median": r(statistics.median(v), n), "mean": r(statistics.mean(v), n),
            "p75": r(q(.75), n), "p90": r(q(.9), n), "max": r(v[-1], n)}


class Prices:
    def __init__(self, pricing):
        self.pricing = pricing

    def unpriced(self, tbm):
        """Whether a model with tokens has no price."""
        return any(m not in self.pricing["models"] and any(v.values()) for m, v in tbm.items())

    def cost(self, tbm):
        """USD at the table's prices; None when a model with tokens has no price."""
        if self.unpriced(tbm):
            return None
        return sum(x * self.pricing["models"][m][k] / 1e6 for m, v in tbm.items() if m in self.pricing["models"]
                   for k, x in v.items())

    def by_kind(self, tbm):
        if self.unpriced(tbm):
            return dict.fromkeys(TOKEN_KINDS)
        out = Counter()
        for m, v in tbm.items():
            for k, x in v.items() if m in self.pricing["models"] else ():
                out[k] += x * self.pricing["models"][m][k] / 1e6
        return {k: r(out[k]) for k in TOKEN_KINDS}


def add_tokens(acc, tbm):
    for m, v in tbm.items():
        for k, x in v.items():
            acc[m][k] += x


def sub_tokens(a, b):
    return {m: {k: a[m][k] - b.get(m, {}).get(k, 0) for k in a[m]} for m in a}


def plain(tbm):
    return {m: {k: int(v.get(k, 0)) for k in TOKEN_KINDS} for m, v in tbm.items()}


def total_tokens(tbm):
    return {k: int(sum(v.get(k, 0) for v in tbm.values())) for k in TOKEN_KINDS}


def remote_counts(met):
    """(remote runs, runs without cost, measured cost or None), from either metrics shape."""
    remote = met.get("remote")
    if isinstance(remote, dict):
        return remote.get("runs") or 0, remote.get("runs_without_cost") or 0, remote.get("cost_usd")
    count = dig(met, "remote_runs", "count") or 0
    return count, count, None


def analyse(name, tel, met, branch, prices):
    span = next(s for s in tel["spans"] if s["skill"] == "build-epic")
    other_spans = [s for s in tel["spans"] if s is not span]

    session_tokens = defaultdict(Counter)
    for s in tel["spans"]:
        add_tokens(session_tokens, s["tokensByModel"])
    span_tokens = defaultdict(Counter)
    add_tokens(span_tokens, span["tokensByModel"])
    sub_tok = defaultdict(Counter)
    for a in span["subagents"]:
        add_tokens(sub_tok, a["tokensByModel"])
    orch_tokens = sub_tokens(span_tokens, sub_tok)

    subs = []
    for i, a in enumerate(span["subagents"]):
        tt = total_tokens(a["tokensByModel"])
        subs.append({"index": i, "type": a["type"], "role": role(a), "description": a["description"],
                     "models": sorted(a["tokensByModel"]), "turns": a["turns"],
                     "toolCalls": a["toolCalls"], "tools": a["tools"],
                     "tokensByModel": plain(a["tokensByModel"]), "costUSD": r(prices.cost(a["tokensByModel"]), 4),
                     "avgContextTokens": int(tt["cacheRead"] / a["turns"]) if a["turns"] else None})

    def group(keyf):
        g = defaultdict(list)
        for s in subs:
            g[keyf(s)].append(s)
        out = {}
        for k, items in sorted(g.items(), key=lambda kv: -sum(x["costUSD"] or 0 for x in kv[1])):
            tok = defaultdict(Counter)
            for x in items:
                add_tokens(tok, x["tokensByModel"])
            out[k] = {"count": len(items), "costUSD": r(plus(*(x["costUSD"] for x in items))),
                      "costPerRunUSD": stats([x["costUSD"] for x in items], 3),
                      "turns": stats([x["turns"] for x in items], 1),
                      "toolCalls": sum(x["toolCalls"] for x in items),
                      "byModelCount": dict(Counter("+".join(x["models"]) for x in items)),
                      "tokens": total_tokens(tok)}
        return out

    orch_turns = span["turns"] - sum(a["turns"] for a in span["subagents"])
    orch_cost = prices.cost(orch_tokens)
    subs_cost = plus(*(s["costUSD"] for s in subs))
    other_cost = plus(*(prices.cost(s["tokensByModel"]) for s in other_spans))
    session_cost = prices.cost(session_tokens)
    orch_cache_read = sum(v["cacheRead"] for v in orch_tokens.values())

    steps = tel["steps"]
    by_agent_tool = defaultdict(lambda: {"count": 0, "failed": 0, "seconds": 0.0})
    for st in steps:
        b = by_agent_tool[f"{st['subagent'] or 'orchestrator'}|{st['tool']}"]
        b["count"] += 1
        b["failed"] += int(bool(st["failed"]))
        b["seconds"] += st["seconds"] or 0
    orch_steps = [s for s in steps if s["subagent"] is None]
    orch_failed = [{"tool": s["tool"], "label": s["label"], "start": s["start"]} for s in orch_steps if s["failed"]]
    longest = sorted(orch_steps, key=lambda s: -(s["seconds"] or 0))[:15]

    prs, tasks, tickets = met.get("prs") or {}, met.get("tasks") or {}, met.get("tickets") or {}
    per_ticket = met.get("per_ticket")
    per_ticket_min = [t.get("minutes_first_task_pr_to_ticket_merge") for t in (per_ticket or {}).values()]
    first_pr = prs.get("first_created")
    remote_runs, _, remote_cost = remote_counts(met)

    return {
        "subs": subs,
        "otherSpans": [{"name": s["name"], "skill": s["skill"], "turns": s["turns"],
                        "costUSD": r(prices.cost(s["tokensByModel"]), 4)} for s in other_spans],
        "out": {
            "id": name, "telemetryLabel": tel.get("label"), "sessionId": tel["sessionId"],
            "time": {
                "sessionStart": tel["start"], "sessionEnd": tel["end"],
                "sessionMinutes": r(tel["wallClockSeconds"] / 60, 1),
                "buildEpicSpanMinutes": r(span["wallClockSeconds"] / 60, 1),
                "firstPrCreated": first_pr, "lastPrMerged": prs.get("last_merged"),
                "firstPrToLastMergeMinutes": prs.get("wall_minutes_first_pr_to_last_merge"),
                "sessionStartToFirstPrMinutes": r((ts(first_pr) - ts(tel["start"])).total_seconds() / 60, 1)
                if first_pr else None,
                "perTicketFirstTaskPrToMergeMinutes": stats(per_ticket_min, 1),
                "stepSecondsTotal": {"orchestrator": r(sum(s["seconds"] or 0 for s in orch_steps), 1),
                                     "subagents": r(sum(s["seconds"] or 0 for s in steps if s["subagent"]), 1)},
            },
            "cost": {
                "telemetryReportedUSD": tel.get("cost"), "pricingAsOf": tel.get("pricingAsOf"),
                "recomputedSessionUSD": r(session_cost),
                "buildEpicSpanUSD": r(plus(orch_cost, subs_cost)),
                "otherSpansUSD": r(other_cost),
                "orchestratorUSD": r(orch_cost), "orchestratorShare": ratio(orch_cost, session_cost, 3),
                "subagentsUSD": r(subs_cost), "subagentsShare": ratio(subs_cost, session_cost, 3),
                "byModelUSD": {m: r(prices.cost({m: v})) for m, v in session_tokens.items()},
                "byTokenKindUSD": prices.by_kind(session_tokens),
                "orchestratorByTokenKindUSD": prices.by_kind(orch_tokens),
                "remoteMeasuredUSD": remote_cost,
                "unmeasuredRemoteRuns": None,  # needs the other run, filled in by compare()
                "perMergedPrUSD": ratio(session_cost, prs.get("merged")),
                "perTicketUSD": ratio(session_cost, tickets.get("count")),
                "perTaskUSD": ratio(session_cost, tasks.get("count")),
            },
            "tokens": {
                "sessionByModel": plain(session_tokens), "sessionTotal": total_tokens(session_tokens),
                "orchestratorByModel": plain(orch_tokens), "orchestratorTotal": total_tokens(orch_tokens),
                "subagentsByModel": plain(sub_tok), "subagentsTotal": total_tokens(sub_tok),
            },
            "orchestrator": {
                "turns": orch_turns, "steps": len(orch_steps),
                "failedSteps": len(orch_failed), "failedStepList": orch_failed,
                "stepsByTool": dict(Counter(s["tool"] for s in orch_steps)),
                "avgContextTokensPerTurn": int(orch_cache_read / orch_turns) if orch_turns else None,
                "costPerTurnUSD": ratio(orch_cost, orch_turns, 4),
                "models": sorted(m for m, v in orch_tokens.items() if v["output"] > 0),
                "longestSteps": [{"tool": s["tool"], "seconds": s["seconds"], "label": s["label"]} for s in longest],
            },
            "buildEpicSpan": {"turns": span["turns"], "toolCalls": span["toolCalls"], "tools": span["tools"],
                              "subagentCount": len(span["subagents"])},
            "subagents": {
                "count": len(subs), "turnsTotal": sum(s["turns"] for s in subs),
                "byType": group(lambda s: s["type"]),
                "byRole": group(lambda s: s["role"]),
                "byModel": group(lambda s: "+".join(s["models"])),
                "costliest": sorted(({k: s[k] for k in ("type", "role", "description", "turns", "costUSD",
                                                         "avgContextTokens", "models")} for s in subs),
                                    key=lambda s: -(s["costUSD"] or 0))[:15],
            },
            "steps": {
                "total": len(steps), "failed": sum(1 for s in steps if s["failed"]),
                "byAgentAndTool": {k: {"count": v["count"], "failed": v["failed"], "seconds": r(v["seconds"], 1)}
                                   for k, v in sorted(by_agent_tool.items(), key=lambda kv: -kv[1]["seconds"])},
            },
            "outcomes": {
                "tickets": met.get("tickets"), "tasks": met.get("tasks"), "prs": met.get("prs"),
                "prsPerTicket": ratio(prs.get("count"), tickets.get("count")),
                "reviewerRunsPerTicket": ratio(sum(1 for s in subs if s["role"] in REVIEW_ROLES), tickets.get("count")),
                "findingsSaved": met.get("findings_saved"), "humanDecisions": met.get("human_decisions"),
                "testsFromRunFolder": met.get("tests"),
                "integrationCatchupMerges": met.get("integration_catchup_merges"),
                "remoteRuns": met.get("remote_runs", met.get("remote", {})),
                "metricsGaps": met.get("gaps", []),
            },
            "finalBranch": branch,
            "perTicket": per_ticket,
        },
    }


def estimate_remote(X, other_subs, met):
    """Fill X's remote cost: runs without cost priced at the other run's implementer cost per run."""
    runs, unmeasured, measured = remote_counts(met)
    base = plus(X["cost"]["recomputedSessionUSD"], measured or 0)
    st = stats([s["costUSD"] for s in other_subs if s["role"] == "implement"], 4)
    if not unmeasured:
        X["cost"]["totalEstimateUSD"] = (None if base is None else
                                         {k: r(base) for k in ("low", "mid", "meanBased", "high")})
        return
    est = {"runs": unmeasured, "tasks": dig(met, "remote_runs", "tasks"),
           "minutesKnown": dig(met, "remote_runs", "minutes_total_known"),
           "basisPerRunUSD": {k: st[k] for k in ("p25", "median", "mean", "p75")} if st else None,
           "estimateUSD": {"low": r(unmeasured * st["p25"]), "mid": r(unmeasured * st["median"]),
                           "meanBased": r(unmeasured * st["mean"]), "high": r(unmeasured * st["p75"])}
           if st else None}
    X["cost"]["unmeasuredRemoteRuns"] = est
    X["cost"]["totalEstimateUSD"] = ({k: r(base + v) for k, v in est["estimateUSD"].items()}
                                     if est["estimateUSD"] and base is not None else None)


def compare(names, outs, metrics):
    a, b = names
    A, B = outs
    minus, pct = f"{b}Minus{a[:1].upper()}{a[1:]}", f"{b}Vs{a[:1].upper()}{a[1:]}Pct"

    def metric(va, vb, unit, lower_is_better=True):
        d = {a: va, b: vb, "unit": unit}
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            d[minus] = r(vb - va)
            d[pct] = r((vb - va) / va * 100, 1) if va else None
            if lower_is_better is not None and vb != va:
                d["better"] = a if (va < vb) == lower_is_better else b
        return d

    def pair(f, unit, lower_is_better=True):
        return metric(f(A), f(B), unit, lower_is_better)

    def role(X, k, f="count"):
        return (X["subagents"]["byRole"].get(k) or {}).get(f, 0)

    def suite(X, f):
        return dig(X, "finalBranch", "suite", f)

    def mid(X):
        return dig(X, "cost", "totalEstimateUSD", "mid")

    remote = [remote_counts(m)[0] for m in metrics]
    return {
        "time": {
            "sessionMinutes": pair(lambda X: X["time"]["sessionMinutes"], "min"),
            "firstPrToLastMergeMinutes": pair(lambda X: X["time"]["firstPrToLastMergeMinutes"], "min"),
            "sessionStartToFirstPrMinutes": pair(lambda X: X["time"]["sessionStartToFirstPrMinutes"], "min"),
            "perTicketMedianMinutes": pair(lambda X: dig(X, "time", "perTicketFirstTaskPrToMergeMinutes", "median"),
                                           "min"),
            "perTicketMaxMinutes": pair(lambda X: dig(X, "time", "perTicketFirstTaskPrToMergeMinutes", "max"), "min"),
        },
        "cost": {
            "measuredUSD": pair(lambda X: X["cost"]["recomputedSessionUSD"], "USD"),
            "totalEstimateMidUSD": pair(mid, "USD"),
            "totalEstimateRangeUSD": {X["id"]: [dig(X, "cost", "totalEstimateUSD", "low"),
                                                dig(X, "cost", "totalEstimateUSD", "high")] for X in outs}
            | {"unit": "USD"},
            "orchestratorUSD": pair(lambda X: X["cost"]["orchestratorUSD"], "USD"),
            "subagentsUSD": pair(lambda X: X["cost"]["subagentsUSD"], "USD"),
            "perMergedPrUSDMeasured": pair(lambda X: X["cost"]["perMergedPrUSD"], "USD"),
            "perMergedPrUSDEstimated": pair(lambda X: ratio(mid(X), dig(X, "outcomes", "prs", "merged")), "USD"),
            "perTicketUSDEstimated": pair(lambda X: ratio(mid(X), dig(X, "outcomes", "tickets", "count")), "USD"),
            "perPassingTestUSDEstimated": pair(lambda X: ratio(mid(X), suite(X, "passed")), "USD"),
        },
        "orchestrator": {
            "turns": pair(lambda X: X["orchestrator"]["turns"], "turns"),
            "avgContextTokensPerTurn": pair(lambda X: X["orchestrator"]["avgContextTokensPerTurn"], "tokens"),
            "costPerTurnUSD": pair(lambda X: X["orchestrator"]["costPerTurnUSD"], "USD"),
            "steps": pair(lambda X: X["orchestrator"]["steps"], "steps"),
            "failedSteps": pair(lambda X: X["orchestrator"]["failedSteps"], "steps"),
            "bashCalls": pair(lambda X: X["orchestrator"]["stepsByTool"].get("Bash", 0), "calls"),
            "costShare": pair(lambda X: X["cost"]["orchestratorShare"], "fraction"),
        },
        "subagents": {
            "localSubagentRuns": pair(lambda X: X["subagents"]["count"], "runs", None),
            "remoteAgentRuns": metric(remote[0], remote[1], "runs", None),
            "reviewerRunsPerTicket": pair(lambda X: X["outcomes"]["reviewerRunsPerTicket"], "runs"),
            **{f"{k}Runs": pair(lambda X, k=k: role(X, k), "runs", None) for k in ROLES},
            **{f"{k}USD": pair(lambda X, k=k: role(X, k, "costUSD"), "USD") for k in ROLES},
            "reviewerModels": {X["id"]: X["subagents"]["byType"].get("pr-reviewer", {}).get("byModelCount")
                               for X in outs},
        },
        "outcomes": {
            "tickets": pair(lambda X: dig(X, "outcomes", "tickets", "count"), "tickets", None),
            "tasks": pair(lambda X: dig(X, "outcomes", "tasks", "count"), "tasks", None),
            "tasksByKind": {X["id"]: dig(X, "outcomes", "tasks", "by_kind") for X in outs},
            "prsMerged": pair(lambda X: dig(X, "outcomes", "prs", "merged"), "PRs", None),
            "prsPerTicket": pair(lambda X: X["outcomes"]["prsPerTicket"], "PRs"),
            "prAdditions": pair(lambda X: dig(X, "outcomes", "prs", "additions"), "lines", None),
            "humanDecisions": pair(lambda X: X["outcomes"]["humanDecisions"], "decisions"),
            "findingsSaved": pair(lambda X: dig(X, "outcomes", "findingsSaved", "total"), "findings", None),
            "tasksNeedingRetry": pair(lambda X: len(dig(X, "outcomes", "remoteRuns", "tasks_needing_retry") or []),
                                      "tasks"),
        },
        "finalBranch": {
            "testsPassed": pair(lambda X: suite(X, "passed"), "tests", False),
            "testsSkipped": pair(lambda X: suite(X, "skipped"), "tests"),
            "testsFailed": pair(lambda X: suite(X, "failed"), "tests"),
            "suiteWallSeconds": pair(lambda X: suite(X, "wallSeconds"), "s"),
            "sourceLines": pair(lambda X: dig(X, "finalBranch", "sourceLines"), "lines", None),
            "testLines": pair(lambda X: dig(X, "finalBranch", "testLines"), "lines", None),
            "testToSourceLineRatio": pair(lambda X: dig(X, "finalBranch", "testToSourceLineRatio"), "ratio", False),
            "readmeLines": pair(lambda X: dig(X, "finalBranch", "readmeLines"), "lines", None),
            "diffVsMergeBase": {X["id"]: dig(X, "finalBranch", "diffVsMergeBase") for X in outs},
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Compare two build-epic runs of the same epic, as JSON.")
    for side in ("a", "b"):
        parser.add_argument(f"--{side}", required=True, type=Path, help=f"run {side}'s saved telemetry JSON")
        parser.add_argument(f"--{side}-metrics", required=True, type=Path, help=f"run {side}'s outcome metrics JSON")
        parser.add_argument(f"--{side}-branch", type=Path, help=f"run {side}'s final branch measurements JSON")
        parser.add_argument(f"--{side}-name", default=side, help=f"run {side}'s key in the output (default: {side})")
    parser.add_argument("--pricing", required=True, type=Path, help="the price table (skills/telemetry/pricing.json)")
    args = parser.parse_args(argv)
    names = (args.a_name, args.b_name)
    if not all(names) or names[0] == names[1]:
        parser.error("--a-name and --b-name must differ and not be empty")

    prices = Prices(load(args.pricing))
    metrics = (load(args.a_metrics), load(args.b_metrics))
    runs = [analyse(name, load(tel), met, load(branch) if branch else None, prices)
            for name, tel, met, branch in zip(names, (args.a, args.b), metrics, (args.a_branch, args.b_branch))]
    for i, run in enumerate(runs):
        estimate_remote(run["out"], runs[1 - i]["subs"], metrics[i])
    outs = [run["out"] for run in runs]
    comparison = compare(names, outs, metrics)
    for run in runs:
        run["out"]["subagentList"] = run["subs"]
        run["out"]["otherSpans"] = run["otherSpans"]
    models = sorted({m for X in outs for m in X["tokens"]["sessionByModel"]})
    out = {
        "schema": SCHEMA,
        "comparison": comparison,
        "runs": dict(zip(names, outs)),
        "pricing": {"asOf": prices.pricing.get("asOf"), "unit": prices.pricing.get("unit"),
                    "source": prices.pricing.get("source"),
                    "models": {m: prices.pricing["models"].get(m) for m in models}},
    }
    sys.stdout.reconfigure(encoding="utf-8")
    json.dump(out, sys.stdout, indent=2, ensure_ascii=False)
    print()


if __name__ == "__main__":
    main()
