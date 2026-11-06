#!/usr/bin/env python3
"""Finishes a task once its implementer reports: commits what it left in the worktree, merges the ticket branch and
amends the task's `Writes` line with every changed path outside it.

Usage: python3 -I finish_task.py <run> <TT-NN> [--report <file>] [--title <commit subject>]

Prints `OK TT-NN committed files=<n>`; or, when the task changed nothing, sets it `done`, removes its worktree and
branch and prints `OK TT-NN no-change`. A merge conflict with the ticket branch is left in the worktree for the
implementer and prints `FAIL TT-NN conflict <files>`; a rerun after the markers are resolved commits the merge.
`--report` copies the implementer's report to `<run>/reports/<TT-NN>.md`; when its status is `blocked` or
`plan-wrong`, the changes are committed and the step fails with that status and its `why`, touching nothing else.
`--title` is the commit's subject (default: the task's title).

For a remote task, first copies its run's `result.json` to `<run>/remote/<TT-NN>.json`: the newest run whose `run.json`
`name` is the task's file name (or starts with `TT-NN-`), under the common git dir's `remote-agent/`, or under the
worktree's own git dir for records made before they moved. With none, the log says so and the step goes on.
"""
import fnmatch
import json
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import core, gitops, runfolder, taskwt  # noqa: E402


def options(args):
    opts, rest = {}, list(args)
    while rest:
        flag = rest.pop(0)
        if flag not in ("--report", "--title") or not rest:
            raise core.Fail(f"usage: finish_task.py <run> <TT-NN> [--report <file>] [--title <subject>] (got {flag})")
        opts[flag[2:]] = rest.pop(0)
    return opts


def read_report(ctx, given):
    """Copies the report to `<run>/reports/<id>.md` and returns its (status, why)."""
    stored = ctx.run_dir / "reports" / f"{ctx.id}.md"
    if given:
        src = core.resolve(given)
        if not src.is_file():
            raise core.Fail(f"no-report: {src}")
        stored.parent.mkdir(parents=True, exist_ok=True)
        if src != core.resolve(stored):
            shutil.copyfile(str(src), str(stored))
    if not stored.is_file():
        return "", ""
    text = stored.read_text()
    status = re.search(r"^status:[ \t]*(\S+)", text, re.MULTILINE)
    why = re.search(r"^why:[ \t]*(.*)$", text, re.MULTILINE)
    return (status.group(1) if status else ""), (why.group(1).strip() if why else "")


def remote_records(ctx, task):
    """Every `remote-agent/<run>/` folder of the repo's common git dir and, while it exists, the worktree's git dir."""
    dirs = [(task.repo, "--path-format=absolute", "--git-common-dir")]
    if task.worktree.is_dir():
        dirs.append((task.worktree, "--absolute-git-dir"))
    folders = {}
    for cwd, *flags in dirs:
        res = ctx.run(["git", "-C", cwd, "rev-parse", *flags])
        if res.rc == 0 and res.out.strip():
            root = core.resolve(res.out.strip()) / "remote-agent"
            for folder in sorted(root.glob("*/")) if root.is_dir() else []:
                folders.setdefault(core.resolve(folder), None)
    return list(folders)


def copy_remote_result(ctx, task):
    """Copies the newest matching run's `result.json` to `<run>/remote/<id>.json`; returns the run folder or None."""
    found = []
    for folder in remote_records(ctx, task):
        try:
            info = json.loads((folder / "run.json").read_text())
        except (OSError, ValueError):
            continue
        name = info.get("name") if isinstance(info, dict) else None
        if not isinstance(name, str) or not (name == task.path.stem or name == ctx.id or name.startswith(ctx.id + "-")):
            continue
        if (folder / "result.json").is_file():
            started = info.get("started")
            found.append((started if isinstance(started, (int, float)) else 0, folder.name, folder))
    if not found:
        ctx.note(f"no remote result found for {ctx.id}")
        return None
    folder = max(found)[2]
    dest = ctx.run_dir / "remote" / f"{ctx.id}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.tmp")
    shutil.copyfile(str(folder / "result.json"), str(tmp))
    os.replace(str(tmp), str(dest))
    ctx.note(f"remote result {folder / 'result.json'} copied to {dest}")
    return folder


def amend_writes(task, changed):
    """Appends each changed path outside the task's `Writes` line to it; returns those paths."""
    writes = runfolder.get(task.path, "Writes")
    if writes is None:
        return []  # a task the orchestrator wrote runs alone in its ticket
    entries = [taskwt.clean(e) for e in writes.split(",")]
    entries = [e for e in entries if e and e.lower() != "none"]

    def covered(path):
        return any(path == e or path.startswith(e.rstrip("/") + "/") or fnmatch.fnmatchcase(path, e) for e in entries)

    extra = [p for p in changed if not covered(p)]
    if extra:
        runfolder.set_header(task.path, "Writes", ", ".join(entries + extra))
    return extra


def no_change(ctx, task):
    branch = task.branch()
    repo = gitops.Git(task.repo, ctx.log)
    repo.worktree_remove(task.worktree)
    repo.branch_delete(branch)
    runfolder.set_header(task.path, "Status", "done")
    return "no-change"


def step(ctx):
    opts = options(ctx.args)
    task = taskwt.Task(ctx)
    status, why = read_report(ctx, opts.get("report"))
    copy_remote_result(ctx, task)  # before anything removes the worktree, and its git dir with older records
    if not task.worktree.is_dir():
        if (runfolder.get(task.path, "Status") or "").strip() == "done" and status not in ("blocked", "plan-wrong"):
            return "no-change"  # a rerun after the worktree was removed
        task.need_worktree()
    if task.merging():
        task.conclude_merge()
    if task.dirty():
        task.git("add", "-A")
        if task.git("diff", "--cached", "--quiet", check=False).rc == 1:
            task.git("commit", "-m", opts.get("title") or taskwt.title(task.path))
    if status in ("blocked", "plan-wrong"):
        raise core.Fail(f"{status} {why}".strip())
    task.fetch()
    if not task.changed():
        return no_change(ctx, task)
    task.merge_upstream()
    changed = task.changed()
    extra = amend_writes(task, changed)
    if extra:
        ctx.note(f"Writes amended with: {', '.join(extra)}")
    return "committed", f"files={len(changed)}"


if __name__ == "__main__":
    core.main("finish_task", step, usage="[--report <file>] [--title <subject>]")
