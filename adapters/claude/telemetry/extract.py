#!/usr/bin/env python3
"""Extract one telemetry run from a Claude Code session log as neutral run JSON."""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def load(path):
    """The log's entries. A line anywhere in the log that is not a JSON object (half-written at session end, cut
    inside a UTF-8 character, a bare number or list) is skipped, so a damaged log never stops a save."""
    entries = []
    with open(path, "rb") as f:
        for line in f:
            try:
                entry = json.loads(line.decode("utf-8"))
            except ValueError:  # UnicodeDecodeError is a ValueError too
                continue
            if isinstance(entry, dict):
                entries.append(entry)
    return entries


def load_meta(path):
    """A subagent's meta, or None when it is not a JSON object (half-written at session end)."""
    try:
        meta = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) else None


def blocks(entry):
    content = entry.get("message", {}).get("content")
    return content if isinstance(content, list) else []


COMMAND = re.compile(r"<command-name>/([^<]+)</command-name>")
LOADED = "Base directory for this skill:"


def same_skill(name, skill):
    # Plugin skills are namespaced ("plugin:skill"); either spelling names the same skill.
    return name == skill or name.endswith(":" + skill)


def typed_command(entry):
    """The /command the user typed in this entry; else None."""
    content = entry.get("message", {}).get("content")
    match = COMMAND.search(content) if typed(entry) else None
    return match.group(1) if match else None


def skill_call(entry):
    """The skill the model started with its Skill tool in this entry; else None."""
    for block in blocks(entry):
        if block.get("type") == "tool_use" and block.get("name") == "Skill":
            return block.get("input", {}).get("skill")
    return None


def loaded_skill(entry):
    """The skill whose instructions this entry loads ("Base directory for this skill: .../<name>"); else None."""
    for block in blocks(entry):
        text = block.get("text", "") if block.get("type") == "text" else ""
        if text.startswith(LOADED):
            return Path(text[len(LOADED):].splitlines()[0].strip()).name
    return None


def typed(entry):
    """A message the user typed (a prompt or a command), as opposed to tool results and loaded text."""
    return entry.get("type") == "user" and isinstance(entry.get("message", {}).get("content"), str)


def parse_time(stamp):
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


# Claude Code's tools, keyed by lowercase name: a log can hold "bash" beside "Bash", and both count as "Bash".
TOOLS = {name.lower(): name for name in (
    "Agent", "AskUserQuestion", "Bash", "BashOutput", "Edit", "ExitPlanMode", "Glob", "Grep", "KillShell",
    "NotebookEdit", "Read", "Skill", "SlashCommand", "Task", "TodoWrite", "WebFetch", "WebSearch", "Write")}


def tool_name(name):
    """The tool's canonical name; None for a placeholder call (bash_placeholder), which did no work."""
    if not isinstance(name, str) or name.endswith("_placeholder"):
        return None
    return TOOLS.get(name.lower(), name)


def measure(entries):
    turns = {}
    tool_ids = {}
    started = set()
    for entry in entries:
        if entry.get("type") != "assistant" or not entry.get("requestId"):
            continue
        turns[entry["requestId"]] = entry["message"]
        for block in blocks(entry):
            if block.get("type") == "tool_use":
                started.add(block["id"])
                name = tool_name(block.get("name"))
                if name:
                    tool_ids[block["id"]] = name

    tokens = {}
    for message in turns.values():
        usage = message.get("usage", {})
        split = usage.get("cache_creation", {})
        model = tokens.setdefault(message["model"], {
            "input": 0, "output": 0, "cacheRead": 0, "cacheWrite5m": 0, "cacheWrite1h": 0,
        })
        model["input"] += usage.get("input_tokens", 0)
        model["output"] += usage.get("output_tokens", 0)
        model["cacheRead"] += usage.get("cache_read_input_tokens", 0)
        model["cacheWrite5m"] += split.get("ephemeral_5m_input_tokens", 0)
        model["cacheWrite1h"] += split.get("ephemeral_1h_input_tokens", 0)

    tools = {}
    for name in tool_ids.values():
        tools[name] = tools.get(name, 0) + 1

    return {
        "turns": len(turns),
        "toolCalls": len(tool_ids),
        "tools": tools,
        "tokensByModel": tokens,
    }, started


# Role names a subagent's description starts with ("review: PR 12"); build-epic starts its subagents with them.
ROLES = ("implement", "review", "re-review", "ticket-review", "security", "fix", "resolve", "tune", "plan", "explore")


def role(description):
    """The role a description's prefix names; "other" when it names none."""
    prefix, colon, _ = description.partition(":") if isinstance(description, str) else ("", "", "")
    return prefix if colon and prefix in ROLES else "other"


# Today's prices, from the repo the extractor sits in; none in a sandbox holding only this script.
PRICING = Path(__file__).resolve().parents[3] / "skills" / "telemetry" / "pricing.json"
TOKEN_KINDS = ("input", "output", "cacheRead", "cacheWrite5m", "cacheWrite1h")


def load_pricing(path=PRICING):
    """The price table, or None when it is missing or not the expected shape."""
    try:
        pricing = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(pricing, dict) or not isinstance(pricing.get("models"), dict):
        return None
    return pricing


def cost(tokens_by_model, pricing):
    """USD for these tokens; None without prices or when one model, or one of its kinds, has no price."""
    if pricing is None:
        return None
    total = 0.0
    for model, tokens in tokens_by_model.items():
        prices = pricing["models"].get(model)
        if not isinstance(prices, dict):
            return None
        for kind in TOKEN_KINDS:
            price = prices.get(kind)
            if not isinstance(price, (int, float)) or isinstance(price, bool):
                return None
            total += tokens[kind] * price
    return round(total / 1_000_000, 6)


# The repo's secret rules (see its header); label lines apply only here, case-insensitive.
PATTERNS = Path(__file__).resolve().parents[3] / "scripts" / "hooks" / "secret-patterns.txt"
LABEL_LENGTH = 80


def secret_rules(path=PATTERNS):
    """(compiled rule, is a label rule) for each token and label line of the patterns file; None when it cannot be read."""
    flags = {"token": 0, "label": re.I}
    try:
        lines = [line.partition(" ") for line in path.read_text(encoding="utf-8").splitlines()]
        rules = [(re.compile(rule, flags[name]), name == "label") for name, _, rule in lines if name in flags]
    except (OSError, UnicodeDecodeError, re.error):
        return None
    return rules or None


def mask(rule, is_label, text):
    """Text with each match of rule as ***; a label rule with groups masks only its last group (the value)."""
    def stars(m):
        if not is_label or m.lastindex is None:
            return "***"
        whole, (start, end) = m.group(0), m.span(m.lastindex)
        return whole[:start - m.start()] + "***" + whole[end - m.start():]
    return rule.sub(stars, text)


def label(tool, given, rules):
    """A step's short label: the first non-blank line of the tool's label input, masked, then cut. Only these keys are read."""
    if tool == "Bash":
        text = given.get("command")
    elif tool in ("Read", "Write", "Edit", "NotebookEdit"):
        text = given.get("file_path")
    elif tool in ("Grep", "Glob"):
        text = given.get("pattern")
        if text is not None and given.get("path"):
            text = f"{text} in {given['path']}"
    elif tool == "WebFetch":
        text = given.get("url")
    elif tool == "WebSearch":
        text = given.get("query")
    elif tool == "Skill":
        text = given.get("skill")
    elif tool == "Agent":
        text = ": ".join(str(v) for v in (given.get("subagent_type"), given.get("description")) if v)
    else:
        text = None
    if not isinstance(text, str) or not text or rules is None:
        return ""
    text = next((line for line in text.splitlines() if line.strip()), "")
    for rule, is_label in rules:
        text = mask(rule, is_label, text)
    return text if len(text) <= LABEL_LENGTH else text[:LABEL_LENGTH - 1] + "…"


def results(logs):
    """{tool_use_id: (time, is_error)} for every tool result in these logs."""
    found = {}
    for log in logs:
        for entry in log:
            if entry.get("type") != "user" or not entry.get("timestamp"):
                continue
            for block in blocks(entry):
                if block.get("type") == "tool_result" and block.get("tool_use_id"):
                    found.setdefault(block["tool_use_id"], (parse_time(entry["timestamp"]), block.get("is_error") is True))
    return found


def steps(entries, done, rules, subagent=None, subagent_id=None):
    """One step per tool call in these entries, timed by its result in done; no span set yet.
    subagent is the subagent's type, shown in reports; subagent_id tells two of one type apart."""
    found = {}
    for entry in entries:
        if entry.get("type") != "assistant" or not entry.get("timestamp"):
            continue
        start = parse_time(entry["timestamp"])
        for block in blocks(entry):
            if block.get("type") != "tool_use" or block.get("id") in found:
                continue
            tool = tool_name(block.get("name", ""))
            if tool is None:
                continue
            given = block.get("input") if isinstance(block.get("input"), dict) else {}
            end, failed = done.get(block.get("id"), (None, False))
            step = {
                "tool": tool,
                "label": label(tool, given, rules),
                "start": start,
                "seconds": round((end - start).total_seconds(), 3) if end else None,
                "failed": failed,
                "subagent": subagent,
                "subagentId": subagent_id,
            }
            if tool == "Bash" and given.get("run_in_background") is True:
                step["background"] = True
            found[block.get("id")] = step
    return list(found.values())


def load_subagents(session):
    """(meta, log, id) for every subagent the session started, id its log's stem (agent-f);
    one whose meta is unreadable is left out."""
    folder = Path(session).with_suffix("") / "subagents"
    found = []
    for m in sorted(folder.glob("*.meta.json")) if folder.is_dir() else []:
        meta = load_meta(m)
        log = m.with_name(m.name.replace(".meta.json", ".jsonl"))
        if meta is not None:
            found.append((meta, load(log) if log.is_file() else [], log.stem))
    return found


def linked_subagents(pool, tool_ids):
    """Subagents started by these tool calls, and by their own tool calls in turn, as (meta, numbers, log, id)."""
    known = set(tool_ids)
    found = []
    pending = list(pool)
    linked = True
    while linked:
        linked = False
        for meta, log, agent_id in list(pending):
            if meta.get("toolUseId") in known:
                pending.remove((meta, log, agent_id))
                numbers, own_ids = measure(log)
                known |= own_ids
                found.append((meta, numbers, log, agent_id))
                linked = True
    return found


def add(totals, counts):
    for key, value in counts.items():
        if isinstance(value, dict):
            add(totals.setdefault(key, {}), value)
        else:
            totals[key] = totals.get(key, 0) + value


def stamp(moment):
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def segment(entries, skills):
    """Split entries into [skill, first, last+1] stretches. A typed skill starts one; so does a skill the
    model starts outside any skill. "telemetry" marks a report's own stretch, which the next typed
    message closes. Built-in commands (/clear, /model) load no skill instructions and start nothing."""
    spans = [[None, 0]]
    for i, entry in enumerate(entries):
        current = spans[-1][0]
        name = typed_command(entry)
        if name and not any(same_skill(name, s) for s in skills | {"telemetry"}):
            name = None
        name = name or (skill_call(entry) if current is None else None)
        if name and same_skill(name, "telemetry"):
            spans.append(["telemetry", i])
        elif name:
            spans.append([name, i])
        elif current == "telemetry" and typed(entry):
            spans.append([None, i])
    ends = [start for _, start in spans[1:]] + [len(entries)]
    return [[skill, start, end] for (skill, start), end in zip(spans, ends)]


def span(entries, skill, pool, done, rules, pricing):
    """Numbers for one span, its subagents included, with its steps; None when nothing happened in it."""
    totals, tool_ids = measure(entries)
    agents = linked_subagents(pool, tool_ids)
    if totals["turns"] == 0 and not agents:
        return None
    own = steps(entries, done, rules) + [s for meta, _, log, agent_id in agents
                                         for s in steps(log, done, rules, meta.get("agentType"), agent_id)]
    timed = [e for e in entries if e.get("timestamp")] + [e for _, _, log, _ in agents for e in log if e.get("timestamp")]
    for _, numbers, _, _ in agents:
        add(totals, {k: numbers[k] for k in ("turns", "toolCalls", "tools", "tokensByModel")})
    start = parse_time(next(e["timestamp"] for e in entries if e.get("timestamp")))
    end = max(parse_time(e["timestamp"]) for e in timed)
    return {
        "skill": skill,
        "start": stamp(start),
        "end": stamp(end),
        "wallClockSeconds": round((end - start).total_seconds()),
        **totals,
        "subagents": [{"type": meta.get("agentType"), "description": meta.get("description"),
                       "role": role(meta.get("description")), **numbers,
                       "cost": cost(numbers["tokensByModel"], pricing)}
                      for meta, numbers, _, _ in agents],
        "steps": own,
    }


def find_session(ref, cwd):
    """A session path from a path or ID; with no ref, the session Claude Code is running, else the newest."""
    if ref and Path(ref).is_file():
        return Path(ref)
    ref = ref or os.environ.get("CLAUDE_CODE_SESSION_ID")
    projects = Path.home() / ".claude" / "projects"
    project = projects / re.sub(r"[^A-Za-z0-9-]", "-", str(cwd))
    if ref:
        # The shell may have left the folder the session started in, so look in every project.
        here = project / f"{ref}.jsonl"
        return here if here.is_file() else next(projects.glob(f"*/{ref}.jsonl"), here)
    sessions = sorted(project.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    if not sessions:
        sys.exit(f"No Claude Code sessions found in {project}")
    return sessions[-1]


def repo_of(cwd):
    """The git top-level folder holding cwd, in the OS's native form; None outside a repo or without git.

    Git for Windows prints C:/Users/...; Path turns that into C:\\Users\\..., the form every other
    path takes, so runs from one repo always carry the same repo string.
    """
    try:
        proc = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
                              capture_output=True, encoding="utf-8")
    except OSError:
        return None
    top = proc.stdout.strip()
    return str(Path(top)) if top else None


def claude_versions(entries):
    """Each Claude Code version the entries were written by (their `version`), once, in the order first seen."""
    return list(dict.fromkeys(e["version"] for e in entries if isinstance(e.get("version"), str)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", help="session .jsonl path or ID (default: current session)")
    parser.add_argument("--skill", help="report only this skill's last span")
    parser.add_argument("--cwd", default=Path.cwd(), help="project directory (default: here)")
    parser.add_argument("--whole", action="store_true",
                        help="measure the whole session; earlier telemetry reports become ordinary spans")
    args = parser.parse_args()
    args.session = find_session(args.session, Path(args.cwd).resolve())

    entries = load(args.session)
    pool = load_subagents(args.session)
    # Results are looked up across the whole session: one can land in the next span.
    done = results([entries] + [log for _, log, _ in pool])
    rules = secret_rules()
    pricing = load_pricing()
    skills = {loaded_skill(e) for e in entries} - {None}
    stretches = segment(entries, skills)
    # The last telemetry stretch is this request; everything before it is measurable.
    reports = [i for i, (skill, _, _) in enumerate(stretches) if skill == "telemetry"]
    if reports and not args.whole:
        entries = entries[:stretches[reports[-1]][1]]
        stretches = stretches[:reports[-1]]
        reports = reports[:-1]

    if args.skill:
        matching = [s for s in stretches if s[0] not in (None, "telemetry") and same_skill(s[0], args.skill)]
        if not matching:
            ran = sorted({s[0] for s in stretches} - {None, "telemetry"})
            print(f"Skill '{args.skill}' did not run in this session. Skills that ran: "
                  + (", ".join(ran) or "none"), file=sys.stderr)
            sys.exit(2)
        chosen = matching[-1:]
    else:
        chosen = stretches[reports[-1] + 1:] if reports and not args.whole else stretches

    spans = []
    for skill, first, last in chosen:
        numbers = span(entries[first:last], skill, pool, done, rules, pricing)
        if numbers:
            spans.append(numbers)
    if not spans:
        print("Nothing to measure: no activity since the previous telemetry report.", file=sys.stderr)
        sys.exit(2)

    # Name spans by skill without its plugin prefix; repeats become renovate-prs-2, -3, ...
    seen = {}
    all_steps = []
    for i, s in enumerate(spans):
        short = s["skill"].split(":")[-1] if s["skill"] else "no skill"
        seen[short] = seen.get(short, 0) + 1
        name = short if seen[short] == 1 else f"{short}-{seen[short]}"
        all_steps += [{**step, "span": name, "skill": s["skill"]} for step in s.pop("steps")]
        spans[i] = {"name": name, **s}
    all_steps.sort(key=lambda step: step["start"])
    keys = ("tool", "label", "start", "seconds", "failed", "span", "skill", "subagent", "subagentId", "background")
    all_steps = [{k: stamp(step[k]) if k == "start" else step[k] for k in keys if k in step} for step in all_steps]

    window = entries[chosen[0][1]:chosen[-1][2]]
    start = parse_time(spans[0]["start"] if args.skill else next(e["timestamp"] for e in window if e.get("timestamp")))
    end = max(parse_time(s["end"]) for s in spans)
    json.dump({
        "schemaVersion": 4,
        "tool": "claude-code",
        "sessionId": Path(args.session).stem,
        "skill": args.skill,
        "repo": repo_of(Path(args.cwd).resolve()),
        "date": stamp(start)[:10],
        "whole": args.whole,
        "start": stamp(start),
        "end": stamp(end),
        "wallClockSeconds": round((end - start).total_seconds()),
        "pricingAsOf": pricing.get("asOf") if pricing else None,
        "claudeVersions": claude_versions(window),
        "spans": spans,
        "steps": all_steps,
    }, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
