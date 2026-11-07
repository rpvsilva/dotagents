#!/usr/bin/env python3
"""Checks a subagent's final message, read on stdin: the 4-line block, at most 400 characters, and its full report.

Usage: python3 -I report_check.py <run> <id> < message
       python3 -I report_check.py <run> <id> --from-report

`--from-report` checks the block ending `<run>/reports/<id>.md` instead (a remote subagent's stdout): from its last
`status:` line to the end, blank lines and one closing code fence after it allowed; a report with no `status:` line is
a `shape` violation.

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


def last_block(text):
    """The report's last block: from its last `status:` line on, without the closing fence and blank lines after it."""
    lines = text.rstrip().splitlines()
    if lines and lines[-1].strip() == "```":
        lines.pop()
    starts = [i for i, line in enumerate(lines) if line.startswith("status:")]
    return "\n".join(lines[starts[-1]:]) if starts else None


def step(ctx):
    if ctx.args not in ([], ["--from-report"]):
        raise core.Fail("usage: report_check.py <run> <id> [--from-report]")
    report = ctx.run_dir / "reports" / f"{ctx.id}.md"
    if ctx.args:
        message = last_block(report.read_text(errors="replace")) if report.is_file() else None
        if message is None:
            ctx.note(f"no block at the end of {report}")
            raise core.Fail("brief-violation shape" + ("" if report.is_file() and report.stat().st_size else " no-report"))
    else:
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
    if not report.is_file() or report.stat().st_size == 0:
        ctx.note(f"no report at {report}")
        problems.append("no-report")
    if problems:
        raise core.Fail("brief-violation " + " ".join(problems))
    return status


if __name__ == "__main__":
    core.main("report_check", step)
