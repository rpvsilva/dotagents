#!/usr/bin/env python3
"""Compare a skill's saved runs over time: one row per run, oldest to newest, then the change."""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

from report import HERE, cost, merged_tokens, money, seconds, table, token_total, tokens
from runs import NO_SKILL, run_date, saved_runs, short_name, skill_matches

HEADERS = ["Date", "Cost", "Time", "Active", "Turns", "Tools", "In", "Out", "Cache R", "Cache W"]
KEY = "Active = time minus stalls over 15 min (unknown for older saves) · Tools = tool calls · In/Out = input/output tokens · Cache R/W = cache read/write"


def figures(spans, pricing):
    """Cost, then each counted figure, of these spans summed."""
    merged = merged_tokens(spans)
    return [
        cost(merged, pricing),
        sum(s["wallClockSeconds"] for s in spans),
        active(spans),
        sum(s["turns"] for s in spans),
        sum(s["toolCalls"] for s in spans),
        token_total(merged, "input"),
        token_total(merged, "output"),
        token_total(merged, "cacheRead"),
        token_total(merged, "cacheWrite5m", "cacheWrite1h"),
    ]


def active(spans):
    """Active seconds of these spans summed; None when a span was saved before they were measured."""
    values = [s.get("activeSeconds") for s in spans]
    return None if any(v is None for v in values) else sum(values)


def maybe_seconds(n):
    return "unknown" if n is None else seconds(n)


FORMATS = [money, seconds, maybe_seconds, str, str, tokens, tokens, tokens, tokens]


def cells(values):
    return [fmt(value) for fmt, value in zip(FORMATS, values)]


def change(first, last):
    out = []
    for fmt, a, b in zip(FORMATS, first, last):
        if a is None or b is None:
            out.append("unknown")
            continue
        delta = b - a
        sign = "+" if delta > 0 else "-" if delta < 0 else ""
        cell = sign + fmt(abs(delta))
        if a:
            cell += f" ({delta / a * 100:+.0f}%)"
        out.append(cell)
    return out


def by_skill(runs, name, since, until):
    """{skill's short name: [(date, spans of that skill in the run)]}, runs in the order given.
    "plug:deploy" and "deploy" are one skill, as skill_matches says."""
    grouped = {}
    for run in runs:
        day = run_date(run)
        if (since and day < since) or (until and day > until):
            continue
        spans = {}
        for span in run["spans"]:
            if name is None or skill_matches(span.get("skill"), name):
                spans.setdefault(short_name(span.get("skill")), []).append(span)
        for skill, picked in spans.items():
            grouped.setdefault(skill, []).append((day, picked))
    return grouped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skill", nargs="?", help="only this skill (short name works)")
    parser.add_argument("--since", type=date.fromisoformat, help="first run date to show, YYYY-MM-DD")
    parser.add_argument("--until", type=date.fromisoformat, help="last run date to show, YYYY-MM-DD")
    parser.add_argument("--dir", type=Path, action="append",
                        help="saved runs folder; repeatable (default: ~/.agents/telemetry and <repo root>/.agents/telemetry)")
    parser.add_argument("--pricing", default=HERE / "pricing.json")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    pricing = json.loads(Path(args.pricing).read_text(encoding="utf-8"))
    runs, skipped = saved_runs(args.dir)
    since = args.since.isoformat() if args.since else None
    until = args.until.isoformat() if args.until else None
    grouped = by_skill(runs, args.skill, since, until)
    note = f"Skipped {skipped} saved file{'s' if skipped != 1 else ''} that are not valid JSON or not readable runs."

    if not grouped:
        what = f"of {args.skill} " if args.skill else ""
        print(f"No saved run {what}matches.", file=sys.stderr)
        if skipped:
            print(note, file=sys.stderr)
        sys.exit(2)

    blocks = []
    for skill in sorted(grouped, key=lambda s: (s == NO_SKILL, s)):
        values = [figures(spans, pricing) for _, spans in grouped[skill]]
        body = [[day, *cells(v)] for (day, _), v in zip(grouped[skill], values)]
        total = ["change", *change(values[0], values[-1])] if len(values) > 1 else None
        blocks.append(f"{skill}\n{table(body, total, HEADERS, KEY)}")
    print("\n\n".join(blocks))
    if skipped:
        print(f"\n{note}")


if __name__ == "__main__":
    main()
