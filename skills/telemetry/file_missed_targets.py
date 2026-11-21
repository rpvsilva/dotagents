#!/usr/bin/env python3
"""File each missed target of a build-epic run comparison as a GitHub issue, and name its number in the comparison.

Usage: python3 -I file_missed_targets.py <comparison.json> --epic <n> [--repo <owner/repo>] [--gh <cmd>]

Reads `comparison.targets` (`compare_runs.py --targets`'s output) and files one issue per target whose `status` is
`missed` and that has no `issue` yet: its title names the target, its body holds the threshold, the run's value, an
HTML marker (`<!-- telemetry-missed-target: <id> -->`) and `Part of #<n>`. Before filing, it searches the repo's
issues, open or closed, for that marker (`gh issue list --search`): a target an issue already holds is recorded, not
filed again, so a comparison rerun and saved over the file files no duplicates. After each issue it writes the
issue number into that target (`issue`) in the comparison file, so a rerun, or a run after a failure, files only the
rest; a `gh issue create` that prints no URL is looked up by its marker. Met and unknown targets file nothing; a
failing search files nothing.
Issues are filed as the identity the `identity_command` Setting names (read through `~/.agents/bin/settings`): a
command printing a GitHub token, run before every gh call; unset (or no settings tool), the current gh login.
`--gh` is the gh command (default `gh`), split like a shell would. Prints `{"filed": {id: n}, "already": {id: n}}`.
Exit 2 when the comparison has no targets.
"""
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

SEARCH = "telemetry-missed-target"
MARKER = "<!-- " + SEARCH + ": {} -->"
MARKER_RE = re.compile(r"<!-- " + SEARCH + r": (\S+) -->")
OPS = {"lt": "<", "le": "<=", "eq": "=", "ge": ">=", "gt": ">"}


def run(argv, **kw):
    if os.name == "nt" and not argv[0].endswith(".exe"):  # Windows starts no `#!` script: take Git Bash's bash
        argv = [shutil.which("bash") or "bash", *argv]
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL, **kw)


def identity_command():
    """The `identity_command` Setting, split, or None when unset or the settings tool is missing."""
    # `HOME` first, as in Git Bash: on Windows `expanduser` reads the profile folder, not where install.sh linked.
    tool = os.path.join(os.environ.get("HOME") or os.path.expanduser("~"), ".agents", "bin", "settings")
    if not os.path.exists(tool):
        tool = shutil.which("settings") or tool
    if not os.path.exists(tool):
        return None
    res = run([tool, "get", "telemetry", "identity_command"])
    if res.returncode != 0:
        sys.exit(f"file_missed_targets.py: settings failed: {res.stderr.strip()}")
    return shlex.split(res.stdout.strip()) or None


def gh_env(command):
    """This process's environment, with `GH_TOKEN` a fresh token from `command` when one is set."""
    if not command:
        return None
    top = run(["git", "rev-parse", "--show-toplevel"])
    try:
        res = run(command, cwd=top.stdout.strip() if top.returncode == 0 else None)
    except OSError as e:
        sys.exit(f"file_missed_targets.py: identity_command failed: {e}")
    token = res.stdout.strip()
    if res.returncode != 0 or len(token.split()) != 1:
        sys.exit(f"file_missed_targets.py: identity_command gave no token (rc {res.returncode}): {res.stderr.strip()}")
    return {**os.environ, "GH_TOKEN": token}


def fmt(value, unit):
    return f"{value} {unit}" if unit else str(value)


def issue_text(tid, t, epic):
    """(title, body) for one missed target."""
    title = f"build-epic rerun missed target {tid}: {t.get('metric')} {OPS.get(t['op'], t['op'])} {t['threshold']}"
    lines = [f"The build-epic rerun (run `{t.get('run')}`) missed the target **{tid}** ({t.get('source')}).", "",
             f"- Threshold: {t.get('metric')} {OPS.get(t['op'], t['op'])} {fmt(t['threshold'], t.get('unit'))}",
             f"- Value: {fmt(t.get('value'), t.get('unit'))}"]
    if t.get("basis"):
        lines.append(f"- Basis: {t['basis']}" + (f", range {t['range'][0]} to {t['range'][1]}" if t.get("range") else ""))
    lines += ["", MARKER.format(tid), f"Part of #{epic}"]
    return title, "\n".join(lines) + "\n"


def search(gh, repo, env, stop):
    """{target id: issue number} for the issues (open or closed) whose body holds a target's marker; the lowest
    number when several do."""
    res = run(gh + ["issue", "list", "--search", f'"{SEARCH}" in:body', "--state", "all", "--limit", "1000",
                    "--json", "number,body"] + repo, env=env)
    try:
        issues = json.loads(res.stdout) if res.returncode == 0 else None
    except ValueError:
        issues = None
    if not isinstance(issues, list):
        stop(f"searching the filed issues failed (rc {res.returncode}): {(res.stderr or res.stdout).strip()}")
    held = {}
    for issue in issues:
        if not isinstance(issue, dict) or not isinstance(issue.get("number"), int) or not isinstance(issue.get("body"), str):
            continue
        number, body = issue["number"], issue["body"]
        for tid in MARKER_RE.findall(body):
            held[tid] = min(held.get(tid, number), number)
    return held


def save(path, data):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def main(argv=None):
    parser = argparse.ArgumentParser(description="File each missed target of a run comparison as a GitHub issue.")
    parser.add_argument("comparison", type=Path, help="compare_runs.py's output, run with --targets")
    parser.add_argument("--epic", required=True, type=int, help="the epic issue each issue is part of")
    parser.add_argument("--repo", help="the repo to file in (owner/repo; default: gh's current repo)")
    parser.add_argument("--gh", default="gh", help="the command to run for gh, split like a shell would (default: gh)")
    args = parser.parse_args(argv)
    gh = shlex.split(args.gh)
    if not gh:
        parser.error("--gh is empty")
    data = json.loads(args.comparison.read_text(encoding="utf-8"))
    targets = (data.get("comparison") or {}).get("targets")
    if not isinstance(targets, dict):
        print(f"{args.comparison}: no comparison.targets; run compare_runs.py with --targets", file=sys.stderr)
        return 2

    missed = {tid: t for tid, t in targets.items() if t.get("status") == "missed"}
    already = {tid: t["issue"] for tid, t in missed.items() if t.get("issue")}
    todo = [tid for tid in missed if tid not in already]
    command = identity_command() if todo else None
    repo = ["--repo", args.repo] if args.repo else []
    filed = {}

    def stop(message):
        print(json.dumps({"filed": filed, "already": already}))
        sys.exit(f"file_missed_targets.py: {message}")

    if todo:
        held = search(gh, repo, gh_env(command), stop)
        for tid in [t for t in todo if t in held]:
            missed[tid]["issue"] = already[tid] = held[tid]
            todo.remove(tid)
        save(args.comparison, data)
    for tid in todo:
        title, body = issue_text(tid, missed[tid], args.epic)
        res = run(gh + ["issue", "create", "--title", title, "--body", body] + repo, env=gh_env(command))
        if res.returncode != 0:
            stop(f"filing {tid} failed (rc {res.returncode}): {(res.stderr or res.stdout).strip()}")
        url = res.stdout.strip().splitlines()[-1] if res.stdout.strip() else ""
        number = url.rstrip("/").rsplit("/", 1)[-1]
        if not number.isdigit():  # filed, but no URL to tell its number: find it by its marker
            number = str(search(gh, repo, gh_env(command), stop).get(tid, ""))
            if not number:
                stop(f"filed {tid} but found no issue number; a rerun finds it by its marker")
        missed[tid]["issue"] = filed[tid] = int(number)
        save(args.comparison, data)
    print(json.dumps({"filed": filed, "already": already}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
