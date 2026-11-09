#!/usr/bin/env python3
"""Compare eval cases on two git refs of dotagents and report it in Markdown, and optionally HTML.

  python3 -I evals/compare.py --tool claude --base <ref> --head <ref> [--runs N] [--model <id>]
                              [--base-model <id>] [--head-model <id>] [--budget-usd 10] [--results <dir>]
                              [--out <file.md>] [--html <file.html>] [--evals <dir>] (--all | <skill>/<case>...)

Each ref runs through `run.py`'s `run_cases`, so a saved run of the same tool, ref commit, model and case files is
reused and `main` is not paid for again; a changed case file changes its hash and runs again. `--model` sets both
refs' model, `--base-model` and `--head-model` one ref's (default: each case's own). One `--budget-usd` covers both
refs: the head gets what the base left. Every run, saved or new, is priced again from its tokens with the current
`skills/telemetry/pricing.json` (cost `unknown` without tokens or a price), and the report names that table's date.

The Markdown report (`_runner/render.py`) is printed on stdout and written to `--out`, by default
`<results root>/reports/<tool>-<base sha>-<head sha>.md`; `--html` also writes the HTML page. A report path inside
the repo is refused: reports are never committed. Each run's line and the report paths go to stderr. Exits 0, 1 when
the budget skipped runs (the report still written), 2 on a usage error (no run made).
"""
import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "_runner"))

import cases  # noqa: E402
import render  # noqa: E402
import results  # noqa: E402
import run  # noqa: E402
import verdict  # noqa: E402

SIDES = ("base", "head")


def reprice(result, pricing, extract):
    """`result` with `cost_usd`, `cost_basis` and `pricing_as_of` from its tokens and the price table `pricing`."""
    tokens = result.get("tokens")
    cost = extract.cost(tokens, pricing) if tokens else None
    return {**result, "cost_usd": cost, "cost_basis": "pricing" if cost is not None else "unknown",
            "pricing_as_of": (pricing or {}).get("asOf")}


def _inside(path, folder):
    try:
        Path(path).resolve().relative_to(Path(folder).resolve())
        return True
    except ValueError:
        return False


def _sha(repo, ref):
    out = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
                         capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if out.returncode != 0:
        raise run.RunError(f"no commit {ref!r} in {repo}")
    return out.stdout.strip()


def _write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def compare(tool, base, head, case_dirs, runs=None, model=None, base_model=None, head_model=None, budget_usd=10.0,
            results_root=None, out_md=None, out_html=None, repo=REPO, out=None, report_to=None):
    """Run `case_dirs` on `base` and `head` of `repo` and write the report. Returns `{base, head}` (each
    `run_cases()`'s output, its results repriced), `comparison` (the dict `render.py` takes) and the report paths
    `out_md` and `out_html`. Raises run.RunError before any run on an unknown ref, tool, case or model, or a report
    path inside the repo. Run lines go to `out`, the Markdown report to `report_to` (default `out`)."""
    out = out or sys.stdout
    report_to = report_to or out
    root = results.store_root(results_root)
    for path in (out_md or root, out_html):
        if path is not None and any(_inside(path, r) for r in {REPO, Path(repo)}):
            raise run.RunError(f"{path} is inside the repo: reports are never committed")
    models = {"base": base_model or model, "head": head_model or model}
    for flag, value in (("--base-model", models["base"]), ("--head-model", models["head"])):
        problem = value and cases.alias_problem(flag, value, cases.priced_models())
        if problem:
            raise run.RunError(problem)
    shas = {"base": _sha(repo, base), "head": _sha(repo, head)}
    try:
        loaded = [cases.load(d) for d in case_dirs]
    except cases.CaseError as err:
        raise run.RunError(str(err)) from None

    got, left = {}, budget_usd
    for side, ref in (("base", base), ("head", head)):
        got[side] = run.run_cases(tool, ref, case_dirs, runs=runs, model=models[side], budget_usd=max(left, 0.0),
                                  results_root=results_root, repo=repo, out=out)
        left -= got[side]["spent_usd"]

    extract = run._extract()
    pricing = extract.load_pricing(run.PRICING)
    for side in SIDES:
        for entry in got[side]["runs"]:
            entry["result"] = reprice(entry["result"], pricing, extract)

    used = {side: sorted({models[side]} if models[side] else {c["model"] for c in loaded}) for side in SIDES}
    per_case = sorted({runs or c["runs"] for c in loaded})
    comparison = {"tool": tool, "model": ", ".join(sorted(set(used["base"]) | set(used["head"]))) or None,
                  "runs": per_case[0] if len(per_case) == 1 else ", ".join(map(str, per_case)) or None,
                  "pricing_as_of": (pricing or {}).get("asOf"),
                  "verdict": verdict.verdict(*(verdict.summarise(e["result"] for e in got[side]["runs"])
                                               for side in SIDES))}
    for side, ref in (("base", base), ("head", head)):
        comparison[side] = {"ref": ref, "sha": shas[side]}
        if used["base"] != used["head"]:
            comparison[side]["model"] = ", ".join(used[side])

    md = render.markdown(comparison)
    out_md = _write(out_md or root / "reports" / f"{tool}-{shas['base'][:12]}-{shas['head'][:12]}.md", md)
    print(f"report: {out_md}", file=out, flush=True)
    if out_html:
        out_html = _write(out_html, render.html(comparison))
        print(f"report: {out_html}", file=out, flush=True)
    print(md, end="", file=report_to, flush=True)
    return {**got, "comparison": comparison, "out_md": out_md, "out_html": out_html}


def main(argv):
    parser = argparse.ArgumentParser(prog="compare.py", description="Compare eval cases on two git refs.")
    parser.add_argument("--tool", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--runs", type=int)
    parser.add_argument("--model")
    parser.add_argument("--base-model")
    parser.add_argument("--head-model")
    parser.add_argument("--budget-usd", type=float, default=10.0)
    parser.add_argument("--results")
    parser.add_argument("--out")
    parser.add_argument("--html")
    parser.add_argument("--evals", default=str(HERE))
    parser.add_argument("--all", action="store_true")
    parser.add_argument("cases", nargs="*")
    args = parser.parse_args(argv)
    if args.all == bool(args.cases):
        parser.error("give --all or one or more <skill>/<case>, not both")
    if args.runs is not None and args.runs < 1:
        parser.error("--runs must be at least 1")
    if args.budget_usd <= 0:
        parser.error("--budget-usd must be over 0")
    try:
        got = compare(args.tool, args.base, args.head, run.find_cases(args.evals, args.cases, args.all),
                      runs=args.runs, model=args.model, base_model=args.base_model, head_model=args.head_model,
                      budget_usd=args.budget_usd, results_root=args.results, out_md=args.out, out_html=args.html,
                      out=sys.stderr, report_to=sys.stdout)
    except (run.RunError, RuntimeError) as err:
        print(f"compare.py: {err}", file=sys.stderr)
        return 2
    return 1 if got["base"]["skipped"] or got["head"]["skipped"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
