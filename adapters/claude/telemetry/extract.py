#!/usr/bin/env python3
"""Extract one telemetry run from a Claude Code session log as neutral run JSON."""
import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path


def load(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


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


def measure(entries):
    turns = {}
    tool_ids = {}
    for entry in entries:
        if entry.get("type") != "assistant" or not entry.get("requestId"):
            continue
        turns[entry["requestId"]] = entry["message"]
        for block in blocks(entry):
            if block.get("type") == "tool_use":
                tool_ids[block["id"]] = block["name"]

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
    }, set(tool_ids)


def load_subagents(session):
    """(meta, log) for every subagent the session started."""
    folder = Path(session).with_suffix("") / "subagents"
    metas = sorted(folder.glob("*.meta.json")) if folder.is_dir() else []
    return [(json.loads(m.read_text()), load(m.with_name(m.name.replace(".meta.json", ".jsonl")))) for m in metas]


def linked_subagents(pool, tool_ids):
    """Subagents started by these tool calls, and by their own tool calls in turn, as (meta, numbers, log)."""
    known = set(tool_ids)
    found = []
    pending = list(pool)
    linked = True
    while linked:
        linked = False
        for meta, log in list(pending):
            if meta.get("toolUseId") in known:
                pending.remove((meta, log))
                numbers, own_ids = measure(log)
                known |= own_ids
                found.append((meta, numbers, log))
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


def span(entries, skill, pool):
    """Numbers for one span, its subagents included; None when nothing happened in it."""
    totals, tool_ids = measure(entries)
    agents = linked_subagents(pool, tool_ids)
    if totals["turns"] == 0 and not agents:
        return None
    timed = [e for e in entries if e.get("timestamp")] + [e for *_, log in agents for e in log if e.get("timestamp")]
    for _, numbers, _ in agents:
        add(totals, {k: numbers[k] for k in ("turns", "toolCalls", "tools", "tokensByModel")})
    start = parse_time(next(e["timestamp"] for e in entries if e.get("timestamp")))
    end = max(parse_time(e["timestamp"]) for e in timed)
    return {
        "skill": skill,
        "start": stamp(start),
        "end": stamp(end),
        "wallClockSeconds": round((end - start).total_seconds()),
        **totals,
        "subagents": [{"type": meta.get("agentType"), "description": meta.get("description"), **numbers}
                      for meta, numbers, _ in agents],
    }


def find_session(ref, cwd):
    """A session path from a path or ID; with no ref, the session Claude Code is running, else the newest."""
    if ref and Path(ref).is_file():
        return Path(ref)
    ref = ref or os.environ.get("CLAUDE_CODE_SESSION_ID")
    project = Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9-]", "-", str(cwd))
    if ref:
        return project / f"{ref}.jsonl"
    sessions = sorted(project.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    if not sessions:
        sys.exit(f"No Claude Code sessions found in {project}")
    return sessions[-1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", help="session .jsonl path or ID (default: current session)")
    parser.add_argument("--skill", help="report only this skill's last span")
    parser.add_argument("--cwd", default=Path.cwd(), help="project directory (default: here)")
    args = parser.parse_args()
    args.session = find_session(args.session, Path(args.cwd).resolve())

    entries = load(args.session)
    skills = {loaded_skill(e) for e in entries} - {None}
    stretches = segment(entries, skills)
    # The last telemetry stretch is this request; everything before it is measurable.
    reports = [i for i, (skill, _, _) in enumerate(stretches) if skill == "telemetry"]
    if reports:
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
        chosen = stretches[reports[-1] + 1:] if reports else stretches

    pool = load_subagents(args.session)
    spans = []
    for skill, first, last in chosen:
        numbers = span(entries[first:last], skill, pool)
        if numbers:
            spans.append(numbers)
    if not spans:
        print("Nothing to measure: no activity since the previous telemetry report.", file=sys.stderr)
        sys.exit(2)

    # Name spans by skill without its plugin prefix; repeats become renovate-prs-2, -3, ...
    seen = {}
    for i, s in enumerate(spans):
        short = s["skill"].split(":")[-1] if s["skill"] else "no skill"
        seen[short] = seen.get(short, 0) + 1
        spans[i] = {"name": short if seen[short] == 1 else f"{short}-{seen[short]}", **s}

    window = entries[chosen[0][1]:chosen[-1][2]]
    start = parse_time(spans[0]["start"] if args.skill else next(e["timestamp"] for e in window if e.get("timestamp")))
    end = max(parse_time(s["end"]) for s in spans)
    json.dump({
        "schemaVersion": 2,
        "tool": "claude-code",
        "sessionId": Path(args.session).stem,
        "skill": args.skill,
        "start": stamp(start),
        "end": stamp(end),
        "wallClockSeconds": round((end - start).total_seconds()),
        "spans": spans,
    }, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
