#!/usr/bin/env python3
"""Renders an agent's output (protocol.py) as Markdown for a human: the only way agent output reaches one.

Usage: python3 -I render.py <role> <output file>

Every role renders its status and `why`, then its own fields: a reviewer's findings, earlier findings, process lines
and follow-ups (the body review-pr posts on a PR); a security reviewer's findings, coverage table and other bugs; an
implementer's commit, tests, suites and follow-ups; a tuner's timings, coverage and mutations; the paths the others
wrote. An output that fails its schema prints `FAIL <role> brief-violation <problems>` and exits 1: nothing
unchecked is rendered.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import protocol  # noqa: E402


def cite(item):
    return f"{item['path']}:{item['line']}" if item.get("line") else item["path"]


def followups(out):
    rows = out.get("followups") or []
    if not rows:
        return []
    lines = ["", "## Follow-ups", ""]
    for f in rows:
        where = f"{f['path']}#{f['item']}" if f["kind"] == "owner-doc" else cite(f)
        lines.append(f"- {f['severity']} {f['kind']} {where}: {f['claim']}")
        if f.get("patch"):
            lines += ["```diff", f["patch"].rstrip("\n"), "```"]
    return lines


def reviewer(out):
    lines = []
    if out.get("earlier"):
        lines += ["## Earlier findings", ""]
        for e in out["earlier"]:
            at = f" ({cite(e)})" if e.get("path") else ""
            lines.append(f"{e['n']}. **{e['verdict']}**{at}: {e['note']}")
        lines.append("")
    findings = sorted(out["findings"], key=lambda f: ["blocker", "should-fix", "nit"].index(f["severity"]))
    lines += ["## Findings", ""]
    if not findings:
        lines.append(f"No findings. Checked: {out['checked']}")
    for i, f in enumerate(findings, 1):
        lines.append(f"{i}. **{f['severity']}** ({f['axis']}) `{cite(f)}`: {f['claim']}")
        lines.append(f"   - Scenario: {f['scenario']}")
        if f.get("rule"):
            lines.append(f"   - Rule: {f['rule']}")
    if findings:
        lines += ["", f"Checked: {out['checked']}"]
    for p in out["process"]:
        lines.append(f"\nprocess: {p}")
    return lines + followups(out)


def security(out):
    axes = ["injection", "secrets", "authz", "privilege", "supply-chain", "unsafe-handling"]
    lines = ["## Coverage" + ("" if out["complete"] else " (incomplete)"), "",
             "| Source | Sinks | " + " | ".join(axes) + " |", "|---|---|" + "---|" * len(axes)]
    for row in out["coverage"]:
        cells = " | ".join(str(row["cells"][a]) for a in axes)
        lines.append(f"| {row['source']} | {', '.join(row['sinks'])} | {cells} |")
    lines += ["", "## Findings", ""]
    if not out["findings"]:
        lines.append("No findings.")
    for f in sorted(out["findings"], key=lambda f: (f["severity"] != "blocker", f["n"])):
        lines += [f"{f['n']}. **{f['severity']}** ({f['axis']}) `{cite(f)}`: {f['claim']}",
                  f"   - Attacker: {f['attacker']}", f"   - Scenario: {f['scenario']}",
                  f"   - Gain: {f['gain']}", f"   - Fix: {f['fix']}"]
    lines += ["", "## Other bugs", ""]
    lines += [f"- `{cite(b)}`: {b['claim']}" for b in out["other_bugs"]] or ["None."]
    for p in out["process"]:
        lines.append(f"\nprocess: {p}")
    return lines


def secs(value):
    return "–" if value is None else f"{value:g} s"


def implementer(out):
    lines = [f"Commit: {out['commit'] or 'none'}", f"Files: {', '.join(out['files']) or 'none'}", "", "## Tests", ""]
    lines += [f"- {t['name']}: {t['criterion']} (went red when: {t['broke']})" for t in out["tests"]] or ["None."]
    lines += ["", "## Suites", "", "| Command | Baseline | Final | Result |", "|---|---|---|---|"]
    lines += [f"| `{s['command']}` | {secs(s.get('baseline_s'))} | {secs(s.get('final_s'))} | "
              f"{'pass' if s['passed'] else 'FAIL'} |" for s in out["suites"]]
    return lines + followups(out)


def tuner(out):
    lines = [f"Commit: {out['commit'] or 'none'}", "", "| Suite | Before | After | Cause |", "|---|---|---|---|"]
    lines += [f"| {t['suite']} | {t['before_s']:g} s | {t['after_s']:g} s | {t['cause']} |" for t in out["time"]]
    lines += ["", "Covered:"] + [f"- {c['entry']}: {c['test']}" for c in out["covered"]]
    lines += ["", "Mutations:"] + [f"- {m['path']}:{m['line']} `{m['old']}` -> `{m['new']}`" for m in out["mutations"]]
    lines += ["", "Moved:"] + [f"- {m['assertion']}: {m['now']}" for m in out["moved"]]
    lines += ["", "Left:"] + [f"- {s['spot']}: {s['why']}" for s in out["left"]]
    return lines


def plain(out):
    items = [(k, v) for k, v in out.items() if k not in ("status", "why")]
    return [f"{k}: {', '.join(map(str, v)) if isinstance(v, list) else v}" for k, v in items]


RENDER = {"reviewer": reviewer, "security": security, "implementer": implementer, "tuner": tuner}


def render(role, out):
    head = [f"Status: {out['status']}" + (f": {out['why']}" if out.get("why") else ""), ""]
    return "\n".join(head + RENDER.get(role, plain)(out)).rstrip() + "\n"


def main(argv):
    if len(argv) != 2:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    role, path = argv
    try:
        out = protocol.parse_json(Path(path).read_text(encoding="utf-8"))
        problems = protocol.check(role, "output", out)
    except KeyError:
        print(f"FAIL {role} unknown-role")
        return 2
    except (OSError, ValueError):
        problems = ["json"]
    if problems:
        print(f"FAIL {role} brief-violation {'; '.join(problems)}")
        return 1
    sys.stdout.write(render(role, out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
