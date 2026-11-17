#!/usr/bin/env python3
"""Checks one exchange of the agent protocol against its role's schema (protocol.py).

Usage: python3 -I check.py <role> output <file> [--message <file>|-]
       python3 -I check.py <role> input <prompt file>

`output`: the agent's output file must hold one JSON object matching the role's output schema. With `--message`, the
agent's final message (a file, or `-` for stdin) must be at most 400 characters and the object
`{"status": <the output's status>, "output": <a path>}`; one code fence around a JSON object is allowed in both.
`input`: the prompt's first line names the agent, and the rest is the role's input object.

Prints `OK <role> <status>` (`OK <role> input` for a prompt), or `FAIL <role> brief-violation <problems>`, the
problems separated by `; ` (`no-output`, `json`, `length=<n>`, `status-mismatch`, or `<json path>: <problem>`), and
exits 1. An unknown role prints `FAIL <role> unknown-role` and exits 2.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import protocol  # noqa: E402


def check_output(role, path, message_arg):
    problems, status = [], ""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ["no-output"], status
    try:
        output = protocol.parse_json(text)
    except ValueError:
        return ["json"], status
    problems += protocol.check(role, "output", output)
    status = output.get("status", "") if isinstance(output.get("status"), str) else ""
    if message_arg is not None:
        raw = sys.stdin.read() if message_arg == "-" else Path(message_arg).read_text(encoding="utf-8", errors="replace")
        if len(raw.strip()) > protocol.MESSAGE_MAX:
            problems.append(f"length={len(raw.strip())}")
        try:
            message = protocol.parse_json(raw)
        except ValueError:
            problems.append("message: json")
        else:
            problems += [p.replace("$", "message", 1) for p in protocol.validate(
                {"$ref": "common.json#/$defs/message"}, message, "common.json")]
            if message.get("status") != status:
                problems.append("status-mismatch")
    return problems, status


def main(argv):
    if len(argv) < 3 or argv[1] not in ("input", "output"):
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    role, part, path, rest = argv[0], argv[1], argv[2], argv[3:]
    try:
        protocol.schema(role)
    except KeyError:
        print(f"FAIL {role} unknown-role")
        return 2
    if part == "input":
        try:
            problems = protocol.check(role, "input", protocol.prompt_inputs(Path(path).read_text(encoding="utf-8")))
        except (OSError, ValueError):
            problems = ["json"]
        status = "input"
    else:
        message = rest[1] if rest[:1] == ["--message"] and len(rest) == 2 else None
        if rest and message is None:
            print("usage: check.py <role> output <file> [--message <file>|-]", file=sys.stderr)
            return 2
        problems, status = check_output(role, path, message)
    if problems:
        print(f"FAIL {role} brief-violation {'; '.join(problems)}")
        return 1
    print(f"OK {role} {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
