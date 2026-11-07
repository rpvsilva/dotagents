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


CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def printable(cell):
    """The cell with each control character shown as \\xNN, so a saved file cannot repaint the terminal."""
    return CONTROL.sub(lambda m: f"\\x{ord(m.group()):02x}", cell)


def table(body, total=None, headers=HEADERS, key=KEY):
    headers = [printable(c) for c in headers]
    body = [[printable(c) for c in r] for r in body]
    total = [printable(c) for c in total] if total else total
    rows = [headers, *body, *([total] if total else [])]
    widths = [max(len(r[i]) for r in rows) + 2 for i in range(len(headers))]

    def line(left, mid, right):
        return left + mid.join("─" * w for w in widths) + right

    def cells(r):
        return "│" + "│".join(" " + c.ljust(w - 1) for c, w in zip(r, widths)) + "│"

    out = [line("┌", "┬", "┐"), cells(headers), line("├", "┼", "┤"), *(cells(r) for r in body)]
    if total:
        out += [line("├", "┼", "┤"), cells(total)]
    out += [line("└", "┴", "┘"), key]
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


ROLE_HEADERS = ["Role", "Runs", "Turns", "Cost", "Avg context"]
ROLE_KEY = ("Runs = subagents of that role · orchestrator = the spans minus their subagents · "
            "Avg context = its cache-read tokens per turn")
# Before schema 4 a subagent had no role: read it from its type, else (a pr-reviewer, an unknown type) from its
# description's keywords. compare_runs.py imports this rule. A conflict resolver is any type told to resolve.
KEYWORD_ROLES = (("re-review", "re-review"), ("ticket", "ticket-review"), ("tuner", "review"),
                 ("security", "security"))
TYPE_ROLES = {"security-reviewer": "security", "pr-reviewer": "review", "epic-implementer": "implement",
              "epic-planner": "plan", "epic-explorer": "explore", "test-tuner": "tune", "pr-fixer": "fix"}


def role_of(agent):
    if isinstance(agent.get("role"), str):
        return agent["role"]
    description = agent.get("description")
    description = description.lower() if isinstance(description, str) else ""
    if "resolve" in description:
        return "resolve"
    if agent.get("type") in TYPE_ROLES and agent.get("type") != "pr-reviewer":
        return TYPE_ROLES[agent["type"]]
    for keyword, role in KEYWORD_ROLES:
        if keyword in description:
            return role
    return TYPE_ROLES.get(agent.get("type"), "other")


def minus(tokens_by_model, others):
    left = {model: dict(counts) for model, counts in tokens_by_model.items()}
    for other in others:
        for model, counts in other.items():
            into = left.setdefault(model, dict.fromkeys(TOKEN_KINDS, 0))
            for kind in TOKEN_KINDS:
                into[kind] -= counts[kind]
    return left


def plus(*costs):
    return None if None in costs else sum(costs)


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def remote_runs(run_folder):
    """(runs, turns, cost, runs without cost) over <run>/remote/*.json; unreadable files count as runs without cost."""
    runs = turns = missing = 0
    total = 0.0
    for path in sorted((run_folder / "remote").glob("*.json")):
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            result = None
        result = result if isinstance(result, dict) else {}
        runs += 1
        turns += result["num_turns"] if is_number(result.get("num_turns")) else 0
        if is_number(result.get("total_cost_usd")):
            total += result["total_cost_usd"]
        else:
            missing += 1
    return runs, turns, total, missing


def by_role(run, pricing, run_folder=None):
    """The cost-by-role table, and a line naming the remote runs without cost (or None)."""
    roles = {}
    for span in run["spans"]:
        for agent in span["subagents"]:
            entry = roles.setdefault(role_of(agent), {"runs": 0, "turns": 0, "tokens": []})
            entry["runs"] += 1
            entry["turns"] += agent["turns"]
            entry["tokens"].append(agent["tokensByModel"])
    agents = [a for span in run["spans"] for a in span["subagents"]]
    own_turns = sum(s["turns"] for s in run["spans"]) - sum(a["turns"] for a in agents)
    own_tokens = minus(merged_tokens(run["spans"]), [a["tokensByModel"] for a in agents])
    own_cost = cost(own_tokens, pricing)
    context = token_total(own_tokens, "cacheRead") // own_turns if own_turns > 0 else 0

    body = [["orchestrator", "1", str(own_turns), money(own_cost), tokens(context)]]
    runs, turns, total = 1, own_turns, own_cost
    priced = []
    for name, entry in roles.items():
        role_cost = plus(*(cost(t, pricing) for t in entry["tokens"]))
        priced.append((name, entry, role_cost))
    priced.sort(key=lambda item: (item[2] is None, -(item[2] or 0), item[0]))
    for name, entry, role_cost in priced:
        body.append([name, str(entry["runs"]), str(entry["turns"]), money(role_cost), ""])
        runs, turns, total = runs + entry["runs"], turns + entry["turns"], plus(total, role_cost)

    note = None
    if run_folder is not None:
        remote, remote_turns, remote_cost, missing = remote_runs(run_folder)
        if remote:
            body.append(["remote", str(remote), str(remote_turns), money(remote_cost), ""])
            runs, turns, total = runs + remote, turns + remote_turns, plus(total, remote_cost)
        if missing:
            note = f"{missing} remote run{'' if missing == 1 else 's'} without cost"
    return table(body, ["Total", str(runs), str(turns), money(total), ""], ROLE_HEADERS, ROLE_KEY), note


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", help="run JSON file, or '-' for stdin")
    parser.add_argument("--pricing", default=HERE / "pricing.json")
    parser.add_argument("--save", metavar="LABEL", help="save the run as JSON under this label")
    parser.add_argument("--dir", type=Path, help="saved runs folder (default: <repo root>/.agents/telemetry)")
    parser.add_argument("--by-role", action="store_true", help="one row per subagent role, plus the orchestrator")
    parser.add_argument("--run-folder", type=Path, help="build-epic run folder whose remote/*.json add a remote row")
    parser.add_argument("--today", type=date.fromisoformat, default=date.today(), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.run_folder and not args.by_role:
        parser.error("--run-folder needs --by-role")
    if args.save and args.by_role:
        parser.error("--save cannot be used with --by-role: save the run without it, then show it --by-role")
    # The table uses non-ASCII characters; Windows consoles default to a legacy code page.
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")

    pricing = json.loads(Path(args.pricing).read_text(encoding="utf-8"))
    run = load_run(args.run)
    if args.by_role:
        out, note = by_role(run, pricing, args.run_folder)
        print(out)
        if note:
            print(f"\n{note}")
        hint = price_check_hint(run, pricing, args.today)
        if hint:
            print(f"\n{hint}")
        return
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
