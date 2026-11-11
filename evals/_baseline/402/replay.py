#!/usr/bin/env python3
"""Seeds a fresh build-epic run folder from a fixed plan, so a build-epic run started on it resumes past planning.

Usage: python3 -I replay.py <plan> <run folder> --base <branch>

<plan> holds a build-epic planner's output as just approved (`spec.md`, `map.md`, `areas.md`, optional `notes/`,
`tickets/`, `tasks/`), with the run's base written as `@BASE@`. Every `@BASE@` becomes <branch> in the copy. The run
folder must not exist or be empty. The plan is refused when a ticket or task is not `Status: ready`, carries a line the
step scripts add (`PR:`, `Done:`, `Claimed by:`, `CI red:`), when a task is one the orchestrator writes mid-run
(`*-review-fixes.md`, `*-tests.md`, `*-fix-ci.md`), or when build-epic's `plan_check.py` does not print `plan ok`.

Prints `OK replay seeded <run> tickets=<n> tasks=<m> base=<branch>`, or `FAIL replay <reason>` and exits 1.
It writes no `run.md` or `decisions.md`: `init_run.py` and `decide.py` own those (README.md says the order).
"""
import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

PLACEHOLDER = "@BASE@"
PLAN_FILES = ("spec.md", "map.md", "areas.md")
PLAN_DIRS = ("notes", "tickets", "tasks")
RUNTIME_LINE = re.compile(r"^(PR|Done|Claimed by|CI red):", re.MULTILINE)
STATUS = re.compile(r"^Status:[ \t]*(.*)$", re.MULTILINE)
ORCHESTRATOR_TASKS = ("-review-fixes.md", "-tests.md", "-fix-ci.md")
BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")
PLAN_CHECK = Path(__file__).resolve().parents[3] / "skills" / "build-epic" / "plan_check.py"


class Fail(Exception):
    pass


def check_plan(plan):
    """Raises Fail naming the first file that does not read as a just-approved plan; returns (tickets, tasks)."""
    for name in PLAN_FILES:
        if not (plan / name).is_file():
            raise Fail(f"missing {name}")
    tickets = sorted((plan / "tickets").glob("*.md"))
    tasks = sorted((plan / "tasks").glob("*.md"))
    if not tickets or not tasks:
        raise Fail("no tickets or no tasks")
    for f in tasks:
        if f.name.endswith(ORCHESTRATOR_TASKS):
            raise Fail(f"orchestrator task {f.name}: the run writes it, the plan does not")
    for f in tickets + tasks:
        text = f.read_text(encoding="utf-8")
        status = STATUS.search(text)
        if not status or status.group(1).strip() != "ready":
            raise Fail(f"{f.parent.name}/{f.name} is not Status: ready")
        line = RUNTIME_LINE.search(text)
        if line:
            raise Fail(f"{f.parent.name}/{f.name} has a run-time {line.group(1)}: line")
    return tickets, tasks


def copy_plan(plan, run, base):
    for name in PLAN_FILES:
        shutil.copyfile(plan / name, run / name)
    for name in PLAN_DIRS:
        if (plan / name).is_dir():
            shutil.copytree(plan / name, run / name)
    for f in run.rglob("*.md"):
        f.write_text(f.read_text(encoding="utf-8").replace(PLACEHOLDER, base), encoding="utf-8")


def plan_check(run):
    out = subprocess.run([sys.executable, "-I", str(PLAN_CHECK), str(run)], capture_output=True, text=True)
    text = (out.stdout + out.stderr).strip()
    if out.returncode != 0 or not text.startswith("plan ok"):
        raise Fail(f"plan_check: {text.splitlines()[0] if text else out.returncode}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="replay.py")
    p.add_argument("plan", type=Path)
    p.add_argument("run", type=Path)
    p.add_argument("--base", required=True)
    args = p.parse_args(argv)
    plan, run = args.plan.expanduser().resolve(), args.run.expanduser().resolve()
    try:
        if not BRANCH.fullmatch(args.base) or ".." in args.base or args.base.endswith((".lock", "/")):
            raise Fail(f"bad-base {args.base!r}")
        tickets, tasks = check_plan(plan)
        if run.exists() and (not run.is_dir() or any(run.iterdir())):
            raise Fail(f"run folder not empty: {run}")
        run.mkdir(parents=True, exist_ok=True)
        try:
            copy_plan(plan, run, args.base)
            plan_check(run)
        except Fail:
            for child in run.iterdir():  # leave the folder as empty as it was given
                shutil.rmtree(child) if child.is_dir() else child.unlink()
            raise
    except Fail as e:
        print(f"FAIL replay {e}")
        return 1
    print(f"OK replay seeded {run} tickets={len(tickets)} tasks={len(tasks)} base={args.base}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
