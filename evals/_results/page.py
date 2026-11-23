#!/usr/bin/env python3
"""The results page: aggregates of build-epic runs and saved eval runs, as Markdown.

Usage: python3 -I evals/_results/page.py --run <run folder> [--metrics <metrics.json>] [--telemetry <session.json>]
                                        [--run ...] [--evals-results <dir>] [--pricing <pricing.json>] --out <file>

Each `--run` starts a run; the `--metrics` and `--telemetry` after it belong to it (a run without `--metrics` gets
`run_metrics.py` run on its folder). Per run the page gives cost per task (`compare_runs.py`'s `derived()`, naming its
basis and `pricing_as_of`), human round trips (`run_metrics.py`'s `human_round_trips`: the dated quotes after
approval, not the session's `humanMessages`) and the PRs merged by an `app/*` login, as a count. Per eval case it gives
the pass rate of the saved runs of the case's latest `ref_sha`, read from the store without running a case or calling a
model. Each figure names its date, the model(s) and the Claude Code version(s) behind it.

Output is a whitelist: counts, sums, ratios, model names, versions and dates only; runs are numbered in argument order,
so no session id, repo, path, folder name, branch, login, PR number, step label or transcript reaches the page. A
missing input prints `unknown`, never 0. The same inputs give the same bytes.
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "skills" / "telemetry"))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "_runner"))

import compare_runs  # noqa: E402
import grade  # noqa: E402
import verdict  # noqa: E402

UNKNOWN = "unknown"
FIRST_LINE = ("Written by `python3 -I evals/_results/page.py --run <run folder> ... --evals-results <dir> --out <file>`; "
              "edit the inputs, not this file.")
RUN_COLUMNS = ("run", "date", "model(s)", "Claude Code", "cost per task", "cost basis", "pricing as of",
               "human round trips", "PRs merged by the agent")
EVAL_COLUMNS = ("case", "date", "model(s)", "Claude Code", "pass rate", "runs judged")


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def metrics_of(run, given):
    """The run's metrics: the given file, else `run_metrics.py`'s output on the folder; None when neither reads."""
    if given:
        return load(given)
    out = subprocess.run([sys.executable, "-I", str(REPO / "skills" / "telemetry" / "run_metrics.py"), str(run)],
                         capture_output=True, text=True, stdin=subprocess.DEVNULL)
    try:
        return json.loads(out.stdout) if out.returncode == 0 else None
    except ValueError:
        return None


def date_of(value):
    return value[:10] if isinstance(value, str) and len(value) >= 10 else None


def merged_by_agent(met):
    """The count of PRs merged by an `app/*` login; None when the metrics do not say."""
    by = ((met or {}).get("prs") or {}).get("merged_by")
    if not isinstance(by, dict):
        return None
    return sum(n for login, n in by.items() if str(login).startswith("app/") and isinstance(n, int))


def one_run(index, run, given_metrics, telemetry, prices):
    """One run's whitelisted figures."""
    met = metrics_of(run, given_metrics)
    row = {"run": f"run {index}", "date": None, "models": set(), "versions": set(), "cost_per_task": None,
           "basis": None, "pricing_as_of": None,
           "round_trips": (met or {}).get("human_round_trips") if met else None,
           "prs_merged_by_agent": merged_by_agent(met)}
    remote = ((met or {}).get("remote") or {})
    row["models"] |= set((remote.get("models") or {}))
    row["versions"] |= {v for v in (remote.get("claude_versions") or {}) if v != "unknown"}
    if met and not isinstance(row["round_trips"], int):
        row["round_trips"] = None
    if telemetry and met is not None:
        tel = load(telemetry)
        analysed = compare_runs.analyse("run", tel, met, None, prices)
        X = analysed["out"]
        compare_runs.estimate_remote(X, analysed["subs"], met)
        X["subagentList"] = analysed["subs"]
        row["cost_per_task"] = compare_runs.derived(X, met)["totalCostPerTaskUSD"]
        row["basis"] = compare_runs.cost_basis(X)
        row["pricing_as_of"] = prices.pricing.get("asOf")
        row["date"] = date_of(tel.get("date") or tel.get("start"))
        row["models"] |= {m for m, v in X["tokens"]["sessionByModel"].items() if any(v.values())}
        row["versions"] |= set(tel.get("claudeVersions") or [])
    if row["date"] is None and met:
        row["date"] = date_of((met.get("prs") or {}).get("last_merged"))
    return row


def latest_by_case(store):
    """Per case, the saved runs of its latest `ref_sha` (the one with the newest `result.json`) and their newest date."""
    by_case = {}
    for run_dir, result in grade.saved(store) if store else []:
        if isinstance(result, str) or not result.get("case"):
            continue
        stamp = (run_dir / "result.json").stat().st_mtime
        by_case.setdefault(result["case"], []).append((stamp, result))
    out = {}
    for case, items in sorted(by_case.items()):
        newest = max(items, key=lambda i: i[0])
        sha = newest[1].get("ref_sha")
        kept = [i for i in items if i[1].get("ref_sha") == sha]
        out[case] = ([r for _, r in kept], datetime.fromtimestamp(newest[0], timezone.utc).strftime("%Y-%m-%d"))
    return out


def fmt(value, template="{}"):
    return UNKNOWN if value is None else template.format(value)


def names(values):
    return ", ".join(sorted(values)) if values else UNKNOWN


def table(columns, rows):
    lines = ["| " + " | ".join(columns) + " |", "|" + "|".join("---" for _ in columns) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return lines


def render(runs, evals, have_store):
    lines = [FIRST_LINE, "", "# Results", "",
             "Aggregates only. Costs are repriced at the table dated in each row. Human round trips are the dated "
             "quotes recorded after approval; `unknown` means the input was missing.", "", "## Build-epic runs", ""]
    if runs:
        lines += table(RUN_COLUMNS, [
            [r["run"], fmt(r["date"]), names(r["models"]), names(r["versions"]), fmt(r["cost_per_task"], "${:.2f}"),
             fmt(r["basis"]), fmt(r["pricing_as_of"]), fmt(r["round_trips"]), fmt(r["prs_merged_by_agent"])]
            for r in runs])
    else:
        lines.append(UNKNOWN)
    lines += ["", "## Eval pass rates", ""]
    if evals:
        summary = verdict.summarise([r for rs, _ in evals.values() for r in rs])
        rows = []
        for case, (rs, date) in evals.items():
            s = summary[case]
            rate = None if s["pass_rate"] is None else f"{s['pass_rate'] * 100:.0f}% ({s['passed']}/{s['judged']})"
            rows.append([case, date, names({r["model"] for r in rs if r.get("model")}),
                         names({r["tool_version"] for r in rs if r.get("tool_version")}), fmt(rate), str(s["judged"])])
        lines += table(EVAL_COLUMNS, rows)
    else:
        lines.append(UNKNOWN if not have_store else UNKNOWN + " (no saved runs)")
    return "\n".join(lines) + "\n"


class Collect(argparse.Action):
    """`--run` opens a run; `--metrics` and `--telemetry` fill the last one opened."""

    def __call__(self, parser, ns, value, option_string=None):
        runs = ns.runs
        if self.dest == "run":
            runs.append({"run": value, "metrics": None, "telemetry": None})
        elif not runs:
            parser.error(f"{option_string} needs a --run before it")
        else:
            runs[-1][self.dest] = value


def main(argv=None):
    parser = argparse.ArgumentParser(description="Write the results page, as Markdown.")
    parser.add_argument("--run", action=Collect, type=Path, help="a build-epic run folder")
    parser.add_argument("--metrics", action=Collect, type=Path, help="the run's metrics.json")
    parser.add_argument("--telemetry", action=Collect, type=Path, help="the run's saved telemetry session")
    parser.add_argument("--evals-results", type=Path, help="the eval results store (read only)")
    parser.add_argument("--pricing", type=Path, default=REPO / "skills" / "telemetry" / "pricing.json")
    parser.add_argument("--out", required=True, type=Path)
    parser.set_defaults(runs=[])
    args = parser.parse_args(argv)
    if not args.runs and not args.evals_results:
        parser.error("nothing to measure: give --run or --evals-results")
    prices = compare_runs.Prices(load(args.pricing))
    runs = [one_run(i, r["run"], r["metrics"], r["telemetry"], prices) for i, r in enumerate(args.runs, 1)]
    page = render(runs, latest_by_case(args.evals_results), args.evals_results is not None)
    args.out.write_text(page, encoding="utf-8")


if __name__ == "__main__":
    main()
