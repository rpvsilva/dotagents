"""Task and ticket PR bodies, rendered from the run folder's files only (no git, no gh).

A task body holds what the task builds (its `## What to build`), its check (`## Check`) and that check's result
(`<run>/reports/<id>.checks.json` when a step wrote one), then `Part of #n` for each of its ticket's issues, which
leaves them open.

A ticket body holds no prose but the run's. It has one `## ` section per heading of the repo's PR template (with no
template, `Summary` and `Testing`), each filled by what its name asks for: a summary (the ticket's title, its issues and
task count), testing (each task's saved suite results, then the skipped tests' reasons under "Not exercised here"), risk
(the files the tasks write) and how to revert (the commit the PR lands on the base). A heading naming none of those
takes the ticket's own section of that name. A section the run cannot fill is left empty, so `empty_sections` names
it. Then come the tasks with their `PR:` links, their acceptance criteria, and `Closes #n` for each issue.

Both bodies hold a `## Merge` section before those refs when `<run>/decisions.md` exists: who merges (the App, or the
human's own login), into which branch (a task PR into its ticket's `Branch:`, a ticket PR into `base:` or the run's
default branch), on what proof (a task PR: a green local run of `map.md`'s Checks, since CI and reviews run on ticket
PRs only; a ticket PR: `review_before_agent_merge` and `ci`), and each Authorizations row quoted as recorded.
With no `decisions.md` the section is left out.

`<id>.checks.json` is a list of suites, or `{"suites": [...]}`; each suite has `command` (or `suite`, `name`), `rc`
(or, in older files, a boolean `passed`), and optionally `seconds`, the counts `passed`, `failed` and `skipped`, and
`skip_reasons`. A suite passed when its `rc` is 0; the counts never decide it.
"""
import json
import re

from . import core, decisions, runfolder
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


def _load(path):
    """The suites saved in a `.checks.json` file, or None when it is missing or unreadable."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except ValueError:
        return None
    suites = data.get("suites") if isinstance(data, dict) else data
    return [s for s in suites if isinstance(s, dict)] if isinstance(suites, list) else None


def _count(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _suite_line(s):
    name = s.get("command") or s.get("suite") or s.get("name") or "?"
    if "rc" in s:
        passed = s["rc"] == 0
    elif isinstance(s.get("passed"), bool):
        passed = s["passed"]
    else:
        passed = _count(s.get("failed")) and s["failed"] == 0
    secs = f" in {s['seconds']:g}s" if isinstance(s.get("seconds"), (int, float)) else ""
    counts = ", ".join(f"{s[k]} {k}" for k in ("passed", "failed", "skipped") if _count(s.get(k)))
    return f"- `{name}`: {'passed' if passed else 'failed'}{secs}" + (f" ({counts})" if counts else "")


def _skips(s):
    """The suite's skip reasons as text lines."""
    reasons = s.get("skip_reasons") or []
    if isinstance(reasons, dict):
        reasons = [f"{k}: {v}" for k, v in reasons.items()]
    out = []
    for r in reasons if isinstance(reasons, list) else [reasons]:
        if isinstance(r, dict):
            r = ": ".join(str(v) for v in (r.get("test") or r.get("name"), r.get("reason")) if v)
        if str(r).strip():
            out.append(str(r).strip())
    return out


def _result(run, ident):
    suites = _load(core.resolve(run) / "reports" / f"{ident}.checks.json")
    return "\n".join(_suite_line(s) for s in suites or ()) or NO_RESULT


def base_branch(run, d=None):
    """The branch a ticket PR merges into: `decisions.md`'s `base:`, else the run's default branch."""
    d = d or decisions.load(run)
    return (d and d.get("base")) or runfolder.run_info(run)["Default"]


def merge_section(run, into=None):
    """The `## Merge` section for a PR into `into` (None: a ticket PR, into `base:` or the run's default branch), from
    `<run>/decisions.md`; [] when the file is missing."""
    d = decisions.load(run)
    if d is None:
        return []
    base = base_branch(run, d)
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
        proof = "a green local run (CI is off for this run)" if d.get("ci") == "off" else "green CI"
    rows = [f"- {a.clause} (waived: {a.waived}): {a.quote}" for a in d.authorizations]
    lines = [f"Merges as {who}, into `{into}`, after {review}{proof}.", "",
             "Authorizations:\n" + "\n".join(rows) if rows else "Authorizations: none recorded."]
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


def _reports(run, task_path):
    """The ids a step may have saved the task's results under: `TT-NN` (or `TT-<name>`) and any spelling that names
    the same task file (`7-2` for `07-02`)."""
    reports = core.resolve(run) / "reports"
    found = []
    for f in sorted(reports.glob("*.checks.json")) if reports.is_dir() else ():
        ident = f.name[:-len(".checks.json")]
        try:
            if runfolder.task_file(run, ident) == task_path:
                found.append(f)
        except core.Fail:
            continue
    return found


def _ident(task_path):
    parts = task_path.stem.split("-", 2)
    return "-".join(parts[:2])


def _testing(run, tasks):
    """Each task's saved suite results, then the skipped tests' reasons; "" when no task saved any."""
    rows, skips, any_saved = [], [], False
    for f in tasks:
        suites = [s for path in _reports(run, f) for s in (_load(path) or ())]
        ident = _ident(f)
        rows.append(f"- {ident} {title(f)}:" + ("" if suites else " no saved result."))
        rows += ["  " + _suite_line(s) for s in suites]
        skips += [f"- `{s.get('command') or s.get('suite') or s.get('name') or '?'}` ({ident}): {r}"
                  for s in suites for r in _skips(s)]
        any_saved = any_saved or bool(suites)
    if not any_saved:
        return ""
    text = "Each task's local run of map.md's Checks before its merge:\n\n" + "\n".join(rows)
    return text + ("\n\nNot exercised here:\n" + "\n".join(skips) if skips else "")


def _writes(tasks):
    files = []
    for f in tasks:
        value = clean(runfolder.get(f, "Writes"))
        files += [w.strip().strip("`") for w in value.split(",") if w.strip() and w.strip().lower() != "none"]
    return list(dict.fromkeys(files))


def _ticket_title(ticket_path):
    for line in ticket_path.read_text().splitlines():
        if line.startswith("# "):
            return re.sub(r"^\d+:\s*", "", line[2:].strip())
    return ticket_path.stem


def _fill(name, run, ticket_path, tasks):
    """The text for the template heading `name`, or "" when the run holds nothing for it."""
    key = name.lower()
    if "test" in key or "verif" in key:
        return _testing(run, tasks)
    if "risk" in key or "impact" in key:
        files = _writes(tasks)
        return ("Touches the files its tasks write:\n" + "\n".join(f"- `{w}`" for w in files)) if files else ""
    if "revert" in key or "roll" in key:
        return f"Revert the commit this PR lands on `{base_branch(run)}`: `git revert <commit>`."
    if re.search(r"summary|description|overview|what|why|change|motivation", key):
        refs = ", ".join(f"#{n}" for n in issues(ticket_path))
        count = f"{len(tasks)} task{'s' if len(tasks) != 1 else ''}"
        return f"{_ticket_title(ticket_path)}: delivers {refs} in {count}." if refs else \
            f"{_ticket_title(ticket_path)}, in {count}."
    return section(ticket_path.read_text(), name)


def _criteria(tasks):
    out = []
    for f in tasks:
        lines = [re.sub(r"^(\s*[-*+]\s*)\[[ xX]\]\s*", r"\1", line)
                 for line in section(f.read_text(), "Acceptance criteria").splitlines() if line.strip()]
        if lines:
            out.append(f"### {_ident(f)} {title(f)}\n\n" + "\n".join(lines))
    return "\n\n".join(out)


def ticket_body(run, ticket_path, *, template=None):
    """The ticket PR body: one section per heading of `template` (the repo's PR template text, or None), then the
    tasks with their PR links and acceptance criteria, the `## Merge` section, and `Closes #n`."""
    tasks = ticket_tasks(run, ticket_path)
    names = re.findall(r"^##[ \t]+(.+?)[ \t]*$", template, re.MULTILINE) if template is not None else \
        ["Summary", "Testing"]
    parts = []
    for name in names:
        text = _fill(name, run, ticket_path, tasks)
        parts += [f"## {name}", text or ("" if template is not None else NO_RESULT)]
    rows = [f"- {title(f)}: {clean(runfolder.get(f, 'PR')) or 'no PR'}" for f in tasks]
    parts += ["## Tasks", "\n".join(rows) or "None."]
    criteria = _criteria(tasks)
    if criteria:
        parts += ["## Acceptance criteria", criteria]
    parts += merge_section(run)
    refs = [f"Closes #{n}" for n in issues(ticket_path)]
    if refs:
        parts.append("\n".join(refs))
    return "\n\n".join(p for p in parts if p) + "\n"
