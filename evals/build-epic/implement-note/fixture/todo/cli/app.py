"""Builds the parser from the command modules and runs the chosen one."""
import argparse
import os
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

from todo.cli.commands import COMMANDS
from todo.cli.errors import UsageError
from todo.storage import ItemNotFound, JsonStore, Store, StoreError


@dataclass
class Context:
    store: Store
    out: Callable[[str], None]
    today: date


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="todo")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, module in COMMANDS.items():
        module.register(sub.add_parser(name))
    return parser


def default_store_path() -> Path:
    """Per-user, so a `todo.json` someone else left in the current directory is never read as the user's list."""
    return Path.home() / ".todo.json"


def main(argv=None, out=print, today=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if out is print and hasattr(sys.stdout, "reconfigure"):
        # A stored character the stdout codec lacks prints as `?` instead of crashing the command.
        sys.stdout.reconfigure(errors="replace")
    ctx = Context(
        store=JsonStore(Path(os.environ.get("TODO_FILE") or default_store_path())),
        out=out,
        today=today or date.today(),
    )
    try:
        COMMANDS[args.cmd].run(args, ctx)
    except UsageError as e:
        parser.exit(2, f"todo: error: {e}\n")
    except ItemNotFound as e:
        parser.exit(1, f"todo: error: no item {e.args[0]}\n")
    except StoreError as e:
        parser.exit(1, f"todo: error: {e}\n")
    return 0
