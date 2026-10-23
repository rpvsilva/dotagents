#!/usr/bin/env python3
"""Render telemetry run JSON as a table; with two runs, compare them side by side."""
import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
STALE_DAYS = 30
TOKEN_KINDS = ("input", "output", "cacheRead", "cacheWrite5m", "cacheWrite1h")


def load_run(path):
    return json.load(sys.stdin) if path == "-" else json.loads(Path(path).read_text())


def cost(run, pricing):
    total = 0.0
    for model, tokens in run["tokensByModel"].items():
        prices = pricing["models"].get(model)
        if prices is None:
            return None
        total += sum(tokens[kind] * prices[kind] for kind in TOKEN_KINDS) / 1_000_000
    return total


def token_total(run, *kinds):
    return sum(t[kind] for t in run["tokensByModel"].values() for kind in kinds)


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


FORMATS = {"seconds": seconds, "count": str, "tokens": tokens, "money": money}


def metrics(run, pricing):
    """(row label, raw value, unit) for each table row."""
    return [
        ("Wall clock", run["wallClockSeconds"], "seconds"),
        ("Turns", run["turns"], "count"),
        ("Tool calls", run["toolCalls"], "count"),
        ("Input tokens", token_total(run, "input"), "tokens"),
        ("Output tokens", token_total(run, "output"), "tokens"),
        ("Cache read", token_total(run, "cacheRead"), "tokens"),
        ("Cache write", token_total(run, "cacheWrite5m", "cacheWrite1h"), "tokens"),
        ("Subagents", len(run["subagents"]), "count"),
        ("Cost", cost(run, pricing), "money"),
    ]


def change(before, after, unit):
    if before is None or after is None:
        return "unknown"
    diff = after - before
    if abs(diff) < (0.005 if unit == "money" else 1):
        return "same"
    sign = "+" if diff > 0 else "-"
    amount = {"seconds": seconds(abs(diff)), "count": str(abs(diff)),
              "tokens": tokens(abs(diff)), "money": f"${abs(diff):.2f}"}[unit]
    percent = f" ({sign}{abs(diff) / before * 100:.0f}%)" if before else ""
    return f"{sign}{amount}{percent}"


def header(run):
    return run.get("label") or f"{run.get('skill') or 'session'} {run['sessionId'][:8]}"


def table(headers, body):
    widths = [max(len(row[i]) for row in [headers, *body]) + 2 for i in range(len(headers))]

    def line(left, mid, right):
        return left + mid.join("─" * w for w in widths) + right

    def cells(row, centre=False):
        return "│" + "│".join(
            (f"{c:^{w}}" if centre and i else " " + c.ljust(w - 1)) for i, (c, w) in enumerate(zip(row, widths))
        ) + "│"

    out = [line("┌", "┬", "┐"), cells(headers, centre=True)]
    for row in body:
        out += [line("├", "┼", "┤"), cells(row)]
    out.append(line("└", "┴", "┘"))
    return "\n".join(out)


def tool_groups(tools):
    """Tool counts with MCP tools ("mcp__server__tool") folded under their server."""
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


def breakdown(run, pricing):
    groups = tool_groups(run["tools"])
    out = ["Tool calls"]
    if groups:
        name_width = max(len(name) for name, _ in groups)
        count_width = max(len(str(g["count"])) for _, g in groups)
        for name, group in groups:
            line = f"  {name:<{name_width}}  {group['count']:>{count_width}}"
            if group["tools"]:
                inner = sorted(group["tools"].items(), key=lambda item: -item[1])
                line += "  (" + ", ".join(f"{tool} {count}" for tool, count in inner) + ")"
            out.append(line)
    else:
        out.append("  none")

    agents = sorted(run["subagents"], key=lambda a: -(cost(a, pricing) or 0))
    if not agents:
        out.append("Subagents: none")
    else:
        out.append("Subagents (by cost)")
        for agent in agents:
            out.append(f"  {agent['type']} · {agent['description']} · {agent['turns']} turns"
                       f" · {agent['toolCalls']} tool calls · {money(cost(agent, pricing))}")
    return "\n".join(out)


def price_check_hint(runs, pricing, today):
    """A line asking for a price check when prices are stale or a model has none; else None."""
    models = sorted({model for run in runs for model in run["tokensByModel"]})
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
    parser.add_argument("runs", nargs="+", help="one or two run JSON files ('-' reads stdin)")
    parser.add_argument("--pricing", default=HERE / "pricing.json")
    parser.add_argument("--save", metavar="LABEL", help="save the last run as JSON under this label")
    parser.add_argument("--dir", default=".agents/telemetry", help="where --save writes")
    parser.add_argument("--today", type=date.fromisoformat, default=date.today(), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if len(args.runs) > 2:
        parser.error("give one run, or two to compare")

    pricing = json.loads(Path(args.pricing).read_text())
    runs = [load_run(path) for path in args.runs]
    if args.save:
        at_save = cost(runs[-1], pricing)
        runs[-1] = {**runs[-1], "label": args.save,
                    "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "costAtSave": None if at_save is None else round(at_save, 2),
                    "pricingAsOf": pricing.get("asOf")}
    columns = [metrics(run, pricing) for run in runs]
    headers = ["", *(header(run) for run in runs)]
    body = [[label, *(FORMATS[unit](col[i][1]) for col in columns)]
            for i, (label, _, unit) in enumerate(columns[0])]
    if len(runs) == 2:
        headers.append("Change")
        for row, before, after in zip(body, *columns):
            row.append(change(before[1], after[1], before[2]))
    print(table(headers, body))

    # Saved runs are repriced at today's rates so columns compare like for like.
    for run in runs:
        now = cost(run, pricing)
        if run.get("costAtSave") is not None and now is not None and abs(now - run["costAtSave"]) >= 0.005:
            print(f"{header(run)} cost {money(run['costAtSave'])} at {run.get('pricingAsOf')} prices;"
                  f" the table uses {pricing.get('asOf')} prices.")

    for run in runs:
        print()
        if len(runs) > 1:
            print(header(run))
        print(breakdown(run, pricing))

    hint = price_check_hint(runs, pricing, args.today)
    if hint:
        print(f"\n{hint}")

    if args.save:
        folder = Path(args.dir)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (re.sub(r"[^a-z0-9]+", "-", args.save.lower()).strip("-") + ".json")
        path.write_text(json.dumps(runs[-1], indent=2) + "\n")
        print(f"\nSaved {path}")


if __name__ == "__main__":
    main()
