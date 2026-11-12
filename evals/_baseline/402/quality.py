#!/usr/bin/env python3
"""Scores one build of a sandbox epic (#402 by default): hidden acceptance tests, plus the run's review findings, red
merges and hand fixes.

Usage: python3 -I quality.py <checkout> <ref> [--run <run folder>] [--python <python with pytest>]
                             [--acceptance <folder>]

<checkout> is any clone (bare too) or worktree of the sandbox repo; its working tree is never touched: <ref> is
checked out in a temporary detached worktree, removed at the end. The acceptance tests (`accept_*.py` of
`--acceptance`, default `acceptance/` beside this file, epic #402's) run there with `--python` (default:
`<run>/env/bin/python` when `--run` has one, else this interpreter), which needs pytest. The folder holds its own
`pytest.ini` and `conftest.py`, and may hold `features.json`, mapping each `accept_*.py` file to its feature name;
without it, the #402 names below apply.

Prints one JSON object:
- `acceptance`: `passed`, `failed` (errors and skips count as failed), `total`, `pass_rate`; the same per `tier`
  (`core`: spec behaviours 1-25; `strict`: the forms the spec's open-question answers fix) and per `feature`
  (`403-subtasks`, `404-notes`, `405-snooze`, `406-dependencies`, `407-next`, or `features.json`'s);
  `feature_weighted`, the mean of the per-feature pass rates, so each feature (one issue of the epic) weighs the
  same however many tests it has; and `failed_tests`.
- with `--run`, `run`:
  - `ticket_reviews`: each ticket's deep review, the first report posted on its PR (the `== post_review PR<n>
    <report>` lines of `logs/PR<n>-post_review.log`, `<n>` a ticket's `PR:`), plus its `*-security.md` reports, their
    findings summed by severity from each report's last `follow-up:` line (`1 blocker, 2 should-fix, 0 nit`); a
    report with no such line is listed in `unparsed`. Reports posted after the deep review (re-reviews of its fixes)
    are listed in `re_reviews`, not counted.
  - `fix_findings`: the findings sorted **Fix**, task and ticket reviews alike: each numbered line of
    `reviews/*-findings.md` (`<n>. <severity>, ...`, `<n>. <severity> | ...`, `<n>. **B · <severity> · ...`), counted by
    the first severity word on it.
  - `red_merges`: `FAIL <id> red ` lines in `logs/<id>-merge_task.log`, as telemetry's `run_metrics.py` counts them;
    `batch_red`: `FAIL batch red ` lines in `logs/batch-ship_batch.log`.
  - `hand_fixes`: the first-parent commits of <ref> after the run's last ticket merge (the newest first-parent commit
    whose subject ends `(#<n>)`, `<n>` a ticket's `PR:`) whose author name does not end in `[bot]` (the GitHub App
    merges as `<app>[bot]`); `last_merge` is null, and the count too, when <ref> holds no ticket merge.

Exits 0 once the JSON is printed, 1 with `{"error": ...}` when it cannot score.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ACCEPTANCE = HERE / "acceptance"
FEATURES = {
    "accept_subtasks.py": "403-subtasks",
    "accept_notes.py": "404-notes",
    "accept_snooze.py": "405-snooze",
    "accept_dependencies.py": "406-dependencies",
    "accept_next.py": "407-next",
}
SEVERITIES = ("blocker", "should-fix", "nit")
POSTED = re.compile(r"^== post_review PR(\d+) (\S+)")
SUMMARY = re.compile(r"^follow-up:(.*)$", re.MULTILINE)
COUNT = re.compile(r"(\d+)\s+(blocker|should-fix|nit)\b")
FIX = re.compile(r"^\d+\.[ \t]+[^\n]*?\b(blocker|should-fix|nit)\b", re.MULTILINE)
PR_NUMBER = re.compile(r"(\d+)\s*$")
TIMEOUT = 900


class Fail(Exception):
    pass


def git(repo, *args, check=True):
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and out.returncode != 0:
        raise Fail(f"git {' '.join(args)}: {out.stderr.strip()}")
    return out.stdout


# Acceptance

def load_features(acceptance):
    """The `accept_*.py` file -> feature name map: `features.json` in `acceptance`, else the #402 one."""
    path = acceptance / "features.json"
    if not path.exists():
        return FEATURES
    try:
        features = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise Fail(f"{path}: {e}") from None
    if not isinstance(features, dict) or not features or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in features.items()):
        raise Fail(f"{path}: expected an object of accept_*.py file names to feature names")
    return features


def run_acceptance(worktree, python, scratch, acceptance=ACCEPTANCE):
    """Runs the acceptance tests in `acceptance` on `worktree`; returns the rows its conftest.py wrote."""
    results = Path(scratch) / "results.json"
    env = dict(os.environ, ACCEPT_CHECKOUT=str(worktree), ACCEPT_RESULTS=str(results), PYTHONDONTWRITEBYTECODE="1")
    cmd = [python, "-I", "-m", "pytest", "-c", str(acceptance / "pytest.ini"), "-q", "--rootdir", str(acceptance),
           "--basetemp", str(Path(scratch) / "pytest"), str(acceptance)]
    try:
        out = subprocess.run(cmd, cwd=scratch, env=env, capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        raise Fail(f"acceptance tests timed out after {TIMEOUT}s") from None
    if not results.exists():
        tail = (out.stdout + out.stderr).strip().splitlines()[-3:]
        raise Fail(f"acceptance tests wrote no results (exit {out.returncode}): {' | '.join(tail)}")
    return json.loads(results.read_text(encoding="utf-8"))


def tally(rows):
    passed = sum(1 for r in rows if r["outcome"] == "passed")
    total = len(rows)
    return {"passed": passed, "failed": total - passed, "total": total,
            "pass_rate": round(passed / total, 3) if total else None}


def score_acceptance(rows, features=FEATURES):
    out = tally(rows)
    out["tier"] = {tier: tally([r for r in rows if r["tier"] == tier]) for tier in ("core", "strict")}
    out["feature"] = {name: tally([r for r in rows if r["file"] == file]) for file, name in features.items()}
    rates = [f["pass_rate"] for f in out["feature"].values() if f["pass_rate"] is not None]
    out["feature_weighted"] = round(sum(rates) / len(rates), 3) if rates else None
    out["failed_tests"] = sorted(f"{r['file']}::{r['name']}" for r in rows if r["outcome"] != "passed")
    return out


# Run folder

def header(path, key):
    m = re.search(rf"^{re.escape(key)}:[ \t]*(.*)$", path.read_text(encoding="utf-8"), re.MULTILINE)
    return m.group(1).strip() if m else None


def ticket_prs(run):
    prs = []
    for f in sorted((run / "tickets").glob("*.md")):
        m = PR_NUMBER.search(header(f, "PR") or "")
        if m:
            prs.append(int(m.group(1)))
    return prs


def severities(text):
    counts = dict.fromkeys(SEVERITIES, 0)
    for n, sev in COUNT.findall(text):
        counts[sev] += int(n)
    return counts


def ticket_reviews(run, prs):
    """Each ticket PR's deep review (the first report posted on it) and security reports, their `follow-up:` counts
    summed by severity; the reports posted after them (re-reviews of the fixes) are listed, not counted."""
    reports, later = [], []
    for pr in prs:
        log = run / "logs" / f"PR{pr}-post_review.log"
        if not log.exists():
            continue
        posted = []
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            m = POSTED.match(line)
            if m and int(m.group(1)) == pr:
                name = Path(m.group(2)).name  # the run folder may have moved since: read it from this one
                if name not in posted:
                    posted.append(name)
        plain = [n for n in posted if not n.endswith("-security.md")]
        reports += plain[:1] + [n for n in posted if n.endswith("-security.md")]
        later += plain[1:]
    out = dict.fromkeys(SEVERITIES, 0)
    unparsed = []
    for name in reports:
        path = run / "reviews" / name
        summary = SUMMARY.findall(path.read_text(encoding="utf-8")) if path.exists() else []
        if not summary:
            unparsed.append(name)
            continue
        for sev, n in severities(summary[-1]).items():
            out[sev] += n
    return {"reports": reports, **out, "unparsed": unparsed, "re_reviews": later}


def fix_findings(run):
    out = dict.fromkeys(SEVERITIES, 0)
    files = sorted((run / "reviews").glob("*-findings.md"))
    for f in files:
        for sev in FIX.findall(f.read_text(encoding="utf-8")):
            out[sev] += 1
    return {"files": len(files), **out}


def count_lines(paths, start):
    return sum(1 for p in paths for line in p.read_text(encoding="utf-8", errors="replace").splitlines()
               if line.startswith(start(p)))


def red_merges(run):
    logs = sorted((run / "logs").glob("*-merge_task.log"))
    red = count_lines(logs, lambda p: f"FAIL {p.name[:-len('-merge_task.log')]} red ")
    batch = count_lines([p for p in [run / "logs" / "batch-ship_batch.log"] if p.exists()], lambda p: "FAIL batch red ")
    return red, batch


def hand_fixes(repo, sha, prs):
    """First-parent commits of `sha` after its newest ticket merge, authored by no `[bot]`."""
    log = git(repo, "log", "--first-parent", "--format=%H%x00%an%x00%s", sha)
    rows = [line.split("\x00") for line in log.splitlines() if line]
    merged = re.compile(rf"\(#({'|'.join(str(p) for p in prs)})\)\s*$") if prs else None
    for i, (_, _, subject) in enumerate(rows):
        if merged and merged.search(subject):
            after = rows[:i]
            fixes = [{"sha": h[:7], "author": a, "subject": s} for h, a, s in after if not a.endswith("[bot]")]
            return {"last_merge": rows[i][0][:7], "after_last_merge": len(after), "count": len(fixes),
                    "commits": fixes}
    return {"last_merge": None, "after_last_merge": None, "count": None, "commits": []}


def score_run(run, repo, sha):
    prs = ticket_prs(run)
    red, batch = red_merges(run)
    return {"folder": str(run), "ticket_prs": prs, "ticket_reviews": ticket_reviews(run, prs),
            "fix_findings": fix_findings(run), "red_merges": red, "batch_red": batch,
            "hand_fixes": hand_fixes(repo, sha, prs)}


# Main

def pick_python(given, run):
    if given:
        return given
    if run and (run / "env" / "bin" / "python").exists():
        return str(run / "env" / "bin" / "python")
    return sys.executable


def score(checkout, ref, run=None, python=None, acceptance=ACCEPTANCE):
    features = load_features(acceptance)
    top = git(checkout, "rev-parse", "--show-toplevel", check=False).strip()  # empty in a bare clone
    repo = Path(top or git(checkout, "rev-parse", "--absolute-git-dir").strip())
    sha = git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()
    scratch = tempfile.mkdtemp(prefix="quality-")
    worktree = Path(scratch) / "checkout"
    try:
        git(repo, "worktree", "add", "--detach", "--quiet", str(worktree), sha)
        try:
            rows = run_acceptance(worktree, pick_python(python, run), scratch, acceptance)
        finally:
            git(repo, "worktree", "remove", "--force", str(worktree), check=False)
            git(repo, "worktree", "prune", check=False)
        out = {"ref": ref, "sha": sha, "acceptance": score_acceptance(rows, features)}
        if run:
            out["run"] = score_run(run, repo, sha)
        return out
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def main(argv=None):
    p = argparse.ArgumentParser(prog="quality.py")
    p.add_argument("checkout", type=Path)
    p.add_argument("ref")
    p.add_argument("--run", type=Path)
    p.add_argument("--python")
    p.add_argument("--acceptance", type=Path, default=ACCEPTANCE)
    args = p.parse_args(argv)
    acceptance = args.acceptance.expanduser().resolve()
    run = args.run.expanduser().resolve() if args.run else None
    try:
        if run and not (run / "tickets").is_dir():
            raise Fail(f"not a run folder: {run}")
        if not (acceptance / "pytest.ini").is_file():
            raise Fail(f"not an acceptance folder (no pytest.ini): {acceptance}")
        out = score(args.checkout.expanduser().resolve(), args.ref, run, args.python, acceptance)
    except Fail as e:
        print(json.dumps({"error": str(e)}))
        return 1
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
