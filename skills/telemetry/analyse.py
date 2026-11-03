#!/usr/bin/env python3
"""Analyse a skill's steps across saved runs: the slowest, failing, repeated and retried ones."""
import argparse
import difflib
import statistics
import sys
from pathlib import Path

from report import table
from runs import is_number, saved_runs, short_name, skill_matches

SECTIONS = ("Slowest", "Failing", "Repeated", "Retries")
RETRY_WINDOW = 3
NEAR = 0.8
# Their time is the human's (a question) or a whole subagent's run, whose steps are listed too;
# a background step returns at once.
NOT_TIMED = ("AskUserQuestion", "Agent")
WHERE = ["Where", "Tool", "Step"]
TABLES = {
    "Slowest": (WHERE + ["Median", "Max", "Count", "Runs"],
                "Median/Max = seconds per step · Count = steps · Runs = saved runs showing it"),
    "Failing": (WHERE + ["Failed", "Runs"],
                "Failed = failed/total steps · Runs = saved runs where it failed"),
    "Repeated": (WHERE + ["Times", "Runs"],
                 "Times = how often it ran within each run showing it · Runs = saved runs showing it"),
    "Retries": (WHERE + ["Retries", "Runs"],
                "Step = the one that failed, then ran again nearly the same · Runs = saved runs showing it"),
}


def readable_step(step):
    return (isinstance(step, dict) and isinstance(step.get("tool"), str) and isinstance(step.get("label"), str)
            and isinstance(step.get("skill"), (str, type(None))) and isinstance(step.get("subagent"), (str, type(None)))
            and (step.get("seconds") is None or is_number(step.get("seconds"))))


def where(step):
    """The subagent that ran the step, else the skill's short name."""
    return step["subagent"] or short_name(step["skill"])


def key(step):
    return (where(step), step["tool"], step["label"])


def skill_steps(runs, name):
    """([steps of the skill, per run that has steps], runs of the skill without steps)."""
    found, without = [], 0
    for run in runs:
        if not any(skill_matches(s.get("skill"), name) for s in run["spans"]):
            continue
        steps = run.get("steps")
        if not isinstance(steps, list):
            without += 1
            continue
        picked = [s for s in steps if readable_step(s) and skill_matches(s["skill"], name)]
        found.append(sorted(picked, key=lambda s: s.get("start") if isinstance(s.get("start"), str) else ""))
    return found, without


def duration(n):
    if n >= 120:
        return f"{int(n) // 60}m {int(n) % 60:02d}s"
    return f"{n:.1f} s" if n >= 1 else f"{n:.2f} s"


def slowest(runs, top):
    groups = {}
    for i, steps in enumerate(runs):
        for s in steps:
            if s["tool"] in NOT_TIMED or s.get("background") or s["seconds"] is None:
                continue
            group = groups.setdefault(key(s), ([], set()))
            group[0].append(s["seconds"])
            group[1].add(i)
    ranked = sorted(groups.items(), key=lambda g: (-statistics.median(g[1][0]), -max(g[1][0]), g[0]))[:top]
    return [[*k, duration(statistics.median(times)), duration(max(times)), str(len(times)), str(len(seen))]
            for k, (times, seen) in ranked]


def failing(runs):
    groups = {}
    for i, steps in enumerate(runs):
        for s in steps:
            group = groups.setdefault(key(s), [0, 0, set()])
            group[1] += 1
            if s.get("failed") is True:
                group[0] += 1
                group[2].add(i)
    ranked = sorted(((k, g) for k, g in groups.items() if g[0]), key=lambda kg: (-kg[1][0], -kg[1][0] / kg[1][1], kg[0]))
    return [[*k, f"{failed}/{total}", str(len(seen))] for k, (failed, total, seen) in ranked]


def repeated(runs):
    """Steps run twice or more within one run. A step with no label (a question, an MCP call)
    is left out: nothing shows that two of them are the same."""
    groups = {}
    for steps in runs:
        counts = {}
        for s in steps:
            if s["tool"] != "AskUserQuestion" and s["label"]:
                counts[key(s)] = counts.get(key(s), 0) + 1
        for k, n in counts.items():
            if n > 1:
                groups.setdefault(k, []).append(n)
    ranked = sorted(groups.items(), key=lambda g: (-len(g[1]), -max(g[1]), g[0]))
    return [[*k, ", ".join(str(n) for n in sorted(times, reverse=True)), str(len(times))] for k, times in ranked]


def near(a, b):
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= NEAR


def retries(runs):
    """A failed step, then within the next RETRY_WINDOW steps of the same where, the same tool
    with an equal or near-identical label. Steps with no label are left out, as in repeated()."""
    groups = {}
    for i, steps in enumerate(runs):
        by_where = {}
        for s in steps:
            by_where.setdefault(where(s), []).append(s)
        for in_where in by_where.values():
            for j, s in enumerate(in_where):
                if s.get("failed") is not True or not s["label"]:
                    continue
                if any(n["tool"] == s["tool"] and n["label"] and near(s["label"], n["label"])
                       for n in in_where[j + 1:j + 1 + RETRY_WINDOW]):
                    group = groups.setdefault(key(s), [0, set()])
                    group[0] += 1
                    group[1].add(i)
    ranked = sorted(groups.items(), key=lambda g: (-g[1][0], g[0]))
    return [[*k, str(count), str(len(seen))] for k, (count, seen) in ranked]


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skill", help="the skill to analyse (short name works)")
    parser.add_argument("--top", type=int, default=10, help="slowest steps to show (default 10)")
    parser.add_argument("--dir", type=Path, action="append",
                        help="saved runs folder; repeatable (default: ~/.agents/telemetry and <repo root>/.agents/telemetry)")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    runs, unreadable = saved_runs(args.dir)
    steps, without = skill_steps(runs, args.skill)
    notes = []
    if without:
        notes.append(f"Skipped {plural(without, 'saved run')} without steps.")
    if unreadable:
        notes.append(f"Skipped {plural(unreadable, 'saved file')} that are not valid JSON or not readable runs.")

    if not steps and not without:
        print(f"No saved run of {args.skill} found.", file=sys.stderr)
        for note in notes:
            print(note, file=sys.stderr)
        sys.exit(2)

    found = {"Slowest": slowest(steps, args.top), "Failing": failing(steps),
             "Repeated": repeated(steps), "Retries": retries(steps)}
    blocks = [f"Steps of {short_name(args.skill)} across {plural(len(steps), 'saved run')}"]
    for name in SECTIONS:
        headers, legend = TABLES[name]
        blocks.append(f"{name}\n" + (table(found[name], None, headers, legend) if found[name] else "none"))
    print("\n\n".join(blocks))
    if notes:
        print("\n" + "\n".join(notes))


if __name__ == "__main__":
    main()
