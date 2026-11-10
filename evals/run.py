#!/usr/bin/env python3
"""Run eval cases against dotagents at one git ref, each case N times, saving one result per run.

  python3 -I evals/run.py --tool claude --ref <ref> [--runs N] [--model <id>] [--budget-usd 10] [--results <dir>]
                          [--fresh] [--evals <dir>] (--all | <skill>/<case>...)

Each run: dotagents extracted at the ref, the tool adapter's `prepare_home`, the ref's `install.sh <tool word>` of the
case's parts into a throwaway home, the fixture as a fresh git repo, the tool's command under the checkout's
`scripts/capped --secs <caps.secs>` with its stdout written to `transcript.jsonl` as it comes, then the case's checks
and `no-github` (`_runner/checks.py`). The result is saved under the store (`_runner/results.py`) with the run's
kept state, every auth value redacted. A run hitting a cap is saved with which cap (`time` from capped's
`killed: timeout` line, `turns` and `cost` from the adapter, else from the run's turns and cost over the case's caps,
for a tool with no such flag); an install failure is saved as `error: install`.
A case with a rubric then has the saved run judged through the same adapter (`_runner/judge.py`), its `rubric`
saved with the result, the judge's cost in it.

Saved runs of the same tool, ref commit, model and case files that change a run (`results.case_hash`: not `checks/`,
the rubric, `judge_model` or `runs`) are reused unless `--fresh` or they errored. A reused run whose checks, rubric or
`judge_model` changed since is re-graded first, as `grade.py` does (`_runner/grading.py`): checks re-run on its kept
state, the judge only when the rubric or `judge_model` changed, the case's tool never. Once the cost of the runs and
judges this invocation made reaches `--budget-usd`, no new run or judge starts (an unknown cost counts as the case's
cost cap for a run, the judge's `$0.50` cap for a judge); the saved results stay and the command names the runs it
skipped. `--evals` points at
another cases folder (default: this repo's `evals/`). Prints one line per run; exits 0, 1 when the budget skipped
runs, 2 on a usage error (no run made). `run_cases()` is the same for `compare.py`.
"""
import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE / "_runner"))

import adapters  # noqa: E402
import cases  # noqa: E402
import checks  # noqa: E402
import grading  # noqa: E402
import home  # noqa: E402
import judge  # noqa: E402
import results  # noqa: E402

PRICING = REPO / "skills" / "telemetry" / "pricing.json"
EXTRACT = REPO / "adapters" / "claude" / "telemetry" / "extract.py"
STDERR = "stderr.log"
TIME_KILL = "capped: killed: timeout"


class RunError(ValueError):
    """The invocation cannot start: nothing was run."""


def _extract():
    spec = importlib.util.spec_from_file_location("evals_extract", EXTRACT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def case_name(case):
    return f"{case['skill']}/{case['case']}"


def find_cases(evals_dir, names, every=False):
    """The case folders `names` (`<skill>/<case>`) name under `evals_dir`, or every case there with `every`."""
    evals_dir = Path(evals_dir)
    if every:
        return [c for s in sorted(evals_dir.iterdir()) if s.is_dir() and not s.name.startswith(("_", "."))
                for c in sorted(s.iterdir()) if (c / "case.json").is_file()]
    found, missing = [], []
    for name in names:
        parts = name.strip("/").split("/")
        path = evals_dir.joinpath(*parts)
        if len(parts) != 2 or parts[0].startswith(("_", ".")) or ".." in parts or not path.is_dir():
            missing.append(name)
        else:
            found.append(path)
    if missing:
        raise RunError(f"no case {', '.join(missing)} under {evals_dir}")
    return found


def subagents(checkout_dir, skill, spawned):
    """`used` when the run started a subagent, `fallback` when the skill (at the ref) lists `agents` and none
    started, else `none`; None when the adapter cannot tell."""
    if spawned is None:
        return None
    if spawned:
        return "used"
    try:
        agents = json.loads((Path(checkout_dir) / "skills" / skill / "needs.json").read_text()).get("agents")
    except (OSError, ValueError, AttributeError):
        agents = None
    return "fallback" if agents else "none"


def _write_redacted(to, data, values):
    for value in values:
        data = data.replace(value, home.REDACTED)
    Path(to).write_bytes(data)


def _clear(dest):
    """An empty `dest`, a saved run there removed only now that the new one has its result."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)


def _check(case, adapter, model, checkout_dir):
    """Raise RunError when `case` could not start: a prompt the tool's command refuses or install parts the ref's
    closure turns down. A failing closure is left to the run, saved as its install error."""
    dummy = {name: Path("/") / name for name in home.PLACEHOLDERS}
    prompt = home.fill_prompt((case["dir"] / case["prompt"]).read_text(), dummy)
    try:
        adapter.command(prompt, model, case["caps"], dummy["work"])
        with tempfile.TemporaryDirectory(prefix="evals-check-") as tmp:
            home.check_parts(checkout_dir, tmp, adapter.INSTALL_WORD, case["install"])
    except home.InstallError:
        pass
    except ValueError as err:
        raise RunError(f"{case_name(case)}: {err}") from None


def _capped(checkout_dir):
    own = Path(checkout_dir) / "scripts" / "capped"
    return own if own.is_file() else REPO / "scripts" / "capped"


def over_cap(caps, turns, cost):
    """`turns` or `cost` when the run went over that cap of `caps`, checked after the run for a tool without the flag;
    None within both or when the value is unknown."""
    if turns is not None and turns > caps["turns"]:
        return "turns"
    if cost is not None and cost > caps["cost_usd"]:
        return "cost"
    return None


def run_once(case, n, adapter, tool, model, co, dest, pricing, extract):
    """Make run `n` of `case` and save it in `dest`; returns the saved result."""
    base = {"tool": tool, "model": model, "ref": co.ref, "ref_sha": co.sha, "case": case_name(case),
            "case_hash": case["hash"], "run": n, "checks_hash": checks.checks_hash(case["dir"]), "passed": False,
            "cost_basis": "unknown", "pricing_as_of": (pricing or {}).get("asOf"), "transcript": adapters.TRANSCRIPT}
    values = sorted({os.environ[v].encode() for v in adapter.AUTH_VARS if os.environ.get(v)}, key=len, reverse=True)
    try:
        run = home.prepare(case, co.path, adapter.INSTALL_WORD, prepare_home=adapter.prepare_home)
    except home.InstallError as err:
        _clear(dest)
        _write_redacted(dest / STDERR, (str(err) + "\n").encode(), values)
        (dest / adapters.TRANSCRIPT).write_bytes(b"")
        result = {**base, "error": "install"}
        results.write(dest, result)
        return results.read(dest)
    with run:
        prompt = run.prompt((case["dir"] / case["prompt"]).read_text())
        argv = adapter.command(prompt, model, case["caps"], run.work)
        transcript, stderr = run.root / adapters.TRANSCRIPT, run.root / STDERR
        start = time.monotonic()
        with open(transcript, "wb") as out, open(stderr, "wb") as err:
            subprocess.run([sys.executable, "-I", str(_capped(co.path)), "--secs", str(case["caps"]["secs"]), "--",
                            *argv], cwd=str(run.work), env=run.env(adapter.AUTH_VARS), stdin=subprocess.DEVNULL,
                           stdout=out, stderr=err)
        wall = round(time.monotonic() - start, 3)
        parsed = adapter.parse(run.root, run.home, run.work)
        timed_out = TIME_KILL in stderr.read_bytes().decode("utf-8", "replace").splitlines()
        found = checks.run(case["checks"], {**run.paths, "transcript": transcript, "gh_calls": run.gh_calls,
                                            "checkout": co.path})
        tokens = parsed.get("tokens") or None
        cost = extract.cost(tokens, pricing) if tokens else None
        capped = "time" if timed_out else parsed.get("capped") or over_cap(case["caps"], parsed.get("turns"), cost)
        error = None if capped else parsed.get("error")
        result = {**base, "tool_version": parsed.get("tool_version"), "checks": found,
                  "passed": all(found.values()) and not capped and not error, "tokens": tokens, "cost_usd": cost,
                  "cost_basis": "pricing" if cost is not None else "unknown", "turns": parsed.get("turns"),
                  "duration_s": parsed.get("duration_s") if parsed.get("duration_s") is not None else wall,
                  "capped": capped, "subagents": subagents(co.path, case["skill"], parsed.get("spawned_agents")),
                  "error": error}
        _clear(dest)
        run.keep(dest, adapter.AUTH_VARS)
        _write_redacted(dest / adapters.TRANSCRIPT, transcript.read_bytes(), values)
        _write_redacted(dest / STDERR, stderr.read_bytes(), values)
        results.write(dest, result)
    if case.get("rubric"):
        results.write(dest, {**results.read(dest), "rubric": judge.judge(dest, case, co.path, tool)})
    return results.read(dest)


def line(res, reused=False):
    """`<case> run <n> pass|fail <cost> turns <n> <secs>s cap <cap>`, plus the error and `(saved)` when any."""
    cost = "unknown" if res["cost_usd"] is None else f"${res['cost_usd']:.4f}"
    turns = "?" if res["turns"] is None else res["turns"]
    secs = "?" if res["duration_s"] is None else f"{res['duration_s']:.1f}"
    text = (f"{res['case']}  run {res['run']}  {'pass' if res['passed'] else 'fail'}  {cost}  turns {turns}  "
            f"{secs}s  cap {res['capped'] or '-'}")
    if res["error"]:
        text += f"  error {res['error']}"
    return text + ("  (saved)" if reused else "")


def run_cases(tool, ref, case_dirs, runs=None, model=None, budget_usd=10.0, results_root=None, fresh=False,
              repo=REPO, out=None):
    """Run each case of `case_dirs` `runs` times (default its own `runs`) on `tool` with dotagents at `ref` of `repo`.
    Returns `{ref, sha, runs: [{case, run, dir, result, reused}], skipped: [(case, run)], spent_usd}`. Raises
    RunError, before any run, on an unknown tool, a malformed case, a prompt the tool would read as an option,
    install parts pulling in `global` or a missing or alias `--model`. A saved run is replaced only once its new
    run has a result."""
    out = out or sys.stdout
    try:
        adapter = adapters.load(tool)
    except ValueError as err:
        raise RunError(str(err)) from None
    if model is not None:
        problem = cases.alias_problem("--model", model, cases.priced_models())
        if problem:
            raise RunError(problem)
    elif not adapter.USES_CASE_MODEL:
        raise RunError(f"the {tool} adapter does not run the case's model: give --model <full model ID>")
    loaded = []
    for d in case_dirs:
        try:
            case = cases.load(d)
        except cases.CaseError as err:
            raise RunError(str(err)) from None
        case["hash"] = results.case_hash(case["dir"])
        loaded.append(case)
    root = results.store_root(results_root)
    extract = _extract()
    pricing = extract.load_pricing(PRICING)
    done, skipped, spent = [], [], 0.0
    co = home.checkout(repo, ref)
    try:
        for case in loaded:
            _check(case, adapter, model or case["model"], co.path)
        for case in loaded:
            use_model = model or case["model"]
            for n in range(1, (runs or case["runs"]) + 1):
                dest = results.run_dir(root, tool, co.sha, use_model, case_name(case), case["hash"], n)
                saved = None if fresh or not (dest / results.RESULT).is_file() else results.read(dest)
                if saved is not None and saved["error"] is None:
                    res, reused = saved, True
                    if grading.stale(saved, case):
                        if grading.needs_judging(saved, case) and spent >= budget_usd:
                            skipped.append((case_name(case), n))
                            continue
                        res, judged = grading.regrade(dest, saved, case, co.path)
                        if judged:
                            spent += judge.spent(res["rubric"])
                elif spent >= budget_usd:
                    skipped.append((case_name(case), n))
                    continue
                else:
                    res, reused = run_once(case, n, adapter, tool, use_model, co, dest, pricing, extract), False
                    if res["error"] != "install":
                        spent += res["cost_usd"] if res["cost_usd"] is not None else case["caps"]["cost_usd"]
                        spent += judge.spent(res["rubric"])
                done.append({"case": case_name(case), "run": n, "dir": dest, "result": res, "reused": reused})
                print(line(res, reused), file=out, flush=True)
    finally:
        co.close()
    if skipped:
        names = ", ".join(f"{c} run {n}" for c, n in skipped)
        print(f"budget of ${budget_usd:g} reached (${spent:.4f} spent): skipped {names}", file=out, flush=True)
    return {"ref": ref, "sha": co.sha, "runs": done, "skipped": skipped, "spent_usd": spent}


def main(argv):
    parser = argparse.ArgumentParser(prog="run.py", description="Run eval cases against dotagents at a git ref.")
    parser.add_argument("--tool", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--runs", type=int)
    parser.add_argument("--model")
    parser.add_argument("--budget-usd", type=float, default=10.0)
    parser.add_argument("--results")
    parser.add_argument("--fresh", action="store_true")
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
        got = run_cases(args.tool, args.ref, find_cases(args.evals, args.cases, args.all), runs=args.runs,
                        model=args.model, budget_usd=args.budget_usd, results_root=args.results, fresh=args.fresh)
    except (RunError, RuntimeError) as err:
        print(f"run.py: {err}", file=sys.stderr)
        return 2
    return 1 if got["skipped"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
