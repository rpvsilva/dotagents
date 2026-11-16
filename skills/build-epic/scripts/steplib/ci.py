"""The ticket PR's facts: the run's base (`decisions.md`'s `base:`, or the default branch when it is unset), whether CI
runs (`decisions.md`'s `ci:`), a `PR:` line's number and the PR's title. CI runs once per ticket, on its PR: tasks
merge locally (`merge_task.py`) and wait on no checks.
"""
import re

from . import core, decisions, runfolder

CONVENTIONAL = re.compile(r"^[a-z]+(\([^)]*\))?!?: ")


def enabled(run):
    """False when `<run>/decisions.md` says `ci: off` or `ci: none`; True otherwise (no file: CI assumed on)."""
    value = decisions.get(run, "ci")
    return (value or "on").lower() not in ("off", "none", "no")


def base(run, default=None):
    """The branch the run's tickets are cut from and open against: `<run>/decisions.md`'s `base:` when it names a
    branch, else `default` (None: `run.md`'s `Default:`)."""
    path = core.resolve(run) / "decisions.md"
    value = (runfolder.get(path, "base") if path.exists() else None) or ""
    value = value.strip().strip("`")
    if value.lower() not in ("", "null", "none", "default", "~"):
        return value
    return default or runfolder.run_info(run)["Default"]


def pr_number(value):
    """The PR number in a `PR:` line's value (`#12`, `12` or a PR URL), or None."""
    m = re.search(r"(\d+)\D*$", value or "")
    return m.group(1) if m else None


def title(ticket):
    """The ticket PR's title: the ticket's title, in the commit convention (`feat: ` unless it already has a type).
    The title's own case is kept."""
    m = re.search(r"^# *[^:\n]*: *(.+)$", ticket.read_text(encoding="utf-8"), re.MULTILINE)
    text = m.group(1).strip() if m else ticket.stem
    return text if CONVENTIONAL.match(text) else f"feat: {text}"
