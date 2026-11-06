"""Task and ticket PR bodies, rendered from the run folder's files only (no git, no gh).

A task body holds what the task builds (its `## What to build`), its check (`## Check`) and that check's result
(`<run>/reports/<id>.checks.json` when a step wrote one), then `Part of #n` for each of its ticket's issues, which
leaves them open. A ticket body lists its tasks with their `PR:` links, then `Closes #n` for each issue.

`<id>.checks.json` is a list of suites, or `{"suites": [...]}`; each suite has `command` (or `suite`, `name`),
`passed` or `rc`, and optionally `seconds`.
"""
import json
import re

from . import core, runfolder
from .taskwt import clean, title

NO_RESULT = "Not recorded in the run folder."


def section(text, name):
    """The body of `## <name>` in `text`, stripped, or ""."""
    m = re.search(rf"^##[ \t]+{re.escape(name)}[ \t]*\n(.*?)(?=^##?[ \t]|\Z)", text, re.MULTILINE | re.DOTALL)
    return m.group(1).strip() if m else ""


def issues(ticket_path):
    """Issue numbers named on the ticket's `Issue` line: `#12`, `gh-12`, `.../issues/12`, or bare numbers."""
    value = clean(runfolder.get(ticket_path, "Issue"))
    if not value or value.lower().startswith("none"):
        return []
    found = re.findall(r"(?:#|\bgh-|/issues/|/pull/)(\d+)", value)
    if not found and re.fullmatch(r"[\d\s,]+", value):
        found = re.findall(r"\d+", value)
    return list(dict.fromkeys(found))


def _result(run, ident):
    path = core.resolve(run) / "reports" / f"{ident}.checks.json"
    if not path.is_file():
        return NO_RESULT
    try:
        data = json.loads(path.read_text())
    except ValueError:
        return NO_RESULT
    suites = data.get("suites") if isinstance(data, dict) else data
    lines = []
    for s in suites if isinstance(suites, list) else ():
        if not isinstance(s, dict):
            continue
        name = s.get("command") or s.get("suite") or s.get("name") or "?"
        passed = s["passed"] if "passed" in s else s.get("rc") == 0
        secs = f" in {s['seconds']:g}s" if isinstance(s.get("seconds"), (int, float)) else ""
        lines.append(f"- `{name}`: {'passed' if passed else 'failed'}{secs}")
    return "\n".join(lines) or NO_RESULT


def task_body(run, ident, task_path, ticket_path):
    text = task_path.read_text()
    parts = [
        "## What it builds", section(text, "What to build") or title(task_path),
        "## Check", section(text, "Check") or "None written in the task.",
        "## Result", _result(run, ident),
    ]
    refs = [f"Part of #{n}" for n in issues(ticket_path)]
    if refs:
        parts.append("\n".join(refs))
    return "\n\n".join(parts) + "\n"


def ticket_tasks(run, ticket_path):
    """The ticket's task files, in order."""
    number = runfolder.norm(ticket_path.stem.split("-", 1)[0])
    return [f for f in sorted((core.resolve(run) / "tasks").glob("*.md"))
            if runfolder.norm(f.stem.split("-", 1)[0]) == number]


def ticket_body(run, ticket_path):
    rows = []
    for f in ticket_tasks(run, ticket_path):
        pr = clean(runfolder.get(f, "PR"))
        rows.append(f"- {title(f)}: {pr or 'no PR'}")
    parts = ["## Tasks", "\n".join(rows) or "None."]
    refs = [f"Closes #{n}" for n in issues(ticket_path)]
    if refs:
        parts.append("\n".join(refs))
    return "\n\n".join(parts) + "\n"
