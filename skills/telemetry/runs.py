"""Saved telemetry runs: load them from their folders, one per session, oldest first."""
import json
from pathlib import Path

from report import saved_runs_dir

NO_SKILL = "no skill"


def default_dirs():
    """The machine-wide folder automatic saves go to, then the current repo's manual saves."""
    return [Path.home() / ".agents" / "telemetry", saved_runs_dir()]


def read(path):
    """The run in this file; None when it is not valid JSON or holds no spans."""
    try:
        run = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return run if isinstance(run, dict) and "spans" in run else None


def preferred(run):
    # The automatic save covers the whole session; among manual saves, the earliest start wins.
    return (not run.get("whole"), run.get("start") or "")


def saved_runs(dirs=None):
    """(runs, skipped): one run per session from the given folders (default: default_dirs()),
    sorted by start; skipped counts the files that are not valid JSON or have no spans."""
    by_session, skipped = {}, 0
    for folder in dirs if dirs is not None else default_dirs():
        folder = Path(folder)
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.json")):
            run = read(path)
            if run is None:
                skipped += 1
                continue
            key = run.get("sessionId") or str(path.resolve())
            if key not in by_session or preferred(run) < preferred(by_session[key]):
                by_session[key] = run
    return sorted(by_session.values(), key=lambda r: r.get("start") or ""), skipped


def run_date(run):
    """YYYY-MM-DD of the run: its date, else (version 2 runs) the day of its start."""
    return run.get("date") or (run.get("start") or "")[:10]


def skill_matches(span_skill, name):
    """Whether a span's skill (full name, None outside any skill) is the skill called name.
    Plugin skills are namespaced ("plugin:skill"); either spelling names the same skill,
    as extract.py's same_skill. None matches only "no skill"."""
    if span_skill is None:
        return name == NO_SKILL
    return span_skill == name or span_skill.endswith(":" + name)
