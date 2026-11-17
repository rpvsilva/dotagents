#!/usr/bin/env python3
"""Checks a subagent's output and final message by the agent protocol (skills/_shared/protocol).

Usage: python3 -I report_check.py <run> <id> < message
       python3 -I report_check.py <run> <id> --from-report

The output is `<run>/reports/<id>.json`, checked against its role's output schema: `planner` for `plan-*`,
`explorer` for `explore-*`, `implementer` for a task. The final message, on stdin, must be at most 400 characters
and `{"status": <the output's status>, "output": <a path>}`. `--from-report` checks the output alone: a remote
subagent's final message is its output, saved there by the orchestrator.

Prints `OK <id> <status>`, or `FAIL <id> brief-violation <problems> (log: <path>)`, the problems as check.py names
them (`no-output`, `json`, `length=<n>`, `status-mismatch`, `<json path>: <problem>`).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared" / "protocol"))

import protocol  # noqa: E402
from steplib import core  # noqa: E402


def role_of(ident):
    if ident.startswith("plan-"):
        return "planner"
    if ident.startswith("explore-"):
        return "explorer"
    return "implementer"


def step(ctx):
    if ctx.args not in ([], ["--from-report"]):
        raise core.Fail("usage: report_check.py <run> <id> [--from-report]")
    role, path = role_of(ctx.id), ctx.run_dir / "reports" / f"{ctx.id}.json"
    try:
        output = protocol.parse_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        ctx.note(f"no output at {path}")
        raise core.Fail("brief-violation no-output")
    except ValueError:
        raise core.Fail("brief-violation json")
    problems = protocol.check(role, "output", output)
    status = output.get("status") if isinstance(output.get("status"), str) else ""
    if not ctx.args:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        ctx.note(f"message ({len(raw.strip())} characters):\n{raw}")
        if len(raw.strip()) > protocol.MESSAGE_MAX:
            problems.append(f"length={len(raw.strip())}")
        try:
            message = protocol.parse_json(raw)
            problems += [p.replace("$", "message", 1) for p in protocol.validate(
                {"$ref": "common.json#/$defs/message"}, message, "common.json")]
            if message.get("status") != status:
                problems.append("status-mismatch")
        except ValueError:
            problems.append("message: json")
    if problems:
        raise core.Fail("brief-violation " + "; ".join(problems))
    return status


if __name__ == "__main__":
    core.main("report_check", step)
