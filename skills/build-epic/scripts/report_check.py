#!/usr/bin/env python3
"""Checks a subagent's final message, read on stdin: the 4-line block, at most 400 characters, and its full report.

Usage: python3 -I report_check.py <run> <id> < message

The block is the lines `status:`, `changed:`, `why:` and `follow-up:`, in that order and nothing else; `status:` holds
one word. Blank lines around it and one code fence wrapping it are allowed. The length counts the message without
its surrounding whitespace. The full report must be a non-empty `<run>/reports/<id>.md`.
Prints `OK <id> <status>`, or `FAIL <id> brief-violation <details> (log: <path>)`, details being any of
`length=<n>`, `lines=<n>` (not 4 lines), `shape` (wrong fields or order, or an empty status) and `no-report`.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import core  # noqa: E402

MAX_CHARS = 400
FIELDS = ("status:", "changed:", "why:", "follow-up:")


def block_lines(message):
    """The message's lines, without blank lines around them and one wrapping code fence."""
    lines = message.strip().splitlines()
    if len(lines) >= 2 and lines[0].strip().startswith("```") and lines[-1].strip() == "```":
        lines = lines[1:-1]
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
    return lines


def step(ctx):
    message = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    ctx.note(f"message ({len(message.strip())} characters):\n{message}")
    problems = []
    length = len(message.strip())
    if length > MAX_CHARS:
        problems.append(f"length={length}")
    lines = block_lines(message)
    status = ""
    if len(lines) != len(FIELDS):
        problems.append(f"lines={len(lines)}")
    elif not all(line.startswith(field) for line, field in zip(lines, FIELDS)):
        problems.append("shape")
    else:
        status = lines[0][len("status:"):].strip()
        if len(status.split()) != 1:
            problems.append("shape")
    report = ctx.run_dir / "reports" / f"{ctx.id}.md"
    if not report.is_file() or report.stat().st_size == 0:
        ctx.note(f"no report at {report}")
        problems.append("no-report")
    if problems:
        raise core.Fail("brief-violation " + " ".join(problems))
    return status


if __name__ == "__main__":
    core.main("report_check", step)
