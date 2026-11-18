#!/usr/bin/env python3
"""Finishes a task once its implementer reports: commits what it left in the worktree, merges the ticket branch and
amends the task's `Writes` line with every path it changed outside it: not a file a hub task's base merge brought in
and left as the base has it (`own_changes`).

Usage: python3 -I finish_task.py <run> <TT-NN> [--report <file>] [--title <commit subject>]

Prints `OK TT-NN committed files=<n>`, the first time adding a `Finished: <UTC ISO>` header to the task (when its work
was first ready to merge: `resume.py` keeps such a task for Merge, and telemetry starts the ticket's clock there); or, when the task changed nothing, sets it `done`, removes its worktree and
branch and prints `OK TT-NN no-change`. A merge conflict with the ticket branch is left in the worktree for the
implementer and prints `FAIL TT-NN conflict <files>`; a rerun after the markers are resolved commits the merge.
`--report` copies the implementer's output (its `implementer` JSON) to `<run>/reports/<TT-NN>.json`; when the
output's status is `blocked` or `plan-wrong`, the changes are committed and the step fails with that status and its `why`, touching nothing else.
`--title` is the commit's subject (default: the task's title).

For a remote task, first copies the `result.json` of each of its runs (a retried task has several) to
`<run>/remote/<TT-NN>-<remote run>.json`: every run whose `run.json` `name` is the task's file name and whose `worktree`
is the task's worktree (task ids and names repeat across epics; the worktree is under this run folder), under the
common git dir's `remote-agent/`, or under the worktree's own git dir for records made before they moved. With none,
the log says so and the step goes on.
"""
import fnmatch
from datetime import datetime, timezone
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import ci, core, gitops, runfolder, taskwt  # noqa: E402


def options(args):
    opts, rest = {}, list(args)
    while rest:
        flag = rest.pop(0)
        if flag not in ("--report", "--title") or not rest:
            raise core.Fail(f"usage: finish_task.py <run> <TT-NN> [--report <file>] [--title <subject>] (got {flag})")
        opts[flag[2:]] = rest.pop(0)
    return opts


def read_report(ctx, given):
    """Copies the output to `<run>/reports/<id>.json` and returns its (status, why)."""
    stored = ctx.run_dir / "reports" / f"{ctx.id}.json"
    if given:
        src = core.resolve(given)
        if not src.is_file():
            raise core.Fail(f"no-report: {src}")
        stored.parent.mkdir(parents=True, exist_ok=True)
        if src != core.resolve(stored):
            shutil.copyfile(str(src), str(stored))
    if not stored.is_file():
        return "", ""
    try:
        output = json.loads(stored.read_text(encoding="utf-8"))
    except ValueError:
        return "", ""
    if not isinstance(output, dict):
        return "", ""
    return str(output.get("status") or ""), str(output.get("why") or "").strip()


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
    """Copies every matching run's `result.json` to `<run>/remote/<id>-<remote run>.json`; returns the run folders."""
    found = []
    for folder in remote_records(ctx, task):
        try:
            info = json.loads((folder / "run.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(info, dict) or info.get("name") != task.path.stem:
            continue
        worktree = info.get("worktree")
        if not isinstance(worktree, str) or not worktree or core.resolve(worktree) != core.resolve(task.worktree):
            continue
        if (folder / "result.json").is_file():
            found.append(folder)
    if not found:
        ctx.note(f"no remote result found for {ctx.id}")
    for folder in found:
        dest = ctx.run_dir / "remote" / f"{ctx.id}-{folder.name}.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".{dest.name}.tmp")
        shutil.copyfile(str(folder / "result.json"), str(tmp))
        os.replace(str(tmp), str(dest))
        ctx.note(f"remote result {folder / 'result.json'} copied to {dest}")
    return found


def own_changes(task, changed):
    """The paths of `changed` the task itself changed: a path the run's base changed (a hub task's base merge brought
    it in) counts only when HEAD's content differs from the base's, as a resolved conflict or the task's own edit
    does. The base's commit is the newest one HEAD holds (`merge-base HEAD origin/<base>`)."""
    synced = task.git("merge-base", "HEAD", f"refs/remotes/origin/{ci.base(task.ctx.run_dir)}", check=False).out.strip()
    if not synced:
        return changed
    brought = set(task.git("diff", "--name-only", f"{task.upstream}...{synced}", check=False).out.split())

    def blob(ref, path):
        res = task.git("rev-parse", "-q", "--verify", f"{ref}:{path}", check=False)
        return res.out.strip() if res.rc == 0 else None

    return [p for p in changed if p not in brought or blob("HEAD", p) != blob(synced, p)]


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
    extra = amend_writes(task, own_changes(task, changed))
    if extra:
        ctx.note(f"Writes amended with: {', '.join(extra)}")
    if not runfolder.get(task.path, "Finished"):
        runfolder.set_header(task.path, "Finished", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    return "committed", f"files={len(changed)}"


if __name__ == "__main__":
    core.main("finish_task", step, usage="[--report <file>] [--title <subject>]")
