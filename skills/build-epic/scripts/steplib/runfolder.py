"""Ticket, task and run files of a run folder: find them by number, read and set their `Key: value` header lines.

Parsing reuses `header`, `norm` and `blockers` from `skills/build-epic/plan_check.py`. A header write replaces the file
atomically (temp file in the same folder, then rename) under a lock, so a reader never sees half a file and two
writers never lose each other's change.
"""
import importlib.util
import os
import re
import tempfile
from pathlib import Path

from . import core

_spec = importlib.util.spec_from_file_location(
    "build_epic_plan_check", Path(__file__).resolve().parents[2] / "plan_check.py")
plan_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(plan_check)
header, norm, blockers = plan_check.header, plan_check.norm, plan_check.blockers

HEADER_LINE = re.compile(r"^[A-Z][A-Za-z0-9 _-]*:[ \t]")


def get(path, key):
    """The value of header `key` in the file at `path`, or None."""
    return header(Path(path).read_text(), re.escape(key))


def set_header(path, key, value, title="# Run"):
    """Sets header `key` to `value` in the file at `path`: replaces its first line, or adds one after the headers.

    A missing file is created with `title`. The write is atomic and locked per folder.
    """
    value = str(value)
    if "\n" in value or "\r" in value:
        raise ValueError(f"header {key} value has a newline")
    path = core.resolve(path)
    with core.file_lock(path.parent / ".headers.lock"):
        text = path.read_text() if path.exists() else f"{title}\n"
        lines = text.splitlines(keepends=True)
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        line = f"{key}: {value}\n"
        pattern = re.compile(rf"^{re.escape(key)}:")
        for i, old in enumerate(lines):
            if pattern.match(old):
                lines[i] = line
                break
        else:
            # After the title, then after the block of header lines that follows it.
            at = 1 if lines and lines[0].startswith("#") else 0
            while at < len(lines) and HEADER_LINE.match(lines[at]):
                at += 1
            lines.insert(at, line)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.writelines(lines)
            if path.exists():
                os.chmod(tmp, path.stat().st_mode & 0o777)
            os.replace(tmp, str(path))
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise


def _one(found, what):
    if not found:
        raise core.Fail(f"no-{what}")
    if len(found) > 1:
        raise core.Fail(f"ambiguous-{what}: {' '.join(f.name for f in found)}")
    return found[0]


def ticket_file(run, ticket):
    """`<run>/tickets/TT-*.md` for ticket number `ticket` (`1`, `01`)."""
    found = [f for f in sorted((core.resolve(run) / "tickets").glob("*.md"))
             if norm(f.stem.split("-", 1)[0]) == norm(str(ticket))]
    return _one(found, "ticket")


def task_file(run, task):
    """`<run>/tasks/TT-NN-*.md` for task id `TT-NN` (`01-02`, `1-2`), or `TT-<name>` for orchestrator tasks
    (`01-tests`, `01-review-fixes`)."""
    tt, _, rest = str(task).partition("-")
    if not tt.isdigit() or not rest:
        raise core.Fail(f"bad-task-id: {task}")
    found = []
    for f in sorted((core.resolve(run) / "tasks").glob("*.md")):
        parts = f.stem.split("-", 2)
        if len(parts) < 2 or norm(parts[0]) != norm(tt):
            continue
        if rest.isdigit() and parts[1].isdigit() and norm(parts[1]) == norm(rest):
            found.append(f)
        elif not rest.isdigit() and f.stem.split("-", 1)[1] == rest:
            found.append(f)
    return _one(found, "task")


def task_blockers(path):
    """The task's `Blocked by` line as (task numbers, ticket numbers)."""
    return blockers(get(path, "Blocked by"))


def run_info(run):
    """`Repo`, `Default` and `Remote` from `<run>/run.md`, written by `init_run.py`."""
    path = core.resolve(run) / "run.md"
    if not path.exists():
        raise core.Fail("no-run-md: run init_run.py first")
    info = {k: get(path, k) for k in ("Repo", "Default", "Remote")}
    missing = [k for k, v in info.items() if not v]
    if missing:
        raise core.Fail(f"run-md-missing: {' '.join(missing)}")
    return info
