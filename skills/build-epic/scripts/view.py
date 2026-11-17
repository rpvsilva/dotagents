#!/usr/bin/env python3
"""Renders what the human reads during a run, from the run folder alone: nothing an agent wrote reaches them raw.

Usage: python3 -I view.py <run> plan
       python3 -I view.py <run> task <TT-NN>
       python3 -I view.py <run> report

`plan`: the plan to approve (step 5). Each ticket (number, issues, title, blocked by, claimed by) with its tasks
(number, title, blocked by, tier, check); `map.md`'s `Lanes`; the gaps (each task whose `## Check` names GAP, then
`map.md`'s `Gaps`); `spec.md`'s `Open questions` with their assumed answers, as written; the guard rules
(`guard_rules.py`'s lines); the paths of `spec.md` and `map.md`. The orchestrator adds its own judgement below it:
the approval cost and the Authorizations.
`task <TT-NN>`: one stuck task: its ticket's status line (`status.py --line`), then the task's output rendered by
`skills/_shared/protocol/render.py`.
`report`: the run's final report: `status.py --report`, every `blocked` task with its `why`, every `claimed` ticket
with who claimed it, then every follow-up of every agent output (`reports/*.json`, `reviews/*.json`), owner-doc
patches included, grouped by the output it came from.
Prints the Markdown and exits 0, or `FAIL <id> <reason>` and exits 1.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "_shared" / "protocol"))

import protocol  # noqa: E402
import render as protocol_render  # noqa: E402
import status  # noqa: E402
from steplib import runfolder  # noqa: E402


def title(text):
    first = text.splitlines()[0] if text else ""
    return re.sub(r"^#\s*(\S+:\s*)?", "", first).strip()


def section(text, name):
    body = runfolder.section(text, name)
    return body.strip() if body else None


def plan(run):
    r = status.Run(run)
    out = ["# Plan", "", "## Tickets", ""]
    order = sorted(r.tickets, key=lambda n: (not n.isdigit(), int(n) if n.isdigit() else 0, n))
    gaps = []
    for tt in order:
        ident, _, text = r.tickets[tt]
        claimed = runfolder.header(text, "Claimed by")
        out.append(f"- **{ident}** {title(text)} ({runfolder.header(text, 'Issue') or 'no issue'}); "
                   f"blocked by: {runfolder.header(text, 'Blocked by') or 'none'}"
                   + (f"; claimed by {claimed}" if claimed else ""))
        for tid, _, ttext in r.tasks.get(tt, {}).values():
            check = section(ttext, "Check") or "none"
            first = check.splitlines()[0]
            out.append(f"  - {tid} {title(ttext)}; blocked by: {runfolder.header(ttext, 'Blocked by') or 'none'}; "
                       f"tier: {runfolder.header(ttext, 'Tier') or 'low'}; check: {first}")
            if re.search(r"\bGAP\b", check):
                gaps.append(f"- {tid}: {first}")
    mp = (run / "map.md").read_text(encoding="utf-8") if (run / "map.md").is_file() else ""
    spec = (run / "spec.md").read_text(encoding="utf-8") if (run / "spec.md").is_file() else ""
    out += ["", "## Lanes", "", section(mp, "Lanes") or "none"]
    map_gaps = section(mp, "Gaps")
    out += ["", "## Gaps", ""] + (gaps or ["- no task check is a GAP"]) + ([map_gaps] if map_gaps else [])
    out += ["", "## Open questions and assumed answers", "", section(spec, "Open questions") or "none"]
    rules = subprocess.run([sys.executable, "-I", str(HERE / "guard_rules.py"), str(run)],
                           capture_output=True, text=True).stdout.strip()
    out += ["", "## Merge guard rules", "", "\n".join(f"- `{line}`" for line in rules.splitlines()) or "none"]
    out += ["", f"Spec: {run / 'spec.md'}", f"Map: {run / 'map.md'}"]
    return "\n".join(out) + "\n"


def rendered(role, path):
    try:
        out = protocol.parse_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if protocol.check(role, "output", out):
        return None
    return out


def task(run, ident):
    tt = ident.split("-", 1)[0]
    line = subprocess.run([sys.executable, "-I", str(HERE / "status.py"), str(run), "--line", tt],
                          capture_output=True, text=True).stdout.strip()
    path = run / "reports" / f"{ident}.json"
    out = rendered("implementer", path)
    body = protocol_render.render("implementer", out) if out else f"No valid output at {path}.\n"
    return f"{line}\n\n## Task {ident}\n\n{body}"


def role_of(path):
    name = path.stem
    if path.parent.name == "reviews":
        return "security" if name.endswith("-security") else "reviewer"
    if name.startswith("plan-"):
        return "planner"
    if name.startswith("explore-"):
        return "explorer"
    return "implementer"


def report(run):
    table = subprocess.run([sys.executable, "-I", str(HERE / "status.py"), str(run), "--report"],
                           capture_output=True, text=True).stdout.rstrip()
    r = status.Run(run)
    out = ["# Run report", "", table]
    stuck = [f"- task {i} {s}: {why}" for tt in r.tickets for i, s, why in r.stuck(tt)]
    claimed = [f"- ticket {ident} claimed by {runfolder.header(text, 'Claimed by') or 'another developer'}"
               for ident, _, text in r.tickets.values() if r.status(text) == "claimed"]
    if stuck or claimed:
        out += ["", "## Waiting", ""] + stuck + claimed
    follow = []
    for folder in ("reports", "reviews"):
        for path in sorted((run / folder).glob("*.json")) if (run / folder).is_dir() else ():
            if path.name.endswith(".checks.json"):
                continue
            out_obj = rendered(role_of(path), path)
            if not out_obj or not out_obj.get("followups"):
                continue
            follow += ["", f"### {folder}/{path.name}"] + protocol_render.followups(out_obj)[3:]
    if follow:
        out += ["", "## Follow-ups"] + follow
    return "\n".join(out) + "\n"


def main(argv):
    if len(argv) < 2 or argv[1] not in ("plan", "task", "report") or (argv[1] == "task") != (len(argv) == 3):
        print(__doc__.split("\n\n")[1])
        return 2
    run = Path(argv[0]).expanduser().resolve()
    if not run.is_dir():
        print(f"FAIL run no-run-folder: {run}")
        return 1
    if argv[1] == "plan":
        sys.stdout.write(plan(run))
    elif argv[1] == "task":
        sys.stdout.write(task(run, argv[2]))
    else:
        sys.stdout.write(report(run))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
