#!/usr/bin/env python3
"""Re-grade saved eval runs with the current checks and rubric, without running any case again (spec behaviour 14).

  python3 -I evals/grade.py [--results <dir>] [--rejudge] [--ref <ref>] [--budget-usd 10] [--evals <dir>]
                            (--all | <skill>/<case>...)

Each saved run of the named cases (every case with `--all`; only those at `--ref`'s commit with `--ref`), whatever
its tool, model or case hash, is checked again by the case's current `checks/` and `no-github`
(`_runner/checks.py`), on the run's kept `work/`, `home/`, `scratch/`, `gh-calls` and transcript, with
`EVAL_CHECKOUT` a fresh extract of the run's `ref_sha` and `EVAL_INPUT` the case's current `input/`. That rewrites
`checks`, `checks_hash` and `passed` in its `result.json`. The run is judged again (`_runner/judge.py`, through its
own tool's adapter) only when the case's rubric or `judge_model` differs from the saved one, or with `--rejudge`; a
case with no rubric now gets `rubric: null`. The judge is the only tool ever called. Every other field is kept.
Once the judges' cost reaches `--budget-usd` (a judge of unknown cost counting as its `$0.50` cap), no run needing the
judge is graded: it is left as it is and named; re-running checks costs nothing.

A run saved with `error: install` or `error: auth` has no state to check and is left as it is; a saved run whose case folder is gone,
or whose `result.json` does not read as a result, is named and skipped. Only a `result.json` at the store's run-folder
depth (`results.run_dir`) is a saved run, never one inside a run's kept state. `--evals` points at another cases folder (default: this repo's `evals/`). Prints one line per
run; exits 0, 1 when a run could not be graded (its commit missing here, or the budget reached), 2 on a usage error
or a named case with no saved run (nothing graded).
"""
import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE / "_runner"))

import cases  # noqa: E402
import home  # noqa: E402
import judge  # noqa: E402
import results  # noqa: E402
from grading import needs_judging, regrade  # noqa: E402


class GradeError(ValueError):
    """Nothing can be graded: a bad ref, an unknown case or a named case with no saved run."""


RUN_GLOB = f"*/*/*/*/*/*/run-*/{results.RESULT}"  # <tool>/<sha>/<model>/<skill>/<case>/<case_hash>/run-<n>, as `results.run_dir`


def saved(root):
    """`(run_dir, result)` of every finished run in the store, in path order: only a `result.json` at the run-folder
    depth counts, never one inside a run's kept state. A run whose `result.json` does not read as a result gets the
    reason as a string in place of its result."""
    root = Path(root)
    if not root.is_dir():
        return []
    found = []
    for p in sorted(root.glob(RUN_GLOB)):
        if not p.is_file() or not re.fullmatch(r"run-[1-9][0-9]*", p.parent.name):
            continue
        try:
            raw = json.loads(p.read_text())
            if not isinstance(raw, dict):
                raise ValueError("not a JSON object")
            res = results.read(p.parent)
        except (OSError, UnicodeDecodeError, ValueError) as err:
            res = f"{p} does not read as a result: {err}"
        found.append((p.parent, res))
    return found


def _store_case(root, run_dir):
    """`<skill>/<case>` from a run folder's place in the store."""
    return "/".join(Path(run_dir).relative_to(root).parts[3:5])


def _case_dir(evals_dir, name):
    parts = str(name).strip("/").split("/")
    if len(parts) != 2 or parts[0].startswith(("_", ".")) or ".." in parts:
        return None
    path = Path(evals_dir).joinpath(*parts)
    return path if (path / "case.json").is_file() else None


def _sha(repo, ref):
    try:
        return home._git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", what=f"no commit '{ref}'")
    except RuntimeError as err:
        raise GradeError(str(err)) from None


def line(res, judged):
    rubric = "judged" if judged else ("kept" if res["rubric"] else "none")
    if res["rubric"] and "error" in res["rubric"]:
        rubric += " (error)"
    return (f"{res['case']}  run {res['run']}  {res['tool']} {res['model']} {(res['ref_sha'] or '?')[:12]}  "
            f"{'pass' if res['passed'] else 'fail'}  rubric {rubric}")


def grade(names, every=False, results_root=None, rejudge=False, ref=None, repo=REPO, evals=HERE, out=None,
          budget_usd=10.0):
    """Re-grade the saved runs of `names` (`<skill>/<case>`), or every saved run with `every`, as the module says.
    Returns `{graded: [(run_dir, result)], skipped: [(run_dir, why)], failed: [(run_dir, why)], over_budget:
    [run_dir], spent_usd}`. Raises GradeError before grading anything on a bad `ref` or a named case with no folder or
    no saved run."""
    out = out or sys.stdout
    sha = _sha(repo, ref) if ref else None
    root = results.store_root(results_root)
    found = saved(root)
    unread = [(d, r) for d, r in found if isinstance(r, str)]
    runs = [(d, r) for d, r in found if not isinstance(r, str) and (sha is None or r["ref_sha"] == sha)]
    if not every:
        names = [str(n).strip("/") for n in names]
        unread = [(d, r) for d, r in unread if _store_case(root, d) in names]
        unknown = [n for n in names if _case_dir(evals, n) is None]
        if unknown:
            raise GradeError(f"no case {', '.join(unknown)} under {evals}")
        missing = [n for n in names if not any(r["case"] == n for _, r in runs)
                   and not any(_store_case(root, d) == n for d, _ in unread)]
        if missing:
            raise GradeError(f"no saved run of {', '.join(missing)}" + (f" at {ref}" if ref else ""))
        runs = [(d, r) for d, r in runs if r["case"] in names]
    loaded, checkouts = {}, {}
    graded, skipped, failed, over_budget, spent = [], list(unread), [], [], 0.0
    for run_dir, why in unread:
        print(f"{_store_case(root, run_dir)}  {run_dir.name}  skipped: {why}", file=out, flush=True)
    try:
        for run_dir, res in runs:
            name = res["case"]
            if name not in loaded:
                case_dir = _case_dir(evals, name)
                try:
                    loaded[name] = cases.load(case_dir) if case_dir else None
                except cases.CaseError as err:
                    loaded[name] = str(err)
            case = loaded[name]
            why = None
            if case is None:
                why = f"no case {name} under {evals}"
            elif isinstance(case, str):
                why = case
            elif res["error"] in ("install", "auth"):
                why = f"error {res['error']}: no state kept"
            if why:
                skipped.append((run_dir, why))
                print(f"{name}  run {res['run']}  skipped: {why}", file=out, flush=True)
                continue
            if res["ref_sha"] not in checkouts:
                try:
                    checkouts[res["ref_sha"]] = home.checkout(repo, res["ref_sha"])
                except RuntimeError as err:
                    checkouts[res["ref_sha"]] = err
            co = checkouts[res["ref_sha"]]
            if isinstance(co, Exception):
                failed.append((run_dir, str(co)))
                print(f"{name}  run {res['run']}  not graded: {co}", file=out, flush=True)
                continue
            if needs_judging(res, case, rejudge) and spent >= budget_usd:
                over_budget.append(run_dir)
                print(f"{name}  run {res['run']}  not graded: budget of ${budget_usd:g} reached (${spent:.4f} spent)",
                      file=out, flush=True)
                continue
            new, judged = regrade(run_dir, res, case, co.path, rejudge)
            if judged:
                spent += judge.spent(new["rubric"])
            graded.append((run_dir, new))
            print(line(new, judged), file=out, flush=True)
    finally:
        for co in checkouts.values():
            if not isinstance(co, Exception):
                co.close()
    return {"graded": graded, "skipped": skipped, "failed": failed, "over_budget": over_budget, "spent_usd": spent}


def main(argv):
    parser = argparse.ArgumentParser(prog="grade.py", description="Re-grade saved eval runs with the current checks "
                                     "and rubric, running no case again.")
    parser.add_argument("--results")
    parser.add_argument("--rejudge", action="store_true")
    parser.add_argument("--ref")
    parser.add_argument("--budget-usd", type=float, default=10.0)
    parser.add_argument("--evals", default=str(HERE))
    parser.add_argument("--all", action="store_true")
    parser.add_argument("cases", nargs="*")
    args = parser.parse_args(argv)
    if args.all == bool(args.cases):
        parser.error("give --all or one or more <skill>/<case>, not both")
    if args.budget_usd <= 0:
        parser.error("--budget-usd must be over 0")
    try:
        got = grade(args.cases, every=args.all, results_root=args.results, rejudge=args.rejudge, ref=args.ref,
                    evals=Path(args.evals), budget_usd=args.budget_usd)
    except GradeError as err:
        print(f"grade.py: {err}", file=sys.stderr)
        return 2
    return 1 if got["failed"] or got["over_budget"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
