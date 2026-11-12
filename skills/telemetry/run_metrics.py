#!/usr/bin/env python3
"""Outcome metrics for one build-epic run, read from its run folder and GitHub.

Usage: python3 -I run_metrics.py <run> [--gh <cmd>]

Prints one JSON object (`schema: build-epic-run-metrics/v1`):
- `tickets`, `tasks`: counts by `Status:`; tasks also by kind (`implement`, `fix` for `TT-NN-review-fixes` and
  `TT-NN-fix-ci`, `tune` for `TT-tests`), the task file names build-epic's SKILL.md gives them.
- `fix_rounds`: the `OK <id> pushed` lines in `logs/<id>-push_fix.log`, one per fix pushed to an open PR, the red
  merges (`FAIL <id> red` in `logs/<id>-merge_task.log`) and the fix tasks.
- `reviews`: the reviewer reports in `reviews/<PR>-<sha7>.md`; each after a PR's first is a re-review.
- `follow_ups`: the rows of `followups.jsonl` (build-epic's `followups.py record` writes them), counted by `severity`
  and `kind`, and the reports in `reports/<id>.md` with a `follow-up:`. A line that is not a JSON row is a gap.
- `suites`: the suites of `reports/<id>.checks.json` (a count may be `null`: output it could not read), or, for an id
  without one, the old `reports/<id>.checks` whose last line says `N passed` and `N failed`.
- `remote`: the remote runs' results in `remote/<task>-<remote run>.json`; `cost_usd` sums those with a cost and
  `runs_without_cost` counts the rest. `killed` counts the runs by each kind of kill in their `capped_kills` (a run
  killed for memory, then for timeout, counts under both), or by `killed` in a result without that list.
  `by_role` counts them by role and model, the role read from the file name: `review` and `security` for review-pr's
  `review-<PR>-<sha7>` and `security-<PR>-<sha7>`, `task` for `<TT-NN>-...` and `<TT>-tests-...`, else `other`.
  `claude_versions` counts them by the sandbox's Claude Code version (`claude_version`; `unknown` without one).
- `prs`: each ticket's and task's `PR:` line, read with `<cmd> pr view <PR> --json ...` run in `run.md`'s `Repo:`
  folder. `--gh` is that command (default `gh`), split like a shell would; nothing else reaches the network (the
  epic's `issue view` below goes through it too). Also
  `first_created`, `last_merged` and the minutes between them.
- `per_ticket`: per ticket, its tasks and fix tasks, its PR and state, `first_task_pr_opened` and `ticket_merged`
  (its first task PR's opening and its own PR's merge) and the wall-clock minutes between them, waits included;
  `compare_runs.py` keeps only the active ones, from the run's telemetry.
- `lead_time`: hours from the epic's issue opening (`run.md`'s `Epic: #<n>`, read with `<cmd> issue view <n>`) to
  the last ticket PR's merge, `null` until every ticket's PR has merged (`unmerged_tickets` names the rest).
- `human_round_trips`: the dated quotes under `decisions.md`'s Decisions recorded after approval. Approval is placed
  from `logs/decisions-decide.log`, where each `decide.py` call logs `== decide ... at <time>` and, once recorded,
  `OK decisions <set|clause|quote>`: build-epic step 5 records the clauses, then the approval reply's own decisions
  as quotes. So approval ends after the first run of recorded clauses and the quotes right after it, each logged
  within 10 minutes of the entry before it with no `set` between; those quotes, and any before the first clause,
  are not counted. `null` without `decisions.md`, or with Authorizations rows but no logged clause to place them;
  `0` before any Authorizations row.
- `hand_written_scripts`: the `*.py` and `*.sh` files in the run folder, outside `env/`, `worktrees/` and `remote/`.
- `gaps`: what could not be measured, one line each.
"""
import argparse
import json
import re
import shlex
import statistics
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

SCHEMA = "build-epic-run-metrics/v1"
PR_FIELDS = "number,url,state,additions,deletions,changedFiles,createdAt,mergedAt,mergedBy,reviews"
FIX_TASK = re.compile(r"^\d+-\d+-.*(?:review-fixes|fix-ci)$")
TUNE_TASK = re.compile(r"^\d+-tests$")
TASK_ID = re.compile(r"^(\d+-\d+)(?:-|$)")
OLD_PASSED = re.compile(r"(\d+) passed")
OLD_FAILED = re.compile(r"(\d+) failed")
NO_FOLLOW_UP = {"", "none", "-", "n/a"}
DATED = re.compile(r"^- \d{4}-\d{2}-\d{2}: ")
NOT_HAND_WRITTEN = {"env", "worktrees", "remote"}
STAMP = re.compile(r" at (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})$")
APPROVAL_GAP = timedelta(minutes=10)


def header(path, key):
    """The value of the first `key: value` line in the file, or None."""
    m = re.search(rf"^{re.escape(key)}:[ \t]*(.*?)[ \t]*$", path.read_text(encoding="utf-8"), re.MULTILINE)
    return m.group(1) if m else None


def tally(values):
    counts = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return dict(sorted(counts.items()))


def is_count(value):
    return isinstance(value, int) and not isinstance(value, bool)


def is_number(value):
    return is_count(value) or isinstance(value, float)


def task_id(stem):
    """`01-02` for `tasks/01-02-slug.md`; the whole stem for the orchestrator's tasks (`01-tests`)."""
    m = TASK_ID.match(stem)
    return m.group(1) if m else stem


def task_kind(stem):
    if TUNE_TASK.match(stem):
        return "tune"
    if FIX_TASK.match(stem):
        return "fix"
    return "implement"


def plan(run):
    """(tickets, tasks): one dict per file, with its id, status and PR line."""
    tickets = [{"id": f.stem.split("-", 1)[0], "status": header(f, "Status") or "unknown", "pr": header(f, "PR")}
               for f in sorted((run / "tickets").glob("*.md"))]
    tasks = [{"id": task_id(f.stem), "kind": task_kind(f.stem), "status": header(f, "Status") or "unknown",
              "pr": header(f, "PR")}
             for f in sorted((run / "tasks").glob("*.md"))]
    return tickets, tasks


def fix_rounds(run, tasks):
    def count(step, line):
        """{id: lines of `logs/<id>-<step>.log` starting with `line`, `<id>` filled in}."""
        found = {}
        for path in sorted((run / "logs").glob(f"*-{step}.log")):
            ident = path.name[:-len(f"-{step}.log")]
            start = line.replace("<id>", ident)
            n = sum(1 for text in path.read_text(encoding="utf-8", errors="replace").splitlines()
                    if text.startswith(start))
            if n:
                found[ident] = n
        return found

    pushed = count("push_fix", "OK <id> pushed")
    red = count("merge_task", "FAIL <id> red ")
    return {"pushed": sum(pushed.values()), "tasks_pushed": len(pushed), "red_merges": sum(red.values()),
            "fix_tasks": sum(1 for t in tasks if t["kind"] == "fix")}


# A reviewer's own report, `<PR>-<sha7>.md`; the posted copy (`.posted.md`), the numbered findings
# (`-findings.md`) and the security report (`-security.md`) beside it are not reviews of their own.
REVIEW_REPORT = re.compile(r"(\d+)-[0-9a-f]{7}\.md")


def reviews(run):
    names = [p.name for p in sorted((run / "reviews").glob("*.md"))]
    per_pr = tally(m.group(1) for n in names if (m := REVIEW_REPORT.fullmatch(n)))
    total = sum(per_pr.values())
    return {"reports": total, "prs": len(per_pr), "re_reviews": total - len(per_pr),
            "security": sum(1 for n in names if re.fullmatch(r"\d+-[0-9a-f]{7}-security\.md", n))}


def follow_ups(run, gaps):
    path = run / "followups.jsonl"
    rows = []
    if path.is_file():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                row = None
            if isinstance(row, dict):
                rows.append(row)
            else:
                gaps.append(f"follow-ups: followups.jsonl line {number} is not a JSON row")
    reported = 0
    for report in sorted((run / "reports").glob("*.md")):
        value = header(report, "follow-up")
        if value is not None and value.strip().lower() not in NO_FOLLOW_UP:
            reported += 1
    return {"rows": len(rows), "by_severity": tally(str(r.get("severity")) for r in rows),
            "by_kind": tally(str(r.get("kind")) for r in rows), "reports_with_follow_up": reported}


def suites(run, gaps):
    rows = []
    reports = run / "reports"
    new = sorted(reports.glob("*.checks.json"))
    for path in new:
        ident = path.name[:-len(".checks.json")]
        try:
            suites_ = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            gaps.append(f"suite results: {path.name} is not JSON")
            continue
        for s in suites_ if isinstance(suites_, list) else []:
            if isinstance(s, dict):
                rows.append((ident, s))
    have = {p.name[:-len(".checks.json")] for p in new}
    for path in sorted(reports.glob("*.checks")):
        if path.stem in have:
            continue
        lines = [line for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
        last = lines[-1] if lines else ""
        passed, failed = OLD_PASSED.search(last), OLD_FAILED.search(last)
        rows.append((path.stem, {"passed": int(passed.group(1)) if passed else None,
                                 "failed": int(failed.group(1)) if failed else (0 if passed else None),
                                 "skipped": None, "seconds": None, "skip_reasons": [], "rc": None}))
    if not rows:
        gaps.append("suite results: no reports/*.checks.json")
    out = {"merges": len({ident for ident, _ in rows}), "runs": len(rows), "red": 0, "unparsed": 0,
           "passed": 0, "failed": 0, "skipped": 0, "seconds": 0.0, "skip_reasons": []}
    reasons = set()
    for _, s in rows:
        rc = s.get("rc")
        if is_count(rc) and rc != 0 or rc is None and is_count(s.get("failed")) and s["failed"] > 0:
            out["red"] += 1
        counts = [s.get(k) for k in ("passed", "failed", "skipped")]
        if not all(is_count(c) for c in counts):
            out["unparsed"] += 1
        for key, value in zip(("passed", "failed", "skipped"), counts):
            out[key] += value if is_count(value) else 0
        if is_number(s.get("seconds")):
            out["seconds"] += s["seconds"]
        reasons.update(r for r in s.get("skip_reasons") or [] if isinstance(r, str))
    out["seconds"] = round(out["seconds"], 1)
    out["skip_reasons"] = sorted(reasons)
    return out


REMOTE_ROLE = re.compile(r"^(review|security)-\d+-")
REMOTE_TASK = re.compile(r"^\d+-(?:\d+|tests)")


def remote_role(stem):
    """A remote run's role from its file name: review-pr's `review-<PR>-<sha7>` and `security-<PR>-<sha7>`,
    a task's `<TT-NN>-<remote run>` (`task`), else `other`."""
    m = REMOTE_ROLE.match(stem)
    return m.group(1) if m else "task" if REMOTE_TASK.match(stem) else "other"


def remote(run, gaps):
    out = {"runs": 0, "by_status": {}, "complete": 0, "cost_usd": 0.0, "runs_without_cost": 0, "turns": 0,
           "duration_ms": 0, "tokens": {}, "models": {}, "killed": {}, "lost": 0, "by_role": {}, "claude_versions": {}}
    statuses, models, killed, versions = [], [], [], []
    for path in sorted((run / "remote").glob("*.json")):
        out["runs"] += 1
        try:
            r = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            r = None
        if not isinstance(r, dict):
            gaps.append(f"remote: {path.name} is not a result")
            out["runs_without_cost"] += 1
            statuses.append("unreadable")
            continue
        statuses.append(r.get("status") if isinstance(r.get("status"), str) else "unknown")
        out["complete"] += r.get("complete") is True
        if is_number(r.get("total_cost_usd")):
            out["cost_usd"] += r["total_cost_usd"]
        else:
            out["runs_without_cost"] += 1
        for key, field in (("turns", "num_turns"), ("duration_ms", "duration_ms")):
            if is_count(r.get(field)):
                out[key] += r[field]
        for kind, n in (r.get("usage") or {}).items() if isinstance(r.get("usage"), dict) else []:
            if is_count(n):
                out["tokens"][kind] = out["tokens"].get(kind, 0) + n
        if isinstance(r.get("model"), str):
            models.append(r["model"])
        versions.append(r["claude_version"] if isinstance(r.get("claude_version"), str) else "unknown")
        role = out["by_role"].setdefault(remote_role(path.stem), {"count": 0, "models": {}})
        role["count"] += 1
        model = r["model"] if isinstance(r.get("model"), str) else "unknown"
        role["models"][model] = role["models"].get(model, 0) + 1
        killed += kill_kinds(r)
        out["lost"] += r.get("lost") is True
    out["by_status"], out["models"], out["killed"] = tally(statuses), tally(models), tally(killed)
    out["claude_versions"] = tally(versions)
    out["by_role"] = dict(sorted(out["by_role"].items()))
    out["tokens"] = dict(sorted(out["tokens"].items()))
    out["cost_usd"] = round(out["cost_usd"], 4)
    if out["runs_without_cost"]:
        gaps.append(f"remote: {out['runs_without_cost']} run(s) without cost")
    return out


def kill_kinds(result):
    """Each kind of kill a remote run had, once: from `capped_kills` (`killed` holds only the last), else `killed`."""
    kills = result.get("capped_kills")
    if isinstance(kills, list):
        return sorted({k["kind"] for k in kills if isinstance(k, dict) and isinstance(k.get("kind"), str)})
    return [result["killed"]] if isinstance(result.get("killed"), str) else []


def decide_entries(log):
    """(what, when) for each `decide.py` call the log shows recorded: `set`, `clause` or `quote`, and its stamp."""
    entries, current = [], None
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("== decide "):
            current = {"stamp": None, "ok": None}
            entries.append(current)
        if current is None:
            continue
        if current["stamp"] is None:
            m = STAMP.search(line)  # a multi-line quote's header ends on a later line
            if m:
                current["stamp"] = when(m.group(1))
            continue
        if current["ok"] is None and line.startswith("OK decisions "):
            current["ok"] = (line.split() + [""])[2]
    return [(e["ok"], e["stamp"]) for e in entries if e["ok"]]


def approval_end(entries):
    """How many of `entries` belong up to the end of approval, or None without a clause.

    Approval ends after the first run of clauses and the quotes right after it (no `set` between), each logged
    within `APPROVAL_GAP` of the entry before it: build-epic step 5 records the clauses, then the approval reply's
    own decisions."""
    first = next((i for i, (what, _) in enumerate(entries) if what == "clause"), None)
    if first is None:
        return None
    end = first
    while end < len(entries) and entries[end][0] == "clause":
        end += 1
    while (end < len(entries) and entries[end][0] == "quote" and entries[end][1] and entries[end - 1][1]
           and entries[end][1] - entries[end - 1][1] <= APPROVAL_GAP):
        end += 1
    return end


def human_round_trips(run, gaps):
    """The dated quotes in `decisions.md` recorded after its first Authorizations row; None when that cannot be told."""
    path = run / "decisions.md"
    if not path.is_file():
        gaps.append("human round trips: no decisions.md")
        return None
    section, rows, quotes = None, 0, 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            section = line
        elif section == "## Authorizations" and line.startswith("|") and not re.match(r"^\|\s*(clause|---)\s*\|", line):
            rows += 1
        elif section == "## Decisions" and DATED.match(line):
            quotes += 1
    if not rows:
        return 0
    log = run / "logs" / "decisions-decide.log"
    if not log.is_file():
        gaps.append("human round trips: no logs/decisions-decide.log to place the approval")
        return None
    entries = decide_entries(log)
    end = approval_end(entries)
    if end is None:
        gaps.append("human round trips: no clause in logs/decisions-decide.log to place the approval")
        return None
    return max(0, quotes - sum(1 for what, _ in entries[:end] if what == "quote"))


def hand_written_scripts(run):
    """The `*.py` and `*.sh` files in the run folder outside the folders the steps fill."""
    return sum(1 for p in run.rglob("*") if p.suffix in (".py", ".sh") and p.is_file()
               and p.relative_to(run).parts[0] not in NOT_HAND_WRITTEN)


def when(stamp):
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def pr_view(gh, pr, cwd, gaps):
    """`gh pr view <pr> --json ...` as a dict, or None (named in `gaps`) when the command fails or answers no JSON."""
    return gh_view(gh, ["pr", "view", pr, "--json", PR_FIELDS], pr, cwd, gaps)


def gh_view(gh, args, name, cwd, gaps):
    """`<gh> <args>` as a dict, or None (named in `gaps` as `github: <name>: ...`) on failure or no JSON object."""
    try:
        res = subprocess.run(gh + args, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                             stdin=subprocess.DEVNULL, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        gaps.append(f"github: {name}: {e}")
        return None
    if res.returncode != 0:
        gaps.append(f"github: {name}: exit {res.returncode}")
        return None
    try:
        data = json.loads(res.stdout)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        gaps.append(f"github: {name}: no JSON")
        return None
    return data


def lead_time(run, gh, tickets, times, gaps):
    """From the epic's issue opening (`run.md`'s `Epic: #<n>`) to the last ticket PR's merge, in hours; `null` until
    every ticket's PR has merged."""
    out = {"epic": None, "epic_opened": None, "last_ticket_merged": None, "hours": None, "unmerged_tickets": []}
    epic = header(run / "run.md", "Epic") if (run / "run.md").is_file() else None
    m = re.fullmatch(r"#?(\d+)", epic or "")
    out["unmerged_tickets"] = [t["id"] for t in tickets if not times.get(t["id"], (None, None))[1]]
    merged = [times[t["id"]][1] for t in tickets if times.get(t["id"], (None, None))[1]]
    if tickets and not out["unmerged_tickets"]:
        out["last_ticket_merged"] = max(merged).isoformat()
    if not m:
        gaps.append("lead time: no Epic line in run.md")
        return out
    out["epic"] = int(m.group(1))
    data = gh_view(gh, ["issue", "view", m.group(1), "--json", "number,createdAt"], f"issue #{out['epic']}",
                   repo_dir(run), gaps) or {}
    opened = when(data.get("createdAt"))
    out["epic_opened"] = opened.isoformat() if opened else None
    if opened and out["last_ticket_merged"]:
        out["hours"] = round((max(merged) - opened).total_seconds() / 3600, 2)
    return out


def repo_dir(run):
    """`run.md`'s `Repo:` folder when it exists, else None (the current folder)."""
    repo = header(run / "run.md", "Repo") if (run / "run.md").is_file() else None
    return repo if repo and Path(repo).is_dir() else None


def prs(run, gh, items, gaps):
    cwd = repo_dir(run)
    rows, times = [], {}
    for item in items:
        if not item["pr"]:
            continue
        data = pr_view(gh, item["pr"], cwd, gaps) or {}
        created, merged = when(data.get("createdAt")), when(data.get("mergedAt"))
        times[item["id"]] = (created, merged)
        merged_by = data.get("mergedBy") if isinstance(data.get("mergedBy"), dict) else {}
        rows.append({
            "id": item["id"],
            "pr": data.get("number") if is_count(data.get("number")) else item["pr"],
            "state": data.get("state"),
            "additions": data.get("additions") if is_count(data.get("additions")) else None,
            "deletions": data.get("deletions") if is_count(data.get("deletions")) else None,
            "changed_files": data.get("changedFiles") if is_count(data.get("changedFiles")) else None,
            "hours_to_merge": round((merged - created).total_seconds() / 3600, 2) if created and merged else None,
            "merged_by": merged_by.get("login"),
            "reviews": len(data["reviews"]) if isinstance(data.get("reviews"), list) else None,
        })
    hours = [r["hours_to_merge"] for r in rows if r["hours_to_merge"] is not None]

    def total(key):
        return sum(r[key] for r in rows if r[key] is not None)

    first = min((c for c, _ in times.values() if c), default=None)
    last = max((m for _, m in times.values() if m), default=None)
    return {"count": len(rows), "merged": sum(1 for r in rows if r["state"] == "MERGED"),
            "additions": total("additions"), "deletions": total("deletions"),
            "changed_files": total("changed_files"), "reviews": total("reviews"),
            "merged_by": tally(r["merged_by"] for r in rows if r["merged_by"]),
            "hours_to_merge": {"median": round(statistics.median(hours), 2) if hours else None,
                               "max": max(hours) if hours else None},
            "first_created": first.isoformat() if first else None,
            "last_merged": last.isoformat() if last else None,
            "wall_minutes_first_pr_to_last_merge": minutes(first, last),
            "list": rows}, times


def minutes(start, end):
    return round((end - start).total_seconds() / 60, 1) if start and end else None


def per_ticket(tickets, tasks, prs_, times):
    """Per ticket: its tasks, its PR, when its first task PR opened and its own PR merged, and the wall-clock
    minutes between them."""
    out = {}
    for ticket in tickets:
        mine = [t for t in tasks if t["id"].split("-", 1)[0] == ticket["id"]]
        row = next((r for r in prs_["list"] if r["id"] == ticket["id"]), {})
        opened = [times[t["id"]][0] for t in mine if t["id"] in times and times[t["id"]][0]]
        first = min(opened, default=None)
        merged = times.get(ticket["id"], (None, None))[1]
        out[ticket["id"]] = {"tasks": len(mine), "fix_tasks": sum(1 for t in mine if t["kind"] == "fix"),
                             "pr": row.get("pr"), "state": row.get("state"),
                             "first_task_pr_opened": first.isoformat() if first else None,
                             "ticket_merged": merged.isoformat() if merged else None,
                             "wall_minutes_first_task_pr_to_ticket_merge": minutes(first, merged)}
    return out


def metrics(run, gh):
    gaps = []
    tickets, tasks = plan(run)
    if not tickets and not tasks:
        gaps.append("plan: no tickets/*.md or tasks/*.md")
    suites_, remote_ = suites(run, gaps), remote(run, gaps)
    prs_, times = prs(run, gh, tickets + tasks, gaps)
    return {
        "schema": SCHEMA,
        "run": run.name,
        "tickets": {"count": len(tickets), "by_status": tally(t["status"] for t in tickets)},
        "tasks": {"count": len(tasks), "by_status": tally(t["status"] for t in tasks),
                  "by_kind": tally(t["kind"] for t in tasks)},
        "fix_rounds": fix_rounds(run, tasks),
        "reviews": reviews(run),
        "follow_ups": follow_ups(run, gaps),
        "suites": suites_,
        "remote": remote_,
        "prs": prs_,
        "per_ticket": per_ticket(tickets, tasks, prs_, times),
        "lead_time": lead_time(run, gh, tickets, times, gaps),
        "human_round_trips": human_round_trips(run, gaps),
        "hand_written_scripts": hand_written_scripts(run),
        "gaps": gaps,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Outcome metrics for one build-epic run, as JSON.")
    parser.add_argument("run", type=Path, help="the run folder")
    parser.add_argument("--gh", default="gh", help="the command to run for gh, split like a shell would (default: gh)")
    args = parser.parse_args(argv)
    run = args.run.resolve()
    if not run.is_dir():
        parser.error(f"no run folder: {args.run}")
    gh = shlex.split(args.gh)
    if not gh:
        parser.error("--gh is empty")
    print(json.dumps(metrics(run, gh), indent=1))


if __name__ == "__main__":
    main()
