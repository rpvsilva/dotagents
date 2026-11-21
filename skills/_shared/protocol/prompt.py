#!/usr/bin/env python3
"""Writes an agent's prompt: the line naming its agent and brief, then its role's input object as JSON (protocol.py).

Usage: python3 -I prompt.py <role> <key>=<text>... [<key>:=<json>...] [--out <file>] [--skills-root <dir>]

`key=text` sets a string; `key:=json` sets any JSON value (`pr:=12`, `remote:=true`, `resolve:='["a.py"]'`). The
inputs are checked against the role's input schema before anything is written. Prints the prompt, or, with `--out`,
writes it there and prints `OK <role> prompt=<file>`. The brief is named under `--skills-root` (default: the skills folder
this script sits in). A bad input prints `FAIL <role> bad-input <problems>` and
exits 1.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import protocol  # noqa: E402


def main(argv):
    if not argv:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    role, inputs, out, root = argv[0], {}, None, None
    args = argv[1:]
    while args:
        arg = args.pop(0)
        if arg == "--out" and args:
            out = args.pop(0)
        elif arg == "--skills-root" and args:
            root = args.pop(0)
        elif ":=" in arg and arg.index(":=") < arg.find("=") + 1:
            key, _, raw = arg.partition(":=")
            try:
                inputs[key] = json.loads(raw)
            except ValueError:
                print(f"FAIL {role} bad-input {key}: not JSON")
                return 1
        elif "=" in arg:
            key, _, value = arg.partition("=")
            inputs[key] = value
        else:
            print(f"FAIL {role} bad-input {arg}: not key=value")
            return 1
    try:
        text = protocol.prompt(role, inputs, root)
    except KeyError:
        print(f"FAIL {role} unknown-role")
        return 2
    except ValueError as err:
        print(f"FAIL {role} bad-input {err}")
        return 1
    if out is None:
        sys.stdout.write(text)
    else:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text, encoding="utf-8")
        print(f"OK {role} prompt={out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
