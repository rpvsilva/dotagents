"""Saved telemetry runs: load them from their folders, oldest first."""
import json
import math
from pathlib import Path

from report import TOKEN_KINDS, saved_runs_dir

NO_SKILL = "no skill"


def default_dirs():
    """The machine-wide folder automatic saves go to, then the current repo's manual saves."""
    return [Path.home() / ".agents" / "telemetry", saved_runs_dir()]


def is_number(value):
    """A finite int or float; json.loads reads Infinity and NaN as floats."""
    return isinstance(value, int) and not isinstance(value, bool) or isinstance(value, float) and math.isfinite(value)


def readable_span(span):
    """Whether compare can sum this span: its figures are numbers, its skill a string or None."""
    if not isinstance(span, dict) or not isinstance(span.get("skill"), (str, type(None))):
        return False
    if not all(is_number(span.get(k)) for k in ("wallClockSeconds", "turns", "toolCalls")):
        return False
    models = span.get("tokensByModel")
    return isinstance(models, dict) and all(
        isinstance(counts, dict) and all(is_number(counts.get(k)) for k in TOKEN_KINDS)
        for counts in models.values())


def read(path):
    """The run in this file; None when it is not valid JSON or is not a run compare can read
    (spans a list of readable spans, start a string)."""
    try:
        run = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(run, dict) or not isinstance(run.get("spans"), list):
        return None
    if not isinstance(run.get("start"), (str, type(None))) or not isinstance(run.get("date"), (str, type(None))):
        return None
    return run if all(readable_span(s) for s in run["spans"]) else None


def saved_runs(dirs=None):
    """(runs, skipped): the runs in the given folders (default: default_dirs()), sorted by start;
    skipped counts the files that are not valid JSON or not readable runs.
    A session's automatic save (whole: true) covers the whole session, so it alone stands for it;
    without one, every manual save of the session is its own run, as each measures its own stretch.
    The same save found twice (same session, start and end) counts once."""
    whole, manual, skipped = {}, {}, 0
    for folder in dirs if dirs is not None else default_dirs():
        folder = Path(folder)
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.json")):
            run = read(path)
            if run is None:
                skipped += 1
                continue
            session = run.get("sessionId")
            if session is None:
                manual.setdefault(str(path.resolve()), run)
            elif run.get("whole"):
                whole.setdefault(session, run)
            else:
                manual.setdefault((session, run.get("start"), run.get("end")), run)
    kept = list(whole.values()) + [
        run for run in manual.values() if run.get("sessionId") not in whole]
    return sorted(kept, key=lambda r: r.get("start") or ""), skipped


def short_name(skill):
    """The skill's name without its plugin namespace; NO_SKILL for None."""
    return NO_SKILL if skill is None else skill.split(":")[-1]


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
