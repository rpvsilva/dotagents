"""Task and ticket PR bodies, rendered from the run folder's files only (no git, no gh).

A task body holds what the task builds (its `## What to build`), its check (`## Check`) and that check's result
(`<run>/reports/<id>.checks.json` when a step wrote one), then `Part of #n` for each of its ticket's issues, which
leaves them open. A ticket body is the orchestrator's text (filling the repo's PR template), then its tasks with their
`PR:` links, then `Closes #n` for each issue. `empty_sections` names the template's sections a body leaves empty.

Both bodies hold a `## Merge` section before those refs when `<run>/decisions.md` exists: who merges (the App, or the
human's own login), into which branch (a task PR into its ticket's `Branch:`, a ticket PR into `base:` or the run's
default branch), on what proof (a task PR: a green local run of `map.md`'s Checks, since CI and reviews run on ticket
PRs only; a ticket PR: `review_before_agent_merge` and `ci`), and each Authorizations row quoted as recorded.
With `ci: off` it also holds `LOCAL_PROOF`, the line saying no CI runs and the local Checks run is the proof.
With no `decisions.md` the section is left out.

`<id>.checks.json` is a list of suites, or `{"suites": [...]}`; each suite has `command` (or `suite`, `name`),
`passed` or `rc`, and optionally `seconds` and `skip_reasons` (a list of strings). A ticket body holds a
`## Not exercised here` section listing, once each, the skip reasons its tasks' files saved; none, no section.
"""
import json
import re

from . import ci, core, decisions, runfolder
from .taskwt import clean, title

NO_RESULT = "Not recorded in the run folder."
LOCAL_PROOF = "CI is off for this run: the local run of map.md's Checks is the proof."


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
    lines = []
    for s in _suites(core.resolve(run) / "reports" / f"{ident}.checks.json"):
        name = s.get("command") or s.get("suite") or s.get("name") or "?"
        passed = s["passed"] if "passed" in s else s.get("rc") == 0
        secs = f" in {s['seconds']:g}s" if isinstance(s.get("seconds"), (int, float)) else ""
        lines.append(f"- `{name}`: {'passed' if passed else 'failed'}{secs}")
    return "\n".join(lines) or NO_RESULT


def _suites(path):
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    suites = data.get("suites") if isinstance(data, dict) else data
    return [s for s in suites if isinstance(s, dict)] if isinstance(suites, list) else []


def skip_reasons(run, ticket_path):
    """The skip reasons saved in `<run>/reports/<TT>-*.checks.json` for the ticket `TT`, in file order, once each."""
    number = runfolder.norm(ticket_path.stem.split("-", 1)[0])
    reasons = []
    for f in sorted((core.resolve(run) / "reports").glob("*.checks.json")):
        if runfolder.norm(f.name.split("-", 1)[0]) != number:
            continue
        for s in _suites(f):
            listed = s.get("skip_reasons")
            if isinstance(listed, list):
                reasons += [r.strip() for r in listed if isinstance(r, str) and r.strip()]
    return list(dict.fromkeys(reasons))


def not_exercised(run, ticket_path):
    """The `## Not exercised here` section for the ticket, or [] when its tasks saved no skip reason."""
    reasons = skip_reasons(run, ticket_path)
    return ["## Not exercised here", "\n".join(f"- {r}" for r in reasons)] if reasons else []


def merge_section(run, into=None):
    """The `## Merge` section for a PR into `into` (None: a ticket PR, into `base:` or the run's default branch), from
    `<run>/decisions.md`; [] when the file is missing."""
    d = decisions.load(run)
    if d is None:
        return []
    base = d.get("base") or runfolder.run_info(run)["Default"]
    task = bool(into) and into != base
    into = into or base
    if d.get("merge_identity") == "human":
        who = d.get("human")
        who = (f"`{who}`, " if who else "") + "the human's own login, with `GH_TOKEN` unset"
    else:
        who = "the App, with its installation token"
    if task:
        review, proof = "", "a green local run of map.md's Checks"
    else:
        review = "a review and " if d.get("review_before_agent_merge") == "yes" else ""
        proof = "green CI" if ci.enabled(run) else "a green local run (CI is off for this run)"
    rows = [f"- {a.clause} (waived: {a.waived}): {a.quote}" for a in d.authorizations]
    lines = [f"Merges as {who}, into `{into}`, after {review}{proof}.", "",
             "Authorizations:\n" + "\n".join(rows) if rows else "Authorizations: none recorded."]
    if not ci.enabled(run):
        lines[1:1] = ["", LOCAL_PROOF]
    return ["## Merge", "\n".join(lines)]


def task_body(run, ident, task_path, ticket_path):
    text = task_path.read_text()
    parts = [
        "## What it builds", section(text, "What to build") or title(task_path),
        "## Check", section(text, "Check") or "None written in the task.",
        "## Result", _result(run, ident),
        *merge_section(run, clean(runfolder.get(ticket_path, "Branch")) or "?"),
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


TEMPLATES = (".github/pull_request_template.md", ".github/PULL_REQUEST_TEMPLATE.md", "pull_request_template.md",
             "PULL_REQUEST_TEMPLATE.md", "docs/pull_request_template.md", "docs/PULL_REQUEST_TEMPLATE.md")


def template(root):
    """The text of the PR template in the checkout at `root`, or None when it has none."""
    for name in TEMPLATES:
        path = core.resolve(root) / name
        if path.is_file():
            return path.read_text()
    return None


def _empty(text):
    """True when a section says nothing: blank once comments and unchecked boxes go, or only `TODO` or `N/A`."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = "\n".join(line for line in text.splitlines() if not re.match(r"\s*(?:[-*+]\s*)?\[ \]", line)).strip()
    return not text or bool(re.fullmatch(r"(?:todo|tbd|n/?a)\.?", text, re.IGNORECASE))


def empty_sections(template_text, body):
    """The `## ` sections of the template that `body` leaves empty (missing, blank, `TODO`, `N/A`), in order."""
    names = re.findall(r"^##[ \t]+(.+?)[ \t]*$", template_text, re.MULTILINE)
    return [n for n in names if _empty(section(body, n))]


def ticket_body(run, ticket_path, lead=""):
    """`lead` (the orchestrator's text), then the ticket's tasks with their PR links, then `Closes #n`."""
    rows = []
    for f in ticket_tasks(run, ticket_path):
        pr = clean(runfolder.get(f, "PR"))
        rows.append(f"- {title(f)}: {pr or 'no PR'}")
    parts = ([lead.strip()] if lead.strip() else []) + ["## Tasks", "\n".join(rows) or "None."]
    parts += not_exercised(run, ticket_path)
    parts += merge_section(run)
    refs = [f"Closes #{n}" for n in issues(ticket_path)]
    if refs:
        parts.append("\n".join(refs))
    return "\n\n".join(parts) + "\n"
