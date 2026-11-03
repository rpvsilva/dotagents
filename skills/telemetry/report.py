#!/usr/bin/env python3
"""Render a telemetry run as a table: one row per span, plus a total row."""
import argparse
import json
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
STALE_DAYS = 30
TOKEN_KINDS = ("input", "output", "cacheRead", "cacheWrite5m", "cacheWrite1h")
HEADERS = ["Span", "Time", "Turns", "Tools", "In", "Out", "Cache R", "Cache W", "Agents", "Cost"]
KEY = "Tools = tool calls · In/Out = input/output tokens · Cache R/W = cache read/write · Agents = subagents"


def load_run(ref):
    return json.load(sys.stdin) if ref == "-" else json.loads(Path(ref).read_text(encoding="utf-8"))


def slug(label):
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")


def saved_runs_dir():
    """.agents/telemetry at the root of the repo containing the current folder, else under it."""
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return Path(top.stdout.strip() if top.returncode == 0 else ".") / ".agents" / "telemetry"


def cost(tokens_by_model, pricing):
    total = 0.0
    for model, tokens in tokens_by_model.items():
        prices = pricing["models"].get(model)
        if prices is None:
            return None
        total += sum(tokens[kind] * prices[kind] for kind in TOKEN_KINDS) / 1_000_000
    return total


def token_total(tokens_by_model, *kinds):
    return sum(t[kind] for t in tokens_by_model.values() for kind in kinds)


def tokens(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1000:.0f}k"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def seconds(n):
    return f"{n} s" if n < 120 else f"{n // 60}m {n % 60:02d}s"


def money(n):
    return "unknown" if n is None else f"${n:.2f}"


def merged_tokens(spans):
    merged = {}
    for span in spans:
        for model, counts in span["tokensByModel"].items():
            into = merged.setdefault(model, dict.fromkeys(TOKEN_KINDS, 0))
            for kind in TOKEN_KINDS:
                into[kind] += counts[kind]
    return merged


def row(name, wall, turns, tool_calls, subagents, tokens_by_model, pricing):
    return [
        name, seconds(wall), str(turns), str(tool_calls),
        tokens(token_total(tokens_by_model, "input")),
        tokens(token_total(tokens_by_model, "output")),
        tokens(token_total(tokens_by_model, "cacheRead")),
        tokens(token_total(tokens_by_model, "cacheWrite5m", "cacheWrite1h")),
        str(subagents), money(cost(tokens_by_model, pricing)),
    ]


def span_row(span, pricing):
    return row(span["name"], span["wallClockSeconds"], span["turns"], span["toolCalls"],
               len(span["subagents"]), span["tokensByModel"], pricing)


def total_row(run, pricing):
    spans = run["spans"]
    # The run's own wall clock: spans can overlap when a background subagent outlasts its span.
    return row("Total", run["wallClockSeconds"], sum(s["turns"] for s in spans),
               sum(s["toolCalls"] for s in spans), sum(len(s["subagents"]) for s in spans),
               merged_tokens(spans), pricing)


def table(body, total=None):
    rows = [HEADERS, *body, *([total] if total else [])]
    widths = [max(len(r[i]) for r in rows) + 2 for i in range(len(HEADERS))]

    def line(left, mid, right):
        return left + mid.join("─" * w for w in widths) + right

    def cells(r):
        return "│" + "│".join(" " + c.ljust(w - 1) for c, w in zip(r, widths)) + "│"

    out = [line("┌", "┬", "┐"), cells(HEADERS), line("├", "┼", "┤"), *(cells(r) for r in body)]
    if total:
        out += [line("├", "┼", "┤"), cells(total)]
    out += [line("└", "┴", "┘"), KEY]
    return "\n".join(out)


def tool_groups(tools):
    """Tool counts with MCP tools ("mcp__server__tool") folded under their server, busiest first."""
    groups = {}
    for name, count in tools.items():
        if name.startswith("mcp__"):
            _, server, tool = name.split("__", 2)
            group = groups.setdefault(f"{server} (MCP)", {"count": 0, "tools": {}})
            group["tools"][tool] = count
        else:
            group = groups.setdefault(name, {"count": 0, "tools": {}})
        group["count"] += count
    return sorted(groups.items(), key=lambda item: -item[1]["count"])


def breakdown(span, pricing):
    calls = []
    for name, group in tool_groups(span["tools"]):
        call = f"{name} {group['count']}"
        if group["tools"]:
            inner = sorted(group["tools"].items(), key=lambda item: -item[1])
            call += " (" + ", ".join(f"{tool} {count}" for tool, count in inner) + ")"
        calls.append(call)
    out = [span["name"], "  Tool calls: " + (", ".join(calls) or "none")]

    agents = sorted(span["subagents"], key=lambda a: -(cost(a["tokensByModel"], pricing) or 0))
    out.append("  Subagents:" + ("" if agents else " none"))
    for agent in agents:
        out.append(f"    {agent['type']} · {agent['description']} · {agent['turns']} turns"
                   f" · {agent['toolCalls']} tool calls · {money(cost(agent['tokensByModel'], pricing))}")
    return "\n".join(out)


def price_check_hint(run, pricing, today):
    """A line asking for a price check of the run's models when prices are stale or missing; else None."""
    models = sorted({model for span in run["spans"] for model in span["tokensByModel"]})
    command = "update_pricing.py " + " ".join(models)
    missing = [model for model in models if model not in pricing["models"]]
    if missing:
        return f"No price for {', '.join(missing)}; check with: {command}"
    if not pricing.get("asOf"):
        return f"Prices have no date; check with: {command}"
    age = (today - date.fromisoformat(pricing["asOf"])).days
    if age > STALE_DAYS:
        return f"Prices are {age} days old ({pricing['asOf']}); check with: {command}"
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", help="run JSON file, or '-' for stdin")
    parser.add_argument("--pricing", default=HERE / "pricing.json")
    parser.add_argument("--save", metavar="LABEL", help="save the run as JSON under this label")
    parser.add_argument("--dir", type=Path, help="saved runs folder (default: <repo root>/.agents/telemetry)")
    parser.add_argument("--today", type=date.fromisoformat, default=date.today(), help=argparse.SUPPRESS)
    args = parser.parse_args()
    # The table uses non-ASCII characters; Windows consoles default to a legacy code page.
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")

    pricing = json.loads(Path(args.pricing).read_text(encoding="utf-8"))
    run = load_run(args.run)
    body = [span_row(span, pricing) for span in run["spans"]]
    print(table(body, total_row(run, pricing) if len(body) > 1 else None))
    for span in run["spans"]:
        print()
        print(breakdown(span, pricing))

    hint = price_check_hint(run, pricing, args.today)
    if hint:
        print(f"\n{hint}")

    if args.save:
        total = cost(merged_tokens(run["spans"]), pricing)
        run = {**run, "label": args.save,
               "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "cost": None if total is None else round(total, 2),
               "pricingAsOf": pricing.get("asOf")}
        folder = args.dir or saved_runs_dir()
        folder.mkdir(parents=True, exist_ok=True)
        # Saved runs hold step labels: keep them out of git without touching the repo's own files.
        ignore = folder / ".gitignore"
        if not ignore.exists():
            ignore.write_text("*\n", encoding="utf-8")
        path = folder / f"{slug(args.save)}.json"
        path.write_text(json.dumps(run, indent=2) + "\n", encoding="utf-8")
        print(f"\nSaved {path}")


if __name__ == "__main__":
    main()
