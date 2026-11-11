#!/usr/bin/env python3
"""Run eval cases against dotagents at one git ref, each case N times, saving one result per run.

  python3 -I evals/run.py --tool claude --ref <ref> [--runs N] [--model <id>] [--budget-usd 10] [--results <dir>]
                          [--fresh] [--remote] [--evals <dir>] (--all | <skill>/<case>...)

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
judges this invocation made reaches `--budget-usd`, no new run or judge starts (a cost the tool reported as 0 counts as $0, an unknown cost as the case's
cost cap for a run, the judge's `$0.50` cap for a judge); the saved results stay and the command names the runs it
skipped. `--evals` points at
another cases folder (default: this repo's `evals/`). Prints one line per run; exits 0, 1 when the budget skipped
runs, 2 on a usage error (no run made), 3 when a run failed on authentication (it is saved with `error: auth` and
no judge, nothing else starts, and the command names the env vars to renew). `run_cases()` is the same for `compare.py`.

`--remote` runs each case through the backend `remote-agent` is set up for (`EVALS_REMOTE_AGENT`, else this checkout's
`scripts/remote-agent.sh`), with the install at the ref: `remote-agent ready` runs once first, from a folder outside
any repo (its stdout is the backend, a failure exits 2), then each run is `remote-agent --home <run home> --model
<model> --name <case>-run-<n> [--attach <input>] -` under capped, the prompt on stdin, the run's `work/` the worktree.
The patch it returns is applied to `work/`; `work/.eval-scratch/` (what `{scratch}` is remotely) moves to `scratch/`
before the checks, and `work/.git/remote-agent/<run>/transcript.jsonl` is the run's transcript. The result says
`remote: true`, the `backend` and `unenforced: ["turns", "cost_usd"]` (caps checked after the run); the run is stored
under `<tool>@<backend>`, never reused for a local one.
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
REMOTE_AGENT = REPO / "scripts" / "remote-agent.sh"
UNENFORCED = ["turns", "cost_usd"]
TOKEN_KEY = "CLAUDE_CODE_OAUTH_TOKEN"
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


def remote_needs(case):
    """What keeps `case` from running remotely, or None: the sandbox has no run home, so a prompt naming `{home}` or a
    check naming `EVAL_HOME` cannot be served. `{scratch}` / `EVAL_SCRATCH` run (scratch lives in the worktree)."""
    if "{home}" in (case["dir"] / case["prompt"]).read_text():
        return "{home} in its prompt"
    checks_dir = Path(case["dir"]) / "checks"
    for path in sorted(checks_dir.rglob("*")) if checks_dir.is_dir() else []:
        if path.is_file() and b"EVAL_HOME" in path.read_bytes():
            return f"EVAL_HOME in checks/{path.relative_to(checks_dir)}"
    return None


def _remote_agent():
    return os.environ.get("EVALS_REMOTE_AGENT") or str(REMOTE_AGENT)


def _remote_ready():
    """The backend `remote-agent ready` prints, run once from a temp folder outside any repo. Raises RunError quoting
    its line when it exits non-zero."""
    with tempfile.TemporaryDirectory(prefix="evals-ready-") as tmp:
        try:
            out = subprocess.run([_remote_agent(), "ready"], cwd=tmp, stdin=subprocess.DEVNULL, capture_output=True,
                                 text=True)
        except OSError as err:
            raise RunError(f"cannot run remote-agent ({_remote_agent()}): {err}") from None
    text = out.stdout.strip() or out.stderr.strip()
    if out.returncode != 0:
        lines = text.splitlines()
        raise RunError(f"remote-agent is not ready: {lines[0] if lines else f'exit {out.returncode}'}"
                       " (run `remote-agent setup`)")
    backend = out.stdout.strip().splitlines()[0].strip() if out.stdout.strip() else ""
    if not results.BACKEND.fullmatch(backend):
        raise RunError(f"remote-agent ready printed no usable backend: {text[:200]!r}")
    return backend


def check_remote(tool, loaded, out):
    """The refusals of a remote run, made before its first run: a tool other than `claude`, a fixture holding
    `.eval-scratch`, no case that can run remotely, a `remote-agent` that is not ready. Returns the runnable cases
    (the others are named on `out`) and the backend. Raises RunError."""
    if tool != "claude":
        raise RunError("--remote needs --tool claude: the remote sandbox runs Claude Code only")
    held = [case_name(c) for c in loaded if (c["fixture"] / home.SCRATCH_DIR).exists()
            or (c["fixture"] / home.SCRATCH_DIR).is_symlink()]
    if held:
        raise RunError(f"{', '.join(held)}: the fixture holds {home.SCRATCH_DIR}, where a remote run keeps "
                       "its scratch")
    runnable = []
    for c in loaded:
        needs = remote_needs(c)
        if needs:
            print(f"{case_name(c)}: cannot run remotely ({needs})", file=out, flush=True)
        else:
            runnable.append(c)
    if not runnable:
        raise RunError("no case can run remotely")
    return runnable, _remote_ready()


def _remote_values():
    """The remote token, from `remote.env` of the runner's config folder and the caller's env, as bytes."""
    config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "dotagents" / "remote.env"
    found = set()
    try:
        for text in config.read_text().splitlines():
            key, _, value = text.removeprefix("export ").partition("=")
            if key.strip() == TOKEN_KEY and value.strip().strip("'\""):
                found.add(value.strip().strip("'\"").encode())
    except OSError:
        pass
    if os.environ.get(TOKEN_KEY):
        found.add(os.environ[TOKEN_KEY].encode())
    return found


def _remote_transcript(work):
    """The transcript the newest remote-agent run left in `work`'s git dir, then that folder removed (it also holds the
    sandbox's session and patch); empty bytes when there is none."""
    kept = Path(work) / ".git" / "remote-agent"
    found = sorted(kept.glob("*/transcript.jsonl"), key=lambda p: p.stat().st_mtime)
    data = found[-1].read_bytes() if found else b""
    shutil.rmtree(kept, ignore_errors=True)
    return data


def run_once(case, n, adapter, tool, model, co, dest, pricing, extract, backend=None):
    """Make run `n` of `case` and save it in `dest`; returns the saved result. A `backend` makes it a remote run."""
    remote = backend is not None
    base = {"tool": tool, "model": model, "ref": co.ref, "ref_sha": co.sha, "case": case_name(case),
            "case_hash": case["hash"], "run": n, "checks_hash": checks.checks_hash(case["dir"]), "passed": False,
            "cost_basis": "unknown", "pricing_as_of": (pricing or {}).get("asOf"), "transcript": adapters.TRANSCRIPT,
            "remote": remote, "backend": backend, "unenforced": UNENFORCED if remote else None}
    extra = _remote_values() if remote else set()
    values = sorted({os.environ[v].encode() for v in adapter.AUTH_VARS if os.environ.get(v)} | extra, key=len,
                    reverse=True)
    try:
        run = home.prepare(case, co.path, adapter.INSTALL_WORD, prepare_home=adapter.prepare_home, remote=remote)
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
        patch_failed = False
        capped_argv = [sys.executable, "-I", str(_capped(co.path)), "--secs", str(case["caps"]["secs"]), "--"]
        if remote:
            ra = [_remote_agent(), "--home", str(run.home), "--model", model, "--name", f"{case['case']}-run-{n}"]
            if case.get("input"):
                ra += ["--attach", str(run.input)]
            # capped gives its command no stdin, so a shell inside it feeds remote-agent the prompt from a file.
            (run.root / "prompt.txt").write_text(prompt)
            feed = ["sh", "-c", 'f="$1"; shift; exec "$@" <"$f"', "sh", str(run.root / "prompt.txt")]
            with open(stderr, "wb") as err:
                proc = subprocess.run(capped_argv + feed + ra + ["-", str(run.work)], cwd=str(run.work),
                                      stdin=subprocess.DEVNULL, env=dict(os.environ), stdout=subprocess.DEVNULL,
                                      stderr=err)
            transcript.write_bytes(_remote_transcript(run.work))
            run.collect_scratch()
            patch_failed = proc.returncode == 4
        else:
            with open(transcript, "wb") as out, open(stderr, "wb") as err:
                subprocess.run(capped_argv + argv, cwd=str(run.work), env=run.env(adapter.AUTH_VARS),
                               stdin=subprocess.DEVNULL, stdout=out, stderr=err)
        wall = round(time.monotonic() - start, 3)
        parsed = adapter.parse(run.root, run.home, run.work)
        timed_out = TIME_KILL in stderr.read_bytes().decode("utf-8", "replace").splitlines()
        found = checks.run(case["checks"], {**run.paths, "transcript": transcript, "gh_calls": run.gh_calls,
                                            "checkout": co.path})
        tokens = parsed.get("tokens") or None
        zero = bool(parsed.get("zero_cost"))
        cost = 0.0 if zero else extract.cost(tokens, pricing) if tokens else None
        capped = "time" if timed_out else parsed.get("capped") or over_cap(case["caps"], parsed.get("turns"), cost)
        error = "patch" if patch_failed else None if capped else parsed.get("error")
        result = {**base, "tool_version": parsed.get("tool_version"), "checks": found,
                  "passed": all(found.values()) and not capped and not error, "tokens": tokens, "cost_usd": cost,
                  "cost_basis": "reported" if zero else "pricing" if cost is not None else "unknown", "turns": parsed.get("turns"),
                  "duration_s": parsed.get("duration_s") if parsed.get("duration_s") is not None else wall,
                  "capped": capped, "subagents": subagents(co.path, case["skill"], parsed.get("spawned_agents")),
                  "error": error}
        _clear(dest)
        run.keep(dest, adapter.AUTH_VARS, extra)
        _write_redacted(dest / adapters.TRANSCRIPT, transcript.read_bytes(), values)
        _write_redacted(dest / STDERR, stderr.read_bytes(), values)
        results.write(dest, result)
    if case.get("rubric") and result["error"] not in ("auth", "patch"):
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
    text += "  (saved)" if reused else ""
    return text + (f"  remote {res['backend']}" if res.get("remote") else "")


def run_cases(tool, ref, case_dirs, runs=None, model=None, budget_usd=10.0, results_root=None, fresh=False,
              repo=REPO, out=None, only_run=None, remote=False):
    """Run each case of `case_dirs` `runs` times (default its own `runs`) on `tool` with dotagents at `ref` of `repo`;
    with `only_run`, only run number `only_run` of each case that has that many (`compare.py` alternates the refs so).
    Returns `{ref, sha, runs: [{case, run, dir, result, reused}], skipped: [(case, run)], spent_usd, stopped}`
    (`stopped` is `"auth"` when a run failed on authentication and the rest were not started, else None). Raises
    RunError, before any run, on an unknown tool, a malformed case, a prompt the tool would read as an option,
    install parts pulling in `global`, a missing or alias `--model`, and for `remote` (each run through
    `remote-agent`, stored under the backend `remote-agent ready` names) a tool other than `claude`, a fixture
    holding `.eval-scratch` or a `remote-agent` that is not ready. A saved run is replaced only once its new
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
    backend = None
    if remote:
        loaded, backend = check_remote(tool, loaded, out)
    root = results.store_root(results_root)
    extract = _extract()
    pricing = extract.load_pricing(PRICING)
    done, skipped, spent, stopped = [], [], 0.0, None
    total = sum(runs or case["runs"] for case in loaded)
    co = home.checkout(repo, ref)
    try:
        for case in loaded:
            _check(case, adapter, model or case["model"], co.path)
        for case in loaded:
            if stopped:
                break
            use_model = model or case["model"]
            numbers = range(1, (runs or case["runs"]) + 1)
            for n in [only_run] if only_run in numbers else [] if only_run else numbers:
                dest = results.run_dir(root, tool, co.sha, use_model, case_name(case), case["hash"], n,
                                       backend=backend)
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
                    res, reused = run_once(case, n, adapter, tool, use_model, co, dest, pricing, extract, backend), False
                    if res["error"] != "install":
                        spent += res["cost_usd"] if res["cost_usd"] is not None else case["caps"]["cost_usd"]
                        spent += judge.spent(res["rubric"])
                done.append({"case": case_name(case), "run": n, "dir": dest, "result": res, "reused": reused})
                print(line(res, reused), file=out, flush=True)
                if res["error"] == "auth":
                    stopped = "auth"
                    left = total - len(done) - len(skipped)
                    renew = ("run `remote-agent setup` (the token in remote.env)" if backend
                             else f"renew {', '.join(adapter.AUTH_VARS)} ({adapter.RENEW})")
                    print(f"auth failed for {tool}: {renew}; stopped before {left} more run(s)", file=out,
                          flush=True)
                    break
    finally:
        co.close()
    if skipped:
        names = ", ".join(f"{c} run {n}" for c, n in skipped)
        print(f"budget of ${budget_usd:g} reached (${spent:.4f} spent): skipped {names}", file=out, flush=True)
    return {"ref": ref, "sha": co.sha, "runs": done, "skipped": skipped, "spent_usd": spent,
            "stopped": stopped}


def main(argv):
    parser = argparse.ArgumentParser(prog="run.py", description="Run eval cases against dotagents at a git ref.")
    parser.add_argument("--tool", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--runs", type=int)
    parser.add_argument("--model")
    parser.add_argument("--budget-usd", type=float, default=10.0)
    parser.add_argument("--results")
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--remote", action="store_true")
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
                        model=args.model, budget_usd=args.budget_usd, results_root=args.results, fresh=args.fresh,
                        remote=args.remote)
    except (RunError, RuntimeError) as err:
        print(f"run.py: {err}", file=sys.stderr)
        return 2
    return 3 if got["stopped"] else 1 if got["skipped"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
