#!/usr/bin/env python3
"""Extract one telemetry run from a Claude Code session log as neutral run JSON."""
import argparse
import json
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


def same_skill(name, skill):
    # Plugin skills are namespaced ("plugin:skill"); either spelling names the same skill.
    return name == skill or name.endswith(":" + skill)


def invoked_skill(entry):
    """The skill this entry starts, typed as a command or called by the model; else None."""
    content = entry.get("message", {}).get("content")
    if entry.get("type") == "user" and isinstance(content, str):
        match = COMMAND.search(content)
        return match.group(1) if match else None
    for block in blocks(entry):
        if block.get("type") == "tool_use" and block.get("name") == "Skill":
            return block.get("input", {}).get("skill")
    return None


def invokes_skill(entry, skill):
    name = invoked_skill(entry)
    return name is not None and same_skill(name, skill)


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


def subagents(session, entries, tool_ids):
    """Subagents started from the run's tool calls, and from their own tool calls in turn."""
    folder = Path(session).with_suffix("") / "subagents"
    pending = sorted(folder.glob("*.meta.json")) if folder.is_dir() else []
    found = []
    linked = True
    while linked:
        linked = False
        for meta_path in list(pending):
            meta = json.loads(meta_path.read_text())
            if meta.get("toolUseId") not in tool_ids:
                continue
            pending.remove(meta_path)
            log = load(meta_path.with_name(meta_path.name.replace(".meta.json", ".jsonl")))
            numbers, own_ids = measure(log)
            tool_ids |= own_ids
            entries.extend(log)
            found.append({"type": meta.get("agentType"), "description": meta.get("description"), **numbers})
            linked = True
    return found


def add(totals, counts):
    for key, value in counts.items():
        if isinstance(value, dict):
            add(totals.setdefault(key, {}), value)
        else:
            totals[key] = totals.get(key, 0) + value


def find_session(ref, cwd):
    """A session path from a path or ID; with no ref, the newest session of the project in cwd."""
    if ref and Path(ref).is_file():
        return Path(ref)
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
    parser.add_argument("--skill", help="measure from this skill's last invocation")
    parser.add_argument("--cwd", default=Path.cwd(), help="project directory (default: here)")
    args = parser.parse_args()
    args.session = find_session(args.session, Path(args.cwd).resolve())

    entries = load(args.session)
    calls = [i for i, e in enumerate(entries) if invokes_skill(e, "telemetry")]
    end = calls[-1] if calls else len(entries)
    start = calls[-2] + 1 if len(calls) > 1 else 0
    if args.skill:
        found = [i for i, e in enumerate(entries[:end]) if invokes_skill(e, args.skill)]
        if not found:
            ran = sorted({invoked_skill(e) for e in entries[:end]} - {None, "telemetry"})
            print(f"Skill '{args.skill}' did not run in this session. Skills that ran: "
                  + (", ".join(ran) or "none"), file=sys.stderr)
            sys.exit(2)
        start = found[-1]
    window = entries[start:end]

    totals, tool_ids = measure(window)
    timed = list(window)
    agents = subagents(args.session, timed, tool_ids)
    for agent in agents:
        add(totals, {k: agent[k] for k in ("turns", "toolCalls", "tools", "tokensByModel")})

    # Bookkeeping lines (mode changes, summaries) carry no timestamp.
    start_stamp = next(e["timestamp"] for e in window if e.get("timestamp"))
    stamps = [parse_time(e["timestamp"]) for e in timed if e.get("timestamp")]
    run = {
        "schemaVersion": 1,
        "tool": "claude-code",
        "sessionId": Path(args.session).stem,
        "skill": args.skill,
        "start": start_stamp,
        "end": max(stamps).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "wallClockSeconds": round((max(stamps) - parse_time(start_stamp)).total_seconds()),
        **totals,
        "subagents": agents,
    }
    json.dump(run, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
